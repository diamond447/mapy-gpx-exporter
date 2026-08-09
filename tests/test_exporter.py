import httpx
import pytest
import respx

from mapy_gpx_exporter.exceptions import GpxExportError
from mapy_gpx_exporter.exporter import export_gpx
from mapy_gpx_exporter.models import RouteParams

SAMPLE_GPX = b'<?xml version="1.0"?><gpx version="1.1"></gpx>'

ROUTE = RouteParams(
    rc="9mR.HxU17l9mw.Hx8m7L",
    rs=["regi", "regi"],
    ri=["14", "14"],
    profile_code=132,
)


@respx.mock
def test_export_gpx_sends_referer_and_returns_content():
    route_call = respx.get("https://mapy.com/api/tplannerexport").mock(
        return_value=httpx.Response(
            200, content=SAMPLE_GPX, headers={"content-type": "application/xml"}
        )
    )

    with httpx.Client() as client:
        content = export_gpx(client, ROUTE)

    assert content == SAMPLE_GPX
    sent_request = route_call.calls.last.request
    assert sent_request.headers["referer"] == "https://mapy.com/"
    assert "rg=9mR.HxU17l" in str(sent_request.url)
    assert "rg=9mw.Hx8m7L" in str(sent_request.url)


@respx.mock
def test_export_gpx_raises_on_non_200():
    respx.get("https://mapy.com/api/tplannerexport").mock(return_value=httpx.Response(404))

    with httpx.Client() as client:
        with pytest.raises(GpxExportError):
            export_gpx(client, ROUTE)


@respx.mock
def test_export_gpx_raises_on_unexpected_content_type():
    respx.get("https://mapy.com/api/tplannerexport").mock(
        return_value=httpx.Response(
            200, content=b"<html>not gpx</html>", headers={"content-type": "text/html"}
        )
    )

    with httpx.Client() as client:
        with pytest.raises(GpxExportError):
            export_gpx(client, ROUTE)
