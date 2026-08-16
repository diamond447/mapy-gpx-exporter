import httpx
import pyfrpc  # type: ignore[import-untyped]
import pytest
import respx

from mapy_gpx_exporter.exceptions import ShortLinkResolutionError
from mapy_gpx_exporter.frpc_resolver import resolve_dim_link


@respx.mock
def test_resolve_dim_link_success() -> None:
    # Mock a basic pyfrpc response
    mock_data = {
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
                        "id": 12345
                    },
                    {"geometry": "q0000q0000", "source": "coor", "id": "3.3,4.4"},
                ]
            }
        }
    }

    mock_payload = bytes(pyfrpc.encode(pyfrpc.FrpcResponse([mock_data]), version=0x0201))

    respx.post("https://mapy.com/api/mapybox-ng/").mock(
        return_value=httpx.Response(200, content=mock_payload)
    )

    with httpx.Client() as client:
        route = resolve_dim_link(
            client,
            "https://mapy.com/en/turisticka?planovani-trasy&dim=123456789012345678901234",
            "123456789012345678901234",
        )

    assert route.resolution_method == "local_decode"
    assert len(route.geometry_points) > 0
    # no longer populating rs
    # no longer populating ri
    assert route.profile_code == 132


@respx.mock
def test_resolve_dim_link_invalid_dim_length() -> None:
    with httpx.Client() as client:
        with pytest.raises(ShortLinkResolutionError, match="dim_id must be exactly 24 characters"):
            resolve_dim_link(client, "https://mapy.com/invalid", "123")


@respx.mock
def test_resolve_dim_link_http_error() -> None:
    respx.post("https://mapy.com/api/mapybox-ng/").mock(return_value=httpx.Response(500))
    with httpx.Client() as client:
        with pytest.raises(ShortLinkResolutionError, match="Failed to fetch mapybox-ng FRPC"):
            resolve_dim_link(client, "https://mapy.com/invalid", "123456789012345678901234")


@respx.mock
def test_resolve_dim_link_no_geometries() -> None:
    mock_payload = bytes(
        pyfrpc.encode(pyfrpc.FrpcResponse([{"like": {"data": {"route": []}}}]), version=0x0201)
    )
    respx.post("https://mapy.com/api/mapybox-ng/").mock(
        return_value=httpx.Response(200, content=mock_payload)
    )

    with httpx.Client() as client:
        with pytest.raises(ShortLinkResolutionError, match="Unknown route data structure"):
            resolve_dim_link(client, "https://mapy.com/s/mock", "123456789012345678901234")
