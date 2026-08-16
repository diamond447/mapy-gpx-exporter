"""Data models describing a Mapy.com planner route."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class RouteParams:
    """Parameters needed to re-request a route export from Mapy.com.

    These are extracted from the ``Location`` header returned when a
    shortened ``mapy.com/s/{id}`` link is resolved (HTTP 301, no JS
    execution required).
    """

    resolution_method: Literal["tplannerexport", "local_decode"] = "tplannerexport"
    """Which method should be used to export the GPX file."""

    title: str = ""
    """The title of the route."""

    geometry_points: list[tuple[float, float, float]] = field(default_factory=list)
    """Explicitly decoded route geometry points as (lat, lon, ele)."""

    rc: str = ""
    """Concatenated per-waypoint geometry codes (Mapy.com's own encoding,
    not standard polyline)."""

    rg: list[str] | None = None
    """Pre-chunked geometry codes (used by dim link resolution). If provided,
    this overrides the splitting of rc."""

    rs: list[str] = field(default_factory=list)
    """Per-waypoint source type, e.g. ``"regi"`` for a geocoded point."""

    ri: list[str] = field(default_factory=list)
    """Per-waypoint internal index/id. Same length as ``rs``."""

    profile_code: int = 132
    """Routing profile id (``mrp.c`` in the URL), e.g. 132 = recommended
    cycling route. Determines which activity profile the export uses."""

    rwp: str | None = None
    """Full detailed route-point encoding. Not required for GPX export,
    kept only for debugging/inspection."""

    def rg_chunks(self) -> list[str]:
        """Split ``rc`` back into one absolute geometry code per waypoint.

        Mapy.com sends the waypoints as repeated ``rg`` query params when
        planning a route, but the shortened link concatenates them into a
        single ``rc`` string which may use relative delta encodings. We decode
        the entire string into points, and then explicitly encode each point
        as an independent absolute 10-character chunk.
        """
        if self.rg is not None:
            return self.rg

        if not self.rc:
            return []

        try:
            from .decoder import decode_mapy_geometry, encode_mapy_geometry
            pts = decode_mapy_geometry(self.rc)
            return encode_mapy_geometry(pts)
        except Exception as exc:
            import warnings
            warnings.warn(f"decode/encode round-trip failed, falling back to naive split: {exc}", stacklevel=2)
            # Fallback to naive splitting if decoding fails for some reason
            num_points = len(self.ri) or len(self.rs) or 1
            chunk_size = len(self.rc) // num_points
            if chunk_size == 0:
                return [self.rc]
            return [self.rc[i : i + chunk_size] for i in range(0, len(self.rc), chunk_size)]
