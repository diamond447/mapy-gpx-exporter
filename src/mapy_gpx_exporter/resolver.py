"""Resolve shortened mapy.com/s/{id} links into route parameters.

Mapy.com serves a plain HTTP 301 redirect for short links; the full route
state (waypoint geometry, routing profile, ...) is embedded in the
``Location`` header's query string. No JavaScript execution or additional
API calls are required.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlparse

import httpx

from .exceptions import ShortLinkResolutionError
from .models import RouteParams

_REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}


def parse_route_from_location(location: str) -> RouteParams:
    """Parse a Mapy.com planner URL (the redirect target) into RouteParams.

    Pure function, no I/O — used by both the sync and async resolvers so
    the parsing logic (and its tests) only exist once.
    """
    parsed = urlparse(location)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)

    rc = next((v for k, v in pairs if k == "rc"), "")
    dim = next((v for k, v in pairs if k == "dim"), None)

    if not rc and not dim:
        raise ShortLinkResolutionError(
            f"Redirect target has no 'rc' or 'dim' route parameter: {location}"
        )

    if dim:
        # Dim links are resolved later using an extra HTTP request,
        # but we parse the profile_code from here if available
        # (usually it isn't in dim links, but just in case)
        profile_code = 132
        mrp_raw = next((v for k, v in pairs if k == "mrp"), None)
        if mrp_raw:
            try:
                profile_code = json.loads(mrp_raw).get("c", profile_code)
            except (json.JSONDecodeError, AttributeError) as exc:
                raise ShortLinkResolutionError(
                    f"Could not parse 'mrp' JSON in redirect target: {mrp_raw!r}"
                ) from exc

        # We temporarily return a RouteParams with the dim string in `rc` just to pass it back,
        # but `resolve_short_link` will intercept this and resolve it via FRPC.
        # This keeps `parse_route_from_location` pure.
        return RouteParams(rc=dim, rwp="dim_marker", profile_code=profile_code)

    rs = [v for k, v in pairs if k == "rs"]
    ri = [v for k, v in pairs if k == "ri"]
    rwp = next((v for k, v in pairs if k == "rwp"), None)

    profile_code = 132
    mrp_raw = next((v for k, v in pairs if k == "mrp"), None)
    if mrp_raw:
        try:
            profile_code = json.loads(mrp_raw).get("c", profile_code)
        except (json.JSONDecodeError, AttributeError) as exc:
            raise ShortLinkResolutionError(
                f"Could not parse 'mrp' JSON in redirect target: {mrp_raw!r}"
            ) from exc

    return RouteParams(rc=rc, rs=rs, ri=ri, profile_code=profile_code, rwp=rwp)


def resolve_short_link(client: httpx.Client, short_url: str) -> RouteParams:
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
        ShortLinkResolutionError: If the link doesn't redirect as expected,
            or the redirect target is missing required route parameters.
    """
    # If the user pasted a long link directly, it might already contain the parameters.
    # Long links return 200 OK because they are the actual SPA HTML page.
    if "dim=" in short_url or "rc=" in short_url:
        params = parse_route_from_location(short_url)
        if params.rwp == "dim_marker":
            from .frpc_resolver import resolve_dim_link

            return resolve_dim_link(client, short_url, dim_id=params.rc)
        return params

    try:
        response = client.get(short_url, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise ShortLinkResolutionError(f"Request to {short_url} failed: {exc}") from exc

    if response.status_code not in _REDIRECT_STATUS_CODES:
        raise ShortLinkResolutionError(
            f"Expected a redirect from {short_url}, got HTTP {response.status_code}. "
            "The link may be invalid or expired."
        )

    location = response.headers.get("location")
    if not location:
        raise ShortLinkResolutionError(f"Redirect from {short_url} had no Location header.")

    params = parse_route_from_location(location)

    if params.rwp == "dim_marker":
        from .frpc_resolver import resolve_dim_link

        return resolve_dim_link(client, location, dim_id=params.rc)

    return params
