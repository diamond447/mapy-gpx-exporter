"""Command-line interface: `mapy-gpx export ...` / `mapy-gpx batch ...`."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import track

from .client import AsyncMapyGpxClient, MapyGpxClient
from .exceptions import MapyGpxError

app = typer.Typer(help="Export GPX files from mapy.com route planner links.")
console = Console()


def _slug_from_url(url: str) -> str:
    """Return a safe, portable GPX filename for a share URL.

    Only the URL path is used for the route identifier.  Query strings and
    fragments are deliberately ignored because they are not part of the
    route's name and may contain characters that are unsafe in filenames.
    """
    path = urlsplit(url).path.rstrip("/")
    name = unquote(path.rsplit("/", 1)[-1])
    if path == "/s":
        name = ""
    if name.lower().endswith(".gpx"):
        name = name[:-4]

    # Keep the portable subset shared by common operating systems.  Replacing
    # rather than dropping characters makes distinct hostile names predictable
    # while preventing separators and control characters from escaping out_dir.
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = name.rstrip(" .")

    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"com{number}" for number in range(1, 10))
    reserved.update(f"lpt{number}" for number in range(1, 10))
    stem = name.split(".", 1)[0].casefold()
    if not name or name in {".", ".."} or stem in reserved:
        name = "route"

    return f"{name}.gpx"


def _collision_path(path: Path, reserved: set[Path]) -> Path:
    """Choose a deterministic, unused path, reserving it for this batch."""
    candidate = path
    suffix = 2
    while candidate.exists() or candidate in reserved:
        candidate = path.with_name(f"{path.stem}-{suffix}{path.suffix}")
        suffix += 1
    reserved.add(candidate)
    return candidate


def _write_exclusive(path: Path, content: bytes) -> None:
    """Create *path* and write bytes without ever overwriting an existing file."""
    created = False
    try:
        with path.open("xb") as output:
            created = True
            output.write(content)
    except OSError:
        # Remove a partial file produced by a failed write, but never remove a
        # file that existed before this operation or won a race with us.
        if created:
            try:
                path.unlink()
            except OSError:
                pass
        raise


@app.command()
def export(
    url: str = typer.Argument(..., help="A mapy.com/s/{id} share link."),
    out: Path = typer.Option(None, "--out", "-o", help="Output .gpx file path."),
) -> None:
    """Export a single route to a GPX file."""
    out = out or Path(_slug_from_url(url))
    try:
        with MapyGpxClient() as client:
            content = client.fetch_gpx(url)
    except MapyGpxError as exc:
        # Error messages can contain bracketed package extras (e.g. [frpc]);
        # disable Rich markup so installation instructions are printed verbatim.
        console.print(f"Error: {exc}", markup=False)
        raise typer.Exit(code=1) from exc

    try:
        out.write_bytes(content)
    except OSError as exc:
        console.print(f"Error: could not write {out}: {exc}", markup=False)
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Saved[/green] {out}")


@app.command()
def batch(
    links_file: Path = typer.Argument(..., help="Text file with one share link per line."),
    out_dir: Path = typer.Option(Path("./gpx"), "--out-dir", "-o"),
    concurrency: int = typer.Option(5, "--concurrency", "-c"),
) -> None:
    """Export many routes concurrently to a directory."""
    if concurrency < 1:
        console.print("[red]Error:[/red] --concurrency must be at least 1")
        raise typer.Exit(code=1)

    try:
        input_lines = links_file.read_text().splitlines()
    except OSError as exc:
        console.print(f"Error: could not read input file {links_file}: {exc}", markup=False)
        raise typer.Exit(code=1) from exc

    urls = [
        line.strip() for line in input_lines if line.strip() and not line.strip().startswith("#")
    ]
    if not urls:
        console.print("[yellow]No links found in file.[/yellow]")
        raise typer.Exit(code=1)

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        console.print(f"Error: could not create output directory {out_dir}: {exc}", markup=False)
        raise typer.Exit(code=1) from exc

    async def _run() -> list[tuple[str, bytes | Exception]]:
        async with AsyncMapyGpxClient(max_concurrent=concurrency) as client:
            return await client.fetch_many(urls)

    results = asyncio.run(_run())

    failures = 0
    reserved_paths: set[Path] = set()
    for url, result in track(results, description="Writing files..."):
        if isinstance(result, Exception):
            # Escape exception text so package extras such as [frpc] remain
            # visible while the failure label retains its styling.
            console.print(f"[red]FAILED[/red] {url}: {escape(str(result))}")
            failures += 1
            continue
        path = _collision_path(out_dir / _slug_from_url(url), reserved_paths)
        try:
            _write_exclusive(path, result)
        except OSError as exc:
            console.print(f"[red]FAILED[/red] {url}: could not write {path}: {exc}")
            failures += 1

    console.print(
        f"[green]{len(urls) - failures} succeeded[/green], "
        f"[red]{failures} failed[/red] out of {len(urls)}."
    )
    if failures:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
