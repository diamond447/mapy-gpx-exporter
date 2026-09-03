"""Resolve Mapy.com ``source=base`` links to POI geometry and elevation data."""

from __future__ import annotations

import math
import typing
import uuid
from dataclasses import replace

import httpx

from .decoder import (
    decode_mapy_geometry,
    encode_mapy_geometry,
    haversine_distance,
    interpolate_elevation,
)
from .exceptions import MissingOptionalDependencyError, ShortLinkResolutionError
from .models import GeometryPoint, RouteParams

_POI_URL = "https://mapy.com/api/poiagg"
_ALTITUDE_URL = "https://mapy.com/api/altitude"


def _require_pyfrpc() -> typing.Any:
    try:
        import pyfrpc  # type: ignore[import-untyped]
    except ImportError as exc:
        raise MissingOptionalDependencyError from exc
    return pyfrpc


def _decode_response(content: bytes, pyfrpc: typing.Any) -> typing.Any:
    try:
        response = pyfrpc.decode(content)

        def decode(value: typing.Any) -> typing.Any:
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            if isinstance(value, dict):
                return {decode(key): decode(item) for key, item in value.items()}
            if isinstance(value, list | tuple):
                return [decode(item) for item in value]
            return value

        return decode(getattr(response, "data", response))
    except Exception as exc:  # noqa: BLE001 - FRPC is an untyped wire format.
        raise ShortLinkResolutionError(f"Failed to decode Mapy.com POI response: {exc}") from exc


