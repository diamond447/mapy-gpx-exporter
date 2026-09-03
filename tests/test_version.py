from mapy_gpx_exporter import __version__


def test_public_version_matches_release_version() -> None:
    assert __version__ == "0.2.1"
