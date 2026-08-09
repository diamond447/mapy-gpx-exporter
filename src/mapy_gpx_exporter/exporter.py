"""Download a GPX file for an already-resolved Mapy.com route."""

from __future__ import annotations

import httpx

from .exceptions import GpxExportError
from .models import RouteParams

_EXPORT_URL = "https://mapy.com/api/tplannerexport"

# A same-origin Referer is required; the endpoint returns 404 (not 403)
# without it, so this is easy to misdiagnose as "the endpoint doesn't
# exist". No other auth (cookies, User-Agent) is required for anonymous
# public routes.
_REQUIRED_HEADERS = {"Referer": "https://mapy.com/"}


def export_gpx(client: httpx.Client, route: RouteParams, lang: str = "en") -> bytes:
    """Fetch the GPX bytes for a resolved route.

    Args:
        client: A reusable ``httpx.Client``.
        route: Route parameters, typically from
            :func:`mapy_gpx_exporter.resolver.resolve_short_link`.
        lang: Language for any textual metadata Mapy.com embeds in the GPX.

    Returns:
        Raw GPX (XML) file content as bytes.

    Raises:
        GpxExportError: On network failure, non-2xx response, or a
            response that doesn't look like GPX/XML.
    """
    params: list[tuple[str, str | int | float | bool | None]] = [
        ("export", "gpx"),
        ("lang", lang),
        ("rp_c", route.profile_code),
        *[("rg", chunk) for chunk in route.rg_chunks()],
        *[("rs", value) for value in route.rs],
        *[("ri", value) for value in route.ri],
    ]

    try:
        response = client.get(_EXPORT_URL, params=params, headers=_REQUIRED_HEADERS)
    except httpx.HTTPError as exc:
        raise GpxExportError(f"Request to {_EXPORT_URL} failed: {exc}") from exc

    if response.status_code != 200:
        raise GpxExportError(
            f"Export failed with HTTP {response.status_code}: {response.text[:200]!r}"
        )

    content_type = response.headers.get("content-type", "")
    if "xml" not in content_type and not response.content.lstrip().startswith(b"<?xml"):
        raise GpxExportError(
            f"Unexpected response content-type {content_type!r}; "
            "Mapy.com may have changed the export endpoint."
        )

    return response.content
