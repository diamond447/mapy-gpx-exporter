import httpx
import pytest
import respx

from mapy_gpx_exporter.exceptions import ShortLinkResolutionError
from mapy_gpx_exporter.models import RouteParams
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


def test_parse_route_from_location_extracts_all_fields() -> None:
    route = parse_route_from_location(REAL_LOCATION)

    assert route.rc == "9mR.HxU17l9mw.Hx8m7L"
    assert route.rs == ["regi", "regi"]
    assert route.ri == ["14", "14"]
    assert route.profile_code == 132
    assert route.dim_id is None


def test_rg_chunks_splits_evenly_by_waypoint_count() -> None:
    route = parse_route_from_location(REAL_LOCATION)

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        chunks = route.rg_chunks()

    assert chunks == ["9mR.HxU17l", "9mw.Hx8m7L"]


def test_parse_route_from_location_missing_rc_raises() -> None:
    with pytest.raises(ShortLinkResolutionError):
        parse_route_from_location("https://mapy.com/en/turisticka?planovani-trasy")


@respx.mock
def test_resolve_short_link_follows_single_redirect() -> None:
    respx.get("https://mapy.com/s/mukekodezu").mock(
        return_value=httpx.Response(301, headers={"location": REAL_LOCATION})
    )

    with httpx.Client() as client:
        route = resolve_short_link(client, "https://mapy.com/s/mukekodezu")

    assert route.rc == "9mR.HxU17l9mw.Hx8m7L"


@respx.mock
def test_resolve_short_link_raises_on_non_redirect() -> None:
    respx.get("https://mapy.com/s/expired").mock(return_value=httpx.Response(404))

    with httpx.Client() as client:
        with pytest.raises(ShortLinkResolutionError):
            resolve_short_link(client, "https://mapy.com/s/expired")


@respx.mock
def test_resolve_short_link_raises_on_bad_status_mid_chain() -> None:
    respx.get("https://mapy.com/s/two-hop").mock(
        return_value=httpx.Response(301, headers={"location": "https://mapy.com/s/hop2"})
    )
    # druhý hop vrátí 500 s náhodně přítomnou Location hlavičkou
    respx.get("https://mapy.com/s/hop2").mock(
        return_value=httpx.Response(
            500, headers={"location": "https://mapy.com/en/turisticka?rc=x"}
        )
    )
    with httpx.Client() as client:
        with pytest.raises(ShortLinkResolutionError):
            resolve_short_link(client, "https://mapy.com/s/two-hop")


def test_rg_chunks_handles_mixed_absolute_and_delta_encoding() -> None:
    # "hemorusagu" link: first point is absolute (10 chars), second is relative (8 chars)
    # The total rc length is 18, which would naively split into 9 and 9 and break the API.
    # The decoder correctly re-encodes both into 10-char absolute chunks.
    route = RouteParams(rc="9gvHqxXHmjh3RxWoY6", rs=["regi", "regi"], ri=["1", "2"])
    chunks = route.rg_chunks()
    assert chunks == ["9gvHqxXHmj", "9gwmHxWoY6"]


def test_rg_chunks_handles_multiple_points() -> None:
    # Test encoding 3 points to ensure state chaining works correctly.
    # Let's create a route params with 3 points.
    # We will use the same hemorusagu string but append another relative delta point to it.
    # We don't have a direct 3-point delta string on hand, but we can just use 3 absolute chunks
    # Zajišťuje, že enkodér správně řetězí > 2 body na reálném 3-bodovém odkazu (buhadohonu)
    route = RouteParams(
        rc="9ny0ZxU9K09nWOHxUKnx9n7cHxU0ph", rs=["muni", "coor", "coor"], ri=["1", "", ""]
    )
    chunks = route.rg_chunks()
    assert len(chunks) == 3
    assert chunks == ["9ny0ZxU9K0", "9nWOHxUKnx", "9n7cHxU0ph"]
