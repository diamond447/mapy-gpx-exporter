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

    rc = next((v for k, v in pairs if k == "rc"), None)
    if not rc:
        raise ShortLinkResolutionError(
            f"Redirect target has no 'rc' route parameter: {location}"
        )

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
        raise ShortLinkResolutionError(
            f"Redirect from {short_url} had no Location header."
        )

    return parse_route_from_location(location)
