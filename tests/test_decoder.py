from pathlib import Path

from mapy_gpx_exporter.decoder import (
    decode_mapy_geometry,
    haversine_distance,
    interpolate_elevation,
)


def test_haversine_distance() -> None:
    lat1, lon1 = 48.8566, 2.3522
    lat2, lon2 = 51.5074, -0.1278
    dist = haversine_distance(lat1, lon1, lat2, lon2)
    assert 340_000 <= dist <= 345_000, f"Distance {dist} is out of bounds"
    assert haversine_distance(lat1, lon1, lat1, lon1) == 0.0


def test_decode_mapy_geometry() -> None:
    expected_points = [
        [45.61053, 7.351958],
        [45.610351, 7.35203],
        [45.610321, 7.352162],
        [45.610308, 7.352294],
        [45.610289, 7.352487],
    ]

    # We check that the first 5 points are accurately decoded.
    fixture_path = Path(__file__).parent / "fixtures" / "mapy_geometry_sample.txt"
    encoded_str = fixture_path.read_text(encoding="utf-8").strip()
    decoded = decode_mapy_geometry(encoded_str)
    assert len(decoded) > 5
    for i, (_lat, _lon) in enumerate(expected_points):
        exp_lat, exp_lon = expected_points[i]
        assert abs(decoded[i][0] - exp_lat) < 1e-4
        assert abs(decoded[i][1] - exp_lon) < 1e-4


def test_interpolate_elevation() -> None:
    points = [
        (0.0, 0.0),
        (0.0, 0.1),
        (0.0, 0.2),
    ]
    szn_altitude = [
        {"dist": 0.0, "alt": 100.0},
        {"dist": 22238.985, "alt": 200.0},
    ]
    result = interpolate_elevation(points, szn_altitude)
    assert len(result) == 3
    assert result[0] == (0.0, 0.0, 100.0)
    assert 145.0 < result[1][2] < 155.0
    assert abs(result[2][2] - 200.0) < 5.0
