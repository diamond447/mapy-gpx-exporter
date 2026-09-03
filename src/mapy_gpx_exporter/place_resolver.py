"""Resolve Mapy.com ``source=base`` links to their canonical POI position."""

from __future__ import annotations

import typing
import uuid

import httpx

from .decoder import decode_mapy_geometry
from .exceptions import MissingOptionalDependencyError, ShortLinkResolutionError
from .models import RouteParams

_POI_URL = "https://mapy.com/api/poiagg"


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
        geometry_segments = [segment for segment in geometry_segments if segment]
        if not geometry_segments:
            raise ShortLinkResolutionError(
                f"Mapy.com POI response for source=base&id={place_id} has empty geometry."
            )
        return RouteParams(
            resolution_method="local_decode",
            title=title,
            geometry_segments=geometry_segments,
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
    return _parse_response(response.content, place_id, pyfrpc)


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
    return _parse_response(response.content, place_id, pyfrpc)
