"""Resolve shortened mapy.com/s/{id} links that contain 'dim' (saved routes) using FRPC."""

import uuid

import httpx

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
                    k.decode("utf-8", errors="replace") if isinstance(k, bytes) else k: _decode(v)
                    for k, v in obj.items()
                }
            elif isinstance(obj, (list, tuple)):
                return [_decode(x) for x in obj]
            return obj

        data = _decode(data)
    except Exception as exc:
        raise ShortLinkResolutionError(f"Failed to decode Mapy.cz FRPC response: {exc}") from exc

    try:
        # data is usually a list with one dict inside it
        if isinstance(data, list) and len(data) > 0:
            if isinstance(data[0], dict):
                like_data = data[0].get("like", {})
            elif isinstance(data[0], list) and len(data[0]) > 0 and isinstance(data[0][0], dict):
                like_data = data[0][0].get("like", {})
            else:
                like_data = {}
        elif isinstance(data, dict):
            like_data = data.get("like", {})
        else:
            like_data = {}

        route_list = like_data.get("data", {}).get("route", [])

        rg_list = []
        rs_list = []
        ri_list = []

        for wp in route_list:
            rg_list.append(wp.get("geometry", ""))
            rs_list.append(wp.get("source", "coor"))

            wp_id = wp.get("id", "")
            # If id is a float/coord string, ri is empty. If it's an int (POI), ri is the int.
            if isinstance(wp_id, int) or (isinstance(wp_id, str) and wp_id.isdigit()):
                ri_list.append(str(wp_id))
            else:
                ri_list.append("")

    except Exception as exc:
        raise ShortLinkResolutionError(f"Failed to extract route array from FRPC: {exc}") from exc

    if not rg_list:
        raise ShortLinkResolutionError(
            f"No geometries found in the FRPC response for dim={dim_id}."
        )

    return RouteParams(
        rg=rg_list,
        rs=rs_list,
        ri=ri_list,
        profile_code=132,  # default to cycling, GPX exporter handles it
    )
