# mapy-gpx-exporter
[![CI](https://github.com/diamond447/mapy-gpx-exporter/actions/workflows/ci.yml/badge.svg)](https://github.com/diamond447/mapy-gpx-exporter/actions/workflows/ci.yml)

Export GPX files from [Mapy.com](https://mapy.com) route and place links — one
link or a whole batch, from the command line or as a Python library.

## Why

Mapy.com's web UI lets you export a planned route as GPX, but there's no
public API for it and no way to batch-export a list of saved routes. This
library reverse-engineers the two requests the "Export → GPX" button makes
and wraps them in a clean, typed, tested client.

## How it works

1. **Map Places**: Links such as `https://mapy.com/en/zakladni?source=base&id=...` are resolved
   through Mapy.com's public POI endpoint. The endpoint supplies the place's canonical position
   (the `x`/`y` query parameters are only viewport coordinates). Object line geometry is decoded
   into a local GPX track with its segments preserved. When the separate Mapy elevation profile
   endpoint returns a validated profile, elevations are interpolated onto the track points;
   otherwise the track remains 2D. Objects without line geometry become a GPX waypoint.
2. **Anonymous Routes**: `GET https://mapy.com/s/{id}` — Mapy.com replies with a plain **HTTP
   301** redirect; the full route state (waypoint geometry, routing
   profile) is embedded in the `Location` header's query string. We decode the proprietary `rc` parameter string and re-encode it as absolute chunks to `GET https://mapy.com/api/tplannerexport`, which returns the GPX file.
3. **Saved Routes (dim links)**: If the link is a saved route, the geometry is stored server-side. We use `pyfrpc` to simulate Mapy.cz's FastRPC routing API (`https://mapy.com/api/mapybox-ng/`), locally decode the proprietary 5-bit delta-encoded geometry, interpolate elevations, and manually construct the GPX file locally.

This is an unofficial client built against publicly observable network
behavior, not a documented or officially supported API. It does not
bypass any authentication, paywall, or rate limiting; it only automates
the same request an anonymous browser session makes when you click
"Export → Save". Endpoint behavior may change without notice — see
[Limitations](#limitations).

## Install

Anonymous route links (`rc` links) only need the base package:

```bash
uv pip install mapy-gpx-exporter
```

Saved routes (dim links) require the optional FRPC support:

```bash
uv pip install "mapy-gpx-exporter[frpc]"
```

Map places also require the optional FRPC support because Mapy.com's POI endpoint uses FastRPC.

## CLI usage

```bash
# single route
mapy-gpx export https://mapy.com/s/mukekodezu -o route.gpx

# single map place (decodes its object geometry locally)
mapy-gpx export 'https://mapy.com/en/zakladni?source=base&id=2139764&x=15.6340364&y=49.5820419&z=9' -o place.gpx

# batch: one link per line in links.txt
mapy-gpx batch links.txt --out-dir ./gpx --concurrency 5
```

Example output:

```console
$ mapy-gpx export https://mapy.com/s/mukekodezu -o route.gpx
Saved route.gpx
```

Batch filenames are derived safely from each URL's path; query strings and
fragments are ignored. If a filename collides with another route or an
existing file, a deterministic suffix is added (`name-2.gpx`, `name-3.gpx`,
and so on), so existing output is never overwritten.

## Library usage

```python
from mapy_gpx_exporter import MapyGpxClient

with MapyGpxClient() as client:
    gpx_bytes = client.fetch_gpx("https://mapy.com/s/mukekodezu")

with open("route.gpx", "wb") as f:
    f.write(gpx_bytes)
```

Batch export, async:

```python
import asyncio
from mapy_gpx_exporter import AsyncMapyGpxClient


async def main():
    urls = ["https://mapy.com/s/mukekodezu", "https://mapy.com/s/another"]
    async with AsyncMapyGpxClient(max_concurrent=5) as client:
        results = await client.fetch_many(urls)
    for url, result in results:
        if isinstance(result, Exception):
            print(f"failed: {url}: {result}")
        else:
            print(f"ok: {url} ({len(result)} bytes)")


asyncio.run(main())
```

## Limitations

- Only tested against the "planned route" (`turisticka`/planner) and activity traces.
- `source=base` object links are resolved through Mapy.com's public POI and elevation-profile
  endpoints. Their line geometry is exported as a track with elevations when the profile is
  available and validated; objects without line geometry are exported as waypoints.
- Route and object links require the optional FRPC support when Mapy.com returns FastRPC data.
- No authentication support — routes that require a logged-in session and are strictly private won't export.
- This relies on an undocumented, unofficial endpoint and reverse-engineered formats. Mapy.com can
  change it at any time without notice.

## Development

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy src
uv run pre-commit install
```

## License

MIT
