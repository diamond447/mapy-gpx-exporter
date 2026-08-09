import httpx
import pytest
import respx

from mapy_gpx_exporter.exceptions import ShortLinkResolutionError
from mapy_gpx_exporter.resolver import parse_route_from_location, resolve_short_link

# Real Location header captured from mapy.com's own redirect (see README
# for reverse-engineering notes). Query-encoded, exactly as observed.
REAL_LOCATION = (
    "https://mapy.com/en/turisticka?planovani-trasy"
    "&rc=9mR.HxU17l9mw.Hx8m7L"
    "&rs=regi&rs=regi"
    "&ri=14&ri=14"
    "&mrp=%7B%22c%22%3A132%2C%22dt%22%3A%22%22%2C%22d%22%3Atrue%7D"
    "&xc=%5B%5D"
    "&rwp=1%3B9m8fHxUae4kJPe4oi4EeCO9mixqdklmiO3XRlkLbiygdQ17Ug1qxU0hefjy1Cihsba8Z"
    "&rut=1&x=11.3817622&y=48.5563849&z=7"
)


def test_parse_route_from_location_extracts_all_fields():
    route = parse_route_from_location(REAL_LOCATION)

    assert route.rc == "9mR.HxU17l9mw.Hx8m7L"
    assert route.rs == ["regi", "regi"]
    assert route.ri == ["14", "14"]
    assert route.profile_code == 132
    assert route.rwp is not None


def test_rg_chunks_splits_evenly_by_waypoint_count():
    route = parse_route_from_location(REAL_LOCATION)
    chunks = route.rg_chunks()

    assert chunks == ["9mR.HxU17l", "9mw.Hx8m7L"]


def test_parse_route_from_location_missing_rc_raises():
    with pytest.raises(ShortLinkResolutionError):
        parse_route_from_location("https://mapy.com/en/turisticka?planovani-trasy")


@respx.mock
def test_resolve_short_link_follows_single_redirect():
    respx.get("https://mapy.com/s/mukekodezu").mock(
        return_value=httpx.Response(301, headers={"location": REAL_LOCATION})
    )

    with httpx.Client() as client:
        route = resolve_short_link(client, "https://mapy.com/s/mukekodezu")

    assert route.rc == "9mR.HxU17l9mw.Hx8m7L"


@respx.mock
def test_resolve_short_link_raises_on_non_redirect():
    respx.get("https://mapy.com/s/expired").mock(return_value=httpx.Response(404))

    with httpx.Client() as client:
        with pytest.raises(ShortLinkResolutionError):
            resolve_short_link(client, "https://mapy.com/s/expired")
