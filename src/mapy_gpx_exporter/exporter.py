"""Download a GPX file for an already-resolved Mapy.com route."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.dom import minidom

import httpx

from .exceptions import GpxExportError
from .models import RouteParams

_EXPORT_URL = "https://mapy.com/api/tplannerexport"

# A same-origin Referer is required; the endpoint returns 404 (not 403)
# without it, so this is easy to misdiagnose as "the endpoint doesn't
# exist". No other auth (cookies, User-Agent) is required for anonymous
# public routes.
_REQUIRED_HEADERS = {"Referer": "https://mapy.com/"}


def build_export_params(
    route: RouteParams,
    lang: str = "en",
) -> list[tuple[str, str | int | float | bool | None]]:
    """Build the query-string parameters for ``tplannerexport``.

    Shared by both the synchronous :func:`export_gpx` and
    :class:`~mapy_gpx_exporter.client.AsyncMapyGpxClient` so the
    parameter list is always consistent.
    """
    return [
        ("export", "gpx"),
        ("lang", lang),
        ("rp_c", route.profile_code),
        ("name", route.title or "export"),
        ("title", route.title or "export"),
        *[("rg", chunk) for chunk in route.rg_chunks()],
        *[("rs", value) for value in route.rs],
        *[("ri", value) for value in route.ri],
    ]


def build_local_gpx(route: RouteParams) -> bytes:
    """Generate GPX XML bytes from locally-decoded geometry points.

    Used for ``dim`` links whose geometry was resolved via FRPC and
    decoded locally rather than via the ``tplannerexport`` endpoint.
    """
    gpx = ET.Element(
        "gpx",
        {
            "version": "1.1",
            "creator": "mapy-gpx-exporter",
            "xmlns": "http://www.topografix.com/GPX/1/1",
        },
    )

    trk = ET.SubElement(gpx, "trk")
    if route.title:
        name = ET.SubElement(trk, "name")
        name.text = route.title

    trkseg = ET.SubElement(trk, "trkseg")

    for pt in route.geometry_points:
        lat, lon = pt[0], pt[1]
        trkpt = ET.SubElement(
            trkseg,
            "trkpt",
            {"lat": str(lat), "lon": str(lon)},
        )
        if len(pt) >= 3:
            ele = ET.SubElement(trkpt, "ele")
            ele.text = f"{pt[2]:.1f}"

    rough_string = ET.tostring(gpx, "utf-8")
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8")


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
    if route.resolution_method == "local_decode":
        return build_local_gpx(route)

    params = build_export_params(route, lang=lang)

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


async def async_export_gpx(
    client: httpx.AsyncClient, route: RouteParams, lang: str = "en"
) -> bytes:
    """Async equivalent of export_gpx — same validation logic, async client."""
    if route.resolution_method == "local_decode":
        return build_local_gpx(route)

    params = build_export_params(route, lang=lang)

    try:
        response = await client.get(_EXPORT_URL, params=params, headers=_REQUIRED_HEADERS)
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