def _parse_response(content: bytes, place_id: str, pyfrpc: typing.Any) -> RouteParams:
    data = _decode_response(content, pyfrpc)
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    poi = data.get("poi") if isinstance(data, dict) else None
    if not isinstance(poi, dict):
        raise ShortLinkResolutionError(
            f"Mapy.com POI response for source=base&id={place_id} has no POI data."
        )

    title = str(poi.get("title") or place_id)

    geom = poi.get("geom")
    geometry_data = geom.get("data") if isinstance(geom, dict) else None
    if geometry_data:
        if not isinstance(geometry_data, list) or not all(
            isinstance(segment, str) for segment in geometry_data
        ):
            raise ShortLinkResolutionError(
                f"Mapy.com POI response for source=base&id={place_id} has invalid geometry."
            )
        try:
            geometry_segments = [decode_mapy_geometry(segment) for segment in geometry_data]
        except Exception as exc:  # noqa: BLE001 - decoder errors vary by malformed payload.
            raise ShortLinkResolutionError(
                f"Failed to decode Mapy.com POI geometry for source=base&id={place_id}: {exc}"
            ) from exc
        geometry_segments = [
            [(lat, lon) for lat, lon in segment] for segment in geometry_segments if segment
        ]
        if not geometry_segments:
            raise ShortLinkResolutionError(
                f"Mapy.com POI response for source=base&id={place_id} has empty geometry."
            )
        return RouteParams(
            resolution_method="local_decode",
            title=title,
            geometry_segments=typing.cast(list[list[GeometryPoint]], geometry_segments),
            place_id=place_id,
        )

    mark = poi.get("mark")
    if not isinstance(mark, dict):
        raise ShortLinkResolutionError(
            f"Mapy.com POI response for source=base&id={place_id} has no position."
        )

    try:
        latitude = float(mark["lat"])
        longitude = float(mark["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ShortLinkResolutionError(
            f"Mapy.com POI response for source=base&id={place_id} has an invalid position."
        ) from exc

    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ShortLinkResolutionError(
            f"Mapy.com POI response for source=base&id={place_id} has an invalid position."
        )

    return RouteParams(
        resolution_method="local_waypoint",
        title=title,
        geometry_points=[(latitude, longitude, 0.0)],
        place_id=place_id,
    )


def _prepare_request(place_id: str, lang: str, pyfrpc: typing.Any) -> bytes:
    call = pyfrpc.FrpcCall(
        name="detail",
        args=(
            "base",
            int(place_id) if place_id.isdigit() else place_id,
            {"lang": [lang]},
        ),
    )
    return bytes(pyfrpc.encode(call, version=0x0201))


def _prepare_altitude_request(geometry_segment: list[GeometryPoint], pyfrpc: typing.Any) -> bytes:
    # The web client sends one complete path as a Mapy geometry string.
    # Absolute chunks preserve the exact coordinates without relying on the
    # relative encoding at a segment boundary.
    geometry = "".join(encode_mapy_geometry([(point[0], point[1]) for point in geometry_segment]))
    call = pyfrpc.FrpcCall(name="profile", args=(geometry, {"count": 100}))
    return bytes(pyfrpc.encode(call, version=0x0201))


def _extract_altitude_profile(
    content: bytes, pyfrpc: typing.Any
) -> tuple[list[dict[str, typing.Any]], float | None]:
    data = _decode_response(content, pyfrpc)
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    altitude = data.get("altitude") if isinstance(data, dict) else None
    if not isinstance(altitude, dict):
        return [], None
    samples = altitude.get("data")
    if not isinstance(samples, list) or len(samples) < 2:
        return [], None
    valid_samples: list[dict[str, typing.Any]] = []
    try:
        for sample in samples:
            if not isinstance(sample, dict):
                return [], None
            alt = float(sample["alt"])
            dist = float(sample["dist"])
            if not all(math.isfinite(value) for value in (alt, dist)) or dist < 0:
                return [], None
            normalized: dict[str, typing.Any] = {"alt": alt, "dist": dist}
            if "lat" in sample and "lon" in sample:
                lat = float(sample["lat"])
                lon = float(sample["lon"])
                if (
                    not all(math.isfinite(value) for value in (lat, lon))
                    or not -90 <= lat <= 90
                    or not -180 <= lon <= 180
                ):
                    return [], None
                normalized.update(lat=lat, lon=lon)
            valid_samples.append(normalized)
        total_length = altitude.get("length")
        total = float(total_length) if total_length is not None else None
        if total is not None and (not math.isfinite(total) or total <= 0):
            total = None
    except (KeyError, TypeError, ValueError):
        return [], None
    return valid_samples, total


def _enrich_altitude(
    geometry_segment: list[GeometryPoint],
    pyfrpc: typing.Any,
    content: bytes,
) -> list[GeometryPoint]:
    """Apply a validated altitude response to a POI path.

    This helper is intentionally best-effort. A POI's 2D geometry remains a
    valid export when the separate profile service is unavailable or returns
    data that cannot be validated by the distance sanity check.
    """
    fallback = list(geometry_segment)
    try:
        samples, total_length = _extract_altitude_profile(content, pyfrpc)
        points = list(geometry_segment)
        if not samples or not points:
            return fallback

        # The profile service returns the coordinates of its first and last
        # samples. Requiring them to match the requested path prevents adding
        # elevations from a stale or malformed response to this object.
        if all("lat" in sample and "lon" in sample for sample in samples):
            if (
                haversine_distance(points[0][0], points[0][1], samples[0]["lat"], samples[0]["lon"])
                > 100
                or haversine_distance(
                    points[-1][0], points[-1][1], samples[-1]["lat"], samples[-1]["lon"]
                )
                > 100
            ):
                return fallback

        elevated = interpolate_elevation(
            [(point[0], point[1]) for point in points], samples, total_length
        )
        return [(lat, lon, ele) for lat, lon, ele in elevated]
    except Exception:  # noqa: BLE001 - optional enrichment must not break 2D export.
        return fallback


def _resolve_altitude_segment(
    client: httpx.Client,
    location: str,
    geometry_segment: list[GeometryPoint],
    pyfrpc: typing.Any,
) -> list[GeometryPoint]:
    fallback = list(geometry_segment)
    if len(geometry_segment) < 2:
        return fallback
    try:
        response = client.post(
            _ALTITUDE_URL,
            headers={
                "Accept": "application/x-frpc",
                "Content-Type": "application/x-frpc",
                "Referer": location,
                "X-Correlation-Id": str(uuid.uuid4()),
            },
            content=_prepare_altitude_request(geometry_segment, pyfrpc),
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - optional enrichment must not break 2D export.
        return fallback
    return _enrich_altitude(geometry_segment, pyfrpc, response.content)


def _resolve_altitude(
    client: httpx.Client,
    location: str,
    geometry_segments: list[list[GeometryPoint]],
    pyfrpc: typing.Any,
) -> list[list[GeometryPoint]]:
    return [
        _resolve_altitude_segment(client, location, segment, pyfrpc)
        for segment in geometry_segments
    ]


async def _async_resolve_altitude_segment(
    client: httpx.AsyncClient,
    location: str,
    geometry_segment: list[GeometryPoint],
    pyfrpc: typing.Any,
) -> list[GeometryPoint]:
    fallback = list(geometry_segment)
    if len(geometry_segment) < 2:
        return fallback
    try:
        response = await client.post(
            _ALTITUDE_URL,
            headers={
                "Accept": "application/x-frpc",
                "Content-Type": "application/x-frpc",
                "Referer": location,
                "X-Correlation-Id": str(uuid.uuid4()),
            },
            content=_prepare_altitude_request(geometry_segment, pyfrpc),
        )
        response.raise_for_status()
    except Exception:  # noqa: BLE001 - optional enrichment must not break 2D export.
        return fallback
    return _enrich_altitude(geometry_segment, pyfrpc, response.content)


async def _async_resolve_altitude(
    client: httpx.AsyncClient,
    location: str,
    geometry_segments: list[list[GeometryPoint]],
    pyfrpc: typing.Any,
) -> list[list[GeometryPoint]]:
    return [
        await _async_resolve_altitude_segment(client, location, segment, pyfrpc)
        for segment in geometry_segments
    ]


def resolve_map_place(
    client: httpx.Client, location: str, place_id: str, lang: str = "en"
) -> RouteParams:
    """Fetch a place's canonical position from Mapy.com's public POI endpoint."""
    pyfrpc = _require_pyfrpc()
    try:
        response = client.post(
            _POI_URL,
            headers={
                "Accept": "application/x-frpc",
                "Content-Type": "application/x-frpc",
                "Referer": location,
                "X-Correlation-Id": str(uuid.uuid4()),
            },
            content=_prepare_request(place_id, lang, pyfrpc),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ShortLinkResolutionError(f"Failed to fetch Mapy.com POI data: {exc}") from exc
    route = _parse_response(response.content, place_id, pyfrpc)
    if route.geometry_segments:
        return replace(
            route,
            geometry_segments=_resolve_altitude(client, location, route.geometry_segments, pyfrpc),
        )
    return route


async def async_resolve_map_place(
    client: httpx.AsyncClient, location: str, place_id: str, lang: str = "en"
) -> RouteParams:
    """Async equivalent of :func:`resolve_map_place`."""
    pyfrpc = _require_pyfrpc()
    try:
        response = await client.post(
            _POI_URL,
            headers={
                "Accept": "application/x-frpc",
                "Content-Type": "application/x-frpc",
                "Referer": location,
                "X-Correlation-Id": str(uuid.uuid4()),
            },
            content=_prepare_request(place_id, lang, pyfrpc),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ShortLinkResolutionError(f"Failed to fetch Mapy.com POI data: {exc}") from exc
    route = _parse_response(response.content, place_id, pyfrpc)
    if route.geometry_segments:
        return replace(
            route,
            geometry_segments=await _async_resolve_altitude(
                client, location, route.geometry_segments, pyfrpc
            ),
        )
    return route
