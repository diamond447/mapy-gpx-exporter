"""Resolve shortened mapy.com/s/{id} links that contain 'dim' (saved routes) using FRPC."""

import uuid

import httpx

from .decoder import decode_mapy_geometry, interpolate_elevation
from .exceptions import ShortLinkResolutionError
from .models import RouteParams


def resolve_dim_link(client: httpx.Client, location: str, dim_id: str) -> RouteParams:
    """Resolve a mapy.com 'dim' link (saved document) into RouteParams.

    Mapy.com saved routes store their geometry on the server and use FastRPC
    to hydrate them in the browser. We use pyfrpc to simulate this request.

    Args:
        client: An httpx.Client for pooling.
        location: The full mapy.com URL containing the dim parameter.
        dim_id: The document ID (e.g. 69039f733c8bfe32fe7ecc52).

    Raises:
        ImportError: If pyfrpc is not installed.
        ShortLinkResolutionError: If the FRPC request fails or geometry is missing.
    """
    try:
        import pyfrpc  # type: ignore[import-untyped]
    except ImportError as e:
        raise ImportError(
            "Resolving saved routes (dim links) requires the pyfrpc library. "
            "Please install the package with the frpc extra: pip install mapy-gpx-exporter[frpc]"
        ) from e

    url = "https://mapy.com/api/mapybox-ng/"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/x-frpc",
        "Content-Type": "application/x-frpc",
        "X-Correlation-Id": str(uuid.uuid4()),
        "Referer": location,
        # Some endpoints require an Origin, usually it's derived from Referer but let's be explicit
        "Origin": "https://mapy.com",
    }

    if len(dim_id) != 24:
        raise ShortLinkResolutionError(f"dim_id must be exactly 24 characters, got {len(dim_id)}")

    # Since dim_id is exactly 24 chars, \x18 is the correct length prefix for a string in FRPC
    payload = (
        b"\xca\x11\x02\x01h\x0blike.detail \x18"
        + dim_id.encode("utf-8")
        + b"P\x01\x04langX\x01 \x02en"
    )

    try:
        response = client.post(url, headers=headers, content=payload)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ShortLinkResolutionError(f"Failed to fetch mapybox-ng FRPC: {exc}") from exc

    import typing

    try:
        response_obj = pyfrpc.decode(response.content)
        data = getattr(response_obj, "result", response_obj)

        def _decode(obj: typing.Any) -> typing.Any:
            if isinstance(obj, bytes):
                return obj.decode("utf-8", errors="replace")
            elif isinstance(obj, dict):
                return {
                    k.decode("utf-8") if isinstance(k, bytes) else k: _decode(v)
                    for k, v in obj.items()
                }
            elif isinstance(obj, (list, tuple)):
                return [_decode(x) for x in obj]
            return obj

        decoded_data = _decode(data)
            
        if isinstance(decoded_data, list) and len(decoded_data) > 0:
            data = decoded_data[0]
        else:
            data = decoded_data
    except Exception as exc:
        raise ShortLinkResolutionError(f"Failed to decode Mapy.cz FRPC response: {exc}") from exc

    try:
        def find_all_keys(obj: typing.Any, key: str) -> list[typing.Any]:
            results = []
            if isinstance(obj, dict):
                if key in obj:
                    results.append(obj[key])
                for v in obj.values():
                    results.extend(find_all_keys(v, key))
            elif isinstance(obj, list):
                for item in obj:
                    results.extend(find_all_keys(item, key))
            return results

        # 1. Title
        title_candidates = find_all_keys(data, "title")
        title = title_candidates[0] if title_candidates else ""

        # 2. Elevation profile (sznAltitude)
        szn_candidates = find_all_keys(data, "sznAltitude")
        szn_altitude = []
        if szn_candidates and isinstance(szn_candidates[0], dict) and "data" in szn_candidates[0]:
            szn_altitude = szn_candidates[0]["data"]

        # 3. Route string or list
        route_candidates = find_all_keys(data, "route")
        
        route_str = ""
        route_list = []
        
        for cand in route_candidates:
            if isinstance(cand, str) and len(cand) > 100:
                route_str = cand
                break
            elif (
                isinstance(cand, list) 
                and len(cand) > 0 
                and isinstance(cand[0], dict) 
                and "geometry" in cand[0]
            ):
                route_list = cand
                break

        # 1. Look for a parent object containing BOTH 'route' (list) and 'geometry' (str).
        # This is the hallmark of a Type 1 "planned route" disguised as a dim link.
        def find_parent_geom(obj: typing.Any) -> str | None:
            if isinstance(obj, dict):
                has_route = "route" in obj and isinstance(obj["route"], list)
                has_geom = "geometry" in obj and isinstance(obj["geometry"], str)
                if has_route and has_geom and len(obj["geometry"]) > 0:
                    return str(obj["geometry"])
                for v in obj.values():
                    res = find_parent_geom(v)
                    if res:
                        return res
            elif isinstance(obj, list):
                for v in obj:
                    res = find_parent_geom(v)
                    if res:
                        return res
            return None
            
        parent_geom = find_parent_geom(data)
        
        geometry_points = []
        if parent_geom:
            geometry_points = decode_mapy_geometry(parent_geom)
        else:
            if route_str:
                # Type 3 (Activity): single route string. Requires a mark.
                mark_candidates = find_all_keys(data, "mark")
                if mark_candidates and isinstance(mark_candidates[0], dict):
                    geometry_points = decode_mapy_geometry(route_str)
            elif route_list:
                # Type 2 (Planned Route): list of waypoints.
                for wp in route_list:
                    geom = wp.get("geometry", "")
                    if geom:
                        geometry_points.extend(decode_mapy_geometry(geom))
                    
        # Extract total length for sanity checks
        length_candidates = find_all_keys(data, "totalLength")
        total_length = length_candidates[0] if length_candidates else None

        if not geometry_points:
            raise ShortLinkResolutionError(
                "Unknown route data structure: missing both explicit geometry and route nodes."
            )

        # Interpolate elevation and apply drift sanity check
        interpolated_points: list[tuple[float, float, float]] = []
        if geometry_points:
            interpolated_points = interpolate_elevation(geometry_points, szn_altitude, total_length)

    except Exception as exc:
        raise ShortLinkResolutionError(
            f"Failed to extract route geometry from FRPC: {exc}"
        ) from exc

    if not interpolated_points:
        raise ShortLinkResolutionError(
            f"No valid geometry points found in the FRPC response for dim={dim_id}."
        )

    return RouteParams(
        resolution_method="local_decode",
        title=title,
        geometry_points=interpolated_points,
        profile_code=132,  # default to cycling, GPX exporter handles it
    )
