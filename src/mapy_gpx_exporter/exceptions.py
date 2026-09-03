"""Custom exceptions for mapy_gpx_exporter."""


class MapyGpxError(Exception):
    """Base class for all library errors."""


class MissingOptionalDependencyError(MapyGpxError):
    """Raised when a feature's optional dependency is not installed."""

    MESSAGE = (
        "Saved routes and map places require the optional FRPC dependency. Install it with:\n"
        'uv pip install "mapy-gpx-exporter[frpc]"\n\n'
        "Alternatively, use pip:\n"
        'pip install "mapy-gpx-exporter[frpc]"'
    )

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class ShortLinkResolutionError(MapyGpxError):
    """Raised when a mapy.com/s/{id} link could not be resolved to route
    parameters (e.g. link expired, unexpected response shape)."""


class GpxExportError(MapyGpxError):
    """Raised when the GPX export request fails or returns an unexpected
    content type."""
