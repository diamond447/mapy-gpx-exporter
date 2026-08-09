"""Custom exceptions for mapy_gpx_exporter."""


class MapyGpxError(Exception):
    """Base class for all library errors."""


class ShortLinkResolutionError(MapyGpxError):
    """Raised when a mapy.com/s/{id} link could not be resolved to route
    parameters (e.g. link expired, unexpected response shape)."""


class GpxExportError(MapyGpxError):
    """Raised when the GPX export request fails or returns an unexpected
    content type."""
