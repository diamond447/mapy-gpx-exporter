"""Data models describing a Mapy.com planner route."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RouteParams:
    """Parameters needed to re-request a route export from Mapy.com.

    These are extracted from the ``Location`` header returned when a
    shortened ``mapy.com/s/{id}`` link is resolved (HTTP 301, no JS
    execution required).
    """

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
        """Split ``rc`` back into one geometry code per waypoint.

        Mapy.com sends the waypoints as repeated ``rg`` query params when
        planning a route, but the shortened link concatenates them into a
        single ``rc`` string. We split it back evenly using ``ri`` (or
        ``rs``) as the point count, since both always have one entry per
        waypoint.
        """
        if self.rg is not None:
            return self.rg

        if not self.rc:
            return []

        num_points = len(self.ri) or len(self.rs) or 1
        if len(self.rc) % num_points != 0:
            raise ValueError(
                f"Cannot evenly split rc ({len(self.rc)} chars) into "
                f"{num_points} waypoints; Mapy.com may have changed its "
                f"encoding scheme."
            )
        chunk_size = len(self.rc) // num_points
        return [
            self.rc[i : i + chunk_size] for i in range(0, len(self.rc), chunk_size)
        ]
