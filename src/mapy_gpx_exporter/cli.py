"""Command-line interface: `mapy-gpx export ...` / `mapy-gpx batch ...`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import track

from .client import AsyncMapyGpxClient, MapyGpxClient
from .exceptions import MapyGpxError

app = typer.Typer(help="Export GPX files from mapy.com route planner links.")
console = Console()


def _slug_from_url(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1] or "route"


@app.command()
def export(
    url: str = typer.Argument(..., help="A mapy.com/s/{id} share link."),
    out: Path = typer.Option(None, "--out", "-o", help="Output .gpx file path."),
) -> None:
    """Export a single route to a GPX file."""
    out = out or Path(f"{_slug_from_url(url)}.gpx")
    try:
        with MapyGpxClient() as client:
            content = client.fetch_gpx(url)
    except MapyGpxError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    out.write_bytes(content)
    console.print(f"[green]Saved[/green] {out}")


@app.command()
def batch(
    links_file: Path = typer.Argument(..., help="Text file with one share link per line."),
    out_dir: Path = typer.Option(Path("./gpx"), "--out-dir", "-o"),
    concurrency: int = typer.Option(5, "--concurrency", "-c"),
) -> None:
    """Export many routes concurrently to a directory."""
    urls = [
        line.strip()
        for line in links_file.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    if not urls:
        console.print("[yellow]No links found in file.[/yellow]")
        raise typer.Exit(code=1)

    out_dir.mkdir(parents=True, exist_ok=True)

    async def _run() -> list[tuple[str, bytes | Exception]]:
        async with AsyncMapyGpxClient(max_concurrent=concurrency) as client:
            return await client.fetch_many(urls)

    results = asyncio.run(_run())

    failures = 0
    for url, result in track(results, description="Writing files..."):
        if isinstance(result, Exception):
            console.print(f"[red]FAILED[/red] {url}: {result}")
            failures += 1
            continue
        path = out_dir / f"{_slug_from_url(url)}.gpx"
        path.write_bytes(result)

    console.print(
        f"[green]{len(urls) - failures} succeeded[/green], "
        f"[red]{failures} failed[/red] out of {len(urls)}."
    )
    if failures:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
