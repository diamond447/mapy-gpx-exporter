"""High-level sync + async client combining resolve + export steps."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from .exporter import (
    async_export_gpx,
    export_gpx,
)
from .models import RouteParams
from .resolver import (
    async_resolve_short_link,
    resolve_short_link,
)

_DEFAULT_HEADERS = {
    "User-Agent": ("mapy-gpx-exporter/0.1 (+https://github.com/diamond447/mapy-gpx-exporter)"),
}


class MapyGpxClient:
    """Synchronous client for exporting GPX files from mapy.com share links."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._client = httpx.Client(headers=_DEFAULT_HEADERS, timeout=timeout)

    def __enter__(self) -> MapyGpxClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def resolve(self, short_url: str) -> RouteParams:
        return resolve_short_link(self._client, short_url)

    def export(self, route: RouteParams, lang: str = "en") -> bytes:
        return export_gpx(self._client, route, lang=lang)

    def fetch_gpx(self, short_url: str, lang: str = "en") -> bytes:
        """Resolve a share link and download its GPX in one call."""
        route = self.resolve(short_url)
        return self.export(route, lang=lang)


class AsyncMapyGpxClient:
    """Async client, useful for batch-exporting many links concurrently."""

    def __init__(self, timeout: float = 10.0, max_concurrent: int = 5) -> None:
        self._client = httpx.AsyncClient(headers=_DEFAULT_HEADERS, timeout=timeout)
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def __aenter__(self) -> AsyncMapyGpxClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch_gpx(self, short_url: str, lang: str = "en") -> bytes:
        """Resolve a share link and download its GPX in one call."""
        async with self._semaphore:
            route = await async_resolve_short_link(self._client, short_url)
            return await async_export_gpx(self._client, route, lang=lang)

    async def fetch_many(self, short_urls: list[str]) -> list[tuple[str, bytes | Exception]]:
        """Fetch GPX for many links concurrently (bounded by max_concurrent).

        Returns a list of (url, result) pairs where result is either the
        GPX bytes or the Exception raised for that particular URL — so one
        bad link doesn't abort the whole batch.
        """

        async def _one(url: str) -> tuple[str, bytes | Exception]:
            try:
                return url, await self.fetch_gpx(url)
            except Exception as exc:  # noqa: BLE001 - intentionally broad for batch results
                return url, exc

        return await asyncio.gather(*(_one(url) for url in short_urls))


def save_gpx(content: bytes, path: str | Path) -> None:
    Path(path).write_bytes(content)
