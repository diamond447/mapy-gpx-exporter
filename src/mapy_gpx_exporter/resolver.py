"""Resolve shortened mapy.com/s/{id} links into route parameters.

Mapy.com serves a plain HTTP 301 redirect for short links; the full route
state (waypoint geometry, routing profile, ...) is embedded in the
``Location`` header's query string. Place links are resolved through Mapy.com's
public POI endpoint; no JavaScript execution is required.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlparse

import httpx

from .exceptions import ShortLinkResolutionError
from .models import RouteParams
from .place_resolver import async_resolve_map_place, resolve_map_place

_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
_MAPY_BASE_HOSTS = {"mapy.com", "mapy.cz"}


def _validate_mapy_url(location: str) -> None:
    parsed = urlparse(location)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    host_parts = hostname.split(".")
    is_base_host = hostname in _MAPY_BASE_HOSTS
    is_localized_host = (
        len(host_parts) == 3
        and host_parts[1] == "mapy"
        and host_parts[2] in {"com", "cz"}
        and (host_parts[0] == "www" or len(host_parts[0]) == 2 and host_parts[0].isalpha())
    )
    if parsed.scheme != "https" or not (is_base_host or is_localized_host):
        raise ShortLinkResolutionError(
            f"Only HTTPS Mapy.com/Mapy.cz URLs are supported: {location}"
        )


def _query_value(pairs: list[tuple[str, str]], key: str) -> str | None:
    return next((value for name, value in pairs if name == key), None)


def _is_map_place_url(location: str) -> bool:
    pairs = parse_qsl(urlparse(location).query, keep_blank_values=True)
    return _query_value(pairs, "source") == "base"


def _is_route_target(location: str) -> bool:
    pairs = parse_qsl(urlparse(location).query, keep_blank_values=True)
    return any(name in {"rc", "dim"} for name, _ in pairs) or _is_map_place_url(location)


def parse_map_place_url(location: str) -> RouteParams:
    """Parse a Mapy.com place URL into a locally-exported GPX waypoint.

    Place links (for example ``/en/zakladni?source=base&id=...&x=...&y=...``)
    identify the object in their query string. The viewport ``x``/``y`` values
    are deliberately ignored; the resolver fetches the object's canonical
    position from Mapy.com's public POI endpoint.
    """
    _validate_mapy_url(location)
    pairs = parse_qsl(urlparse(location).query, keep_blank_values=True)
    place_id = _query_value(pairs, "id")

    if not place_id:
        raise ShortLinkResolutionError(f"Map place URL is missing its id parameter: {location}")

    return RouteParams(
        resolution_method="local_waypoint",
        title=place_id,
        place_id=place_id,
    )


def _parse_profile_code(pairs: list[tuple[str, str]]) -> int:
    """Extract the routing profile code from the 'mrp' query parameter, if present."""
    profile_code = 132
    mrp_raw = next((v for k, v in pairs if k == "mrp"), None)
    if mrp_raw:
        try:
            profile_code = json.loads(mrp_raw).get("c", profile_code)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ShortLinkResolutionError(
                f"Could not parse 'mrp' JSON in redirect target: {mrp_raw!r}"
            ) from exc
    return profile_code


def parse_route_from_location(location: str) -> RouteParams:
    """Parse a Mapy.com planner URL (the redirect target) into RouteParams.

    Pure function, no I/O — used by both the sync and async resolvers so
    the parsing logic (and its tests) only exist once.
    """
    _validate_mapy_url(location)
    parsed = urlparse(location)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)

    rc = _query_value(pairs, "rc") or ""
    dim = _query_value(pairs, "dim")

    if dim:
        # We return a RouteParams with dim_id set.
        # This keeps `parse_route_from_location` pure.
        return RouteParams(dim_id=dim, profile_code=_parse_profile_code(pairs))

    if rc:
        rs = [v for k, v in pairs if k == "rs"]
        ri = [v for k, v in pairs if k == "ri"]
        return RouteParams(rc=rc, rs=rs, ri=ri, profile_code=_parse_profile_code(pairs))

    if _is_map_place_url(location):
        return parse_map_place_url(location)

    raise ShortLinkResolutionError(
        f"Redirect target has no 'rc', 'dim', or supported place parameters: {location}"
    )


def resolve_short_link(client: httpx.Client, short_url: str, lang: str = "en") -> RouteParams:
    """Resolve a ``https://mapy.com/s/{id}`` link into :class:`RouteParams`.

    Args:
        client: An ``httpx.Client`` (reused across calls for connection
            pooling). Must NOT have ``follow_redirects=True`` set, since we
            need to inspect the redirect ourselves rather than follow it.
        short_url: The shortened share link, e.g.
            ``"https://mapy.com/s/mukekodezu"``.

    Returns:
        Parsed :class:`RouteParams` ready to pass to
        :func:`mapy_gpx_exporter.exporter.export_gpx`.

    Raises:
        MissingOptionalDependencyError: If the link needs FRPC support and
            pyfrpc is not installed.
        ShortLinkResolutionError: If the link doesn't redirect as expected,
            or the redirect target is missing required route parameters.
    """
    _validate_mapy_url(short_url)

    # If the user pasted a long link directly, it might already contain the parameters.
    # Long links return 200 OK because they are the actual SPA HTML page.
    if _is_route_target(short_url):
        params = parse_route_from_location(short_url)
        if params.dim_id:
            from .frpc_resolver import resolve_dim_link

            return resolve_dim_link(client, short_url, dim_id=params.dim_id)
        if params.place_id:
            return resolve_map_place(client, short_url, params.place_id, lang=lang)
        return params

    try:
        response = client.get(short_url, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise ShortLinkResolutionError(f"Request to {short_url} failed: {exc}") from exc

    if response.status_code not in _REDIRECT_STATUS_CODES:
        raise ShortLinkResolutionError(
            f"Expected a redirect from {short_url}, got HTTP {response.status_code}"
        )

    location = response.headers.get("location")
    if location:
        _validate_mapy_url(location)

    # Follow redirects until we get the actual route parameters or hit a max limit
    redirects = 0
    while location and not _is_route_target(location) and redirects < 5:
        try:
            response = client.get(location, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise ShortLinkResolutionError(f"Request to {location} failed: {exc}") from exc

        if response.status_code not in _REDIRECT_STATUS_CODES:
            raise ShortLinkResolutionError(
                f"Expected a redirect while following {location}, got HTTP {response.status_code}"
            )

        location = response.headers.get("location")
        if location:
            _validate_mapy_url(location)
        redirects += 1

    if not location:
        raise ShortLinkResolutionError(
            f"Redirect from {short_url} had no Location header with route parameters."
        )

    params = parse_route_from_location(location)

    if params.dim_id:
        from .frpc_resolver import resolve_dim_link

        return resolve_dim_link(client, location, dim_id=params.dim_id)
    if params.place_id:
        return resolve_map_place(client, location, params.place_id, lang=lang)

    return params


async def async_resolve_short_link(
    client: httpx.AsyncClient, short_url: str, lang: str = "en"
) -> RouteParams:
    """Async equivalent of resolve_short_link.

    Raises:
        MissingOptionalDependencyError: If the link needs FRPC support and
            pyfrpc is not installed.
        ShortLinkResolutionError: If the link doesn't redirect as expected,
            or the redirect target is missing required route parameters.
    """
    _validate_mapy_url(short_url)

    if _is_route_target(short_url):
        params = parse_route_from_location(short_url)
        if params.dim_id:
            from .frpc_resolver import async_resolve_dim_link

            return await async_resolve_dim_link(client, short_url, dim_id=params.dim_id)
        if params.place_id:
            return await async_resolve_map_place(client, short_url, params.place_id, lang=lang)
        return params

    try:
        response = await client.get(short_url, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise ShortLinkResolutionError(f"Request to {short_url} failed: {exc}") from exc

    if response.status_code not in _REDIRECT_STATUS_CODES:
        raise ShortLinkResolutionError(
            f"Expected a redirect from {short_url}, got HTTP {response.status_code}"
        )

    location = response.headers.get("location")
    if location:
        _validate_mapy_url(location)

    redirects = 0
    while location and not _is_route_target(location) and redirects < 5:
        try:
            response = await client.get(location, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise ShortLinkResolutionError(f"Request to {location} failed: {exc}") from exc

        if response.status_code not in _REDIRECT_STATUS_CODES:
            raise ShortLinkResolutionError(
                f"Expected a redirect while following {location}, got HTTP {response.status_code}"
            )

        location = response.headers.get("location")
        if location:
            _validate_mapy_url(location)
        redirects += 1

    if not location:
        raise ShortLinkResolutionError(
            f"Redirect from {short_url} had no Location header with route parameters."
        )

    params = parse_route_from_location(location)

    if params.dim_id:
        from .frpc_resolver import async_resolve_dim_link

        return await async_resolve_dim_link(client, location, dim_id=params.dim_id)
    if params.place_id:
        return await async_resolve_map_place(client, location, params.place_id, lang=lang)

    return params
