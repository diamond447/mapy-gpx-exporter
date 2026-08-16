"""Tests for the sync and async client classes."""

import httpx
import pyfrpc  # type: ignore[import-untyped]
import pytest
import respx

from mapy_gpx_exporter.client import AsyncMapyGpxClient, MapyGpxClient

# Real Location header captured from mapy.com (mukekodezu).
_REDIRECT_LOCATION = (
    "https://mapy.com/en/turisticka?planovani-trasy"
    "&rc=9mR.HxU17l9mw.Hx8m7L"
    "&rs=regi&rs=regi"
    "&ri=14&ri=14"
    "&mrp=%7B%22c%22%3A132%2C%22dt%22%3A%22%22%2C%22d%22%3Atrue%7D"
    "&xc=%5B%5D"
    "&rut=1&x=11.3817622&y=48.5563849&z=7"
)

# Minimal valid GPX response for mocking tplannerexport.
_MOCK_GPX = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b"<gpx><trk><trkseg>"
    b'<trkpt lat="50.0" lon="14.0"/>'
    b"</trkseg></trk></gpx>"
)

# FRPC mock data for dim link tests.
_DIM_MOCK_DATA = {
    "like": {
        "data": {
            "route": [
                {"geometry": "q0000q0000", "source": "coor", "id": "1.1,2.2"},
                {
                    "geometry": (
                        "q0000q0000q0000q0000q0000q0000q0000q0000q0000q0000"
                        "q0000q0000q0000q0000q0000q0000q0000q0000q0000q0000"
                        "q0000q0000q0000q0000q0000q0000q0000q0000q0000q0000"
                        "q0000q0000q0000q0000q0000q0000q0000q0000q0000q0000"
                    ),
                    "source": "stre",
                    "id": 12345,
                },
                {"geometry": "q0000q0000", "source": "coor", "id": "3.3,4.4"},
            ]
        }
    }
}

_DIM_REDIRECT_LOCATION = (
    "https://mapy.com/en/turisticka?planovani-trasy" "&dim=123456789012345678901234"
)


@respx.mock
def test_sync_client_fetch_gpx_rc_link() -> None:
    """Sync client resolves an rc link and fetches GPX."""
    respx.get("https://mapy.com/s/mukekodezu").mock(
        return_value=httpx.Response(301, headers={"location": _REDIRECT_LOCATION})
    )
    respx.get("https://mapy.com/api/tplannerexport").mock(
        return_value=httpx.Response(
            200,
            content=_MOCK_GPX,
            headers={"content-type": "application/xml"},
        )
    )

    with MapyGpxClient() as client:
        gpx = client.fetch_gpx("https://mapy.com/s/mukekodezu")

    assert gpx.startswith(b"<?xml")


@respx.mock
def test_sync_client_export_sends_name_and_title() -> None:
    """Verify that the sync export sends 'name' and 'title' params."""
    export_route = respx.get("https://mapy.com/api/tplannerexport").mock(
        return_value=httpx.Response(
            200,
            content=_MOCK_GPX,
            headers={"content-type": "application/xml"},
        )
    )
    respx.get("https://mapy.com/s/mukekodezu").mock(
        return_value=httpx.Response(301, headers={"location": _REDIRECT_LOCATION})
    )

    with MapyGpxClient() as client:
        client.fetch_gpx("https://mapy.com/s/mukekodezu")

    # Verify name and title are present in the query params
    request = export_route.calls.last.request
    assert "name=export" in str(request.url)
    assert "title=export" in str(request.url)


@pytest.mark.anyio
@respx.mock
async def test_async_client_fetch_gpx_rc_link() -> None:
    """Async client resolves an rc link and fetches GPX with name/title."""
    respx.get("https://mapy.com/s/mukekodezu").mock(
        return_value=httpx.Response(301, headers={"location": _REDIRECT_LOCATION})
    )
    export_route = respx.get("https://mapy.com/api/tplannerexport").mock(
        return_value=httpx.Response(
            200,
            content=_MOCK_GPX,
            headers={"content-type": "application/xml"},
        )
    )

    async with AsyncMapyGpxClient() as client:
        gpx = await client.fetch_gpx("https://mapy.com/s/mukekodezu")

    assert gpx.startswith(b"<?xml")

    # Verify name and title params — this was the Copilot-reported bug
    request = export_route.calls.last.request
    assert "name=export" in str(request.url)
    assert "title=export" in str(request.url)


@pytest.mark.anyio
@respx.mock
async def test_async_client_fetch_gpx_dim_link() -> None:
    """Async client resolves a dim link via FRPC and generates local GPX."""
    respx.get("https://mapy.com/s/dimlink").mock(
        return_value=httpx.Response(
            301,
            headers={"location": _DIM_REDIRECT_LOCATION},
        )
    )

    mock_payload = bytes(pyfrpc.encode(pyfrpc.FrpcResponse([_DIM_MOCK_DATA]), version=0x0201))
    respx.post("https://mapy.com/api/mapybox-ng/").mock(
        return_value=httpx.Response(200, content=mock_payload)
    )

    async with AsyncMapyGpxClient() as client:
        gpx = await client.fetch_gpx("https://mapy.com/s/dimlink")

    # Should produce valid XML GPX from locally decoded geometry
    assert gpx.startswith(b"<?xml")
    assert b"<trkpt" in gpx


@pytest.mark.anyio
@respx.mock
async def test_async_fetch_many_mixed_links() -> None:
    """fetch_many handles a mix of rc and dim links, isolating failures."""
    # rc link setup
    respx.get("https://mapy.com/s/rclink").mock(
        return_value=httpx.Response(301, headers={"location": _REDIRECT_LOCATION})
    )
    respx.get("https://mapy.com/api/tplannerexport").mock(
        return_value=httpx.Response(
            200,
            content=_MOCK_GPX,
            headers={"content-type": "application/xml"},
        )
    )

    # dim link setup
    respx.get("https://mapy.com/s/dimlink2").mock(
        return_value=httpx.Response(
            301,
            headers={"location": _DIM_REDIRECT_LOCATION},
        )
    )
    mock_payload = bytes(pyfrpc.encode(pyfrpc.FrpcResponse([_DIM_MOCK_DATA]), version=0x0201))
    respx.post("https://mapy.com/api/mapybox-ng/").mock(
        return_value=httpx.Response(200, content=mock_payload)
    )

    # bad link setup
    respx.get("https://mapy.com/s/badlink").mock(return_value=httpx.Response(404))

    async with AsyncMapyGpxClient() as client:
        results = await client.fetch_many(
            [
                "https://mapy.com/s/rclink",
                "https://mapy.com/s/dimlink2",
                "https://mapy.com/s/badlink",
            ]
        )

    assert len(results) == 3

    # rc link should succeed
    url_rc, result_rc = results[0]
    assert url_rc == "https://mapy.com/s/rclink"
    assert isinstance(result_rc, bytes)

    # dim link should succeed
    url_dim, result_dim = results[1]
    assert url_dim == "https://mapy.com/s/dimlink2"
    assert isinstance(result_dim, bytes)

    # bad link should return an exception, not crash the batch
    url_bad, result_bad = results[2]
    assert url_bad == "https://mapy.com/s/badlink"
    assert isinstance(result_bad, Exception)
