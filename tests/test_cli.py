"""Tests for command-line validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mapy_gpx_exporter import cli
from mapy_gpx_exporter.exceptions import GpxExportError

runner = CliRunner()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://mapy.com/s/route?lang=en#details", "route.gpx"),
        ("https://mapy.com/s/unsafe%2Fname%00", "unsafe_name_.gpx"),
        ("https://mapy.com/s/name:with*bad|chars...", "name_with_bad_chars.gpx"),
        ("https://mapy.com/s/CON.txt", "route.gpx"),
        ("https://mapy.com/s/", "route.gpx"),
    ],
)
def test_slug_from_url_is_safe_and_path_based(url: str, expected: str) -> None:
    assert cli._slug_from_url(url) == expected


class StubMapyGpxClient:
    """Offline sync client double for the single-export command tests."""

    def __init__(self, result: bytes | Exception) -> None:
        self.result = result

    def __enter__(self) -> StubMapyGpxClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        pass

    def fetch_gpx(self, url: str) -> bytes:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class StubAsyncMapyGpxClient:
    """Offline async client double for the batch command tests."""

    def __init__(self, results: list[tuple[str, bytes | Exception]], max_concurrent: int) -> None:
        self.results = results
        self.max_concurrent = max_concurrent

    async def __aenter__(self) -> StubAsyncMapyGpxClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        pass

    async def fetch_many(self, urls: list[str]) -> list[tuple[str, bytes | Exception]]:
        assert [url for url, _ in self.results] == urls
        return self.results


def test_export_writes_gpx_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "route.gpx"
    gpx = b"<gpx>route</gpx>"
    monkeypatch.setattr(cli, "MapyGpxClient", lambda: StubMapyGpxClient(gpx))

    result = runner.invoke(
        cli.app,
        ["export", "https://mapy.com/s/route", "--out", str(output)],
    )

    assert result.exit_code == 0
    assert output.read_bytes() == gpx
    assert f"Saved {output}" in result.stdout


def test_export_reports_failure_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "route.gpx"
    monkeypatch.setattr(
        cli,
        "MapyGpxClient",
        lambda: StubMapyGpxClient(GpxExportError("route export failed")),
    )

    result = runner.invoke(
        cli.app,
        ["export", "https://mapy.com/s/route", "--out", str(output)],
    )

    assert result.exit_code == 1
    assert "Error: route export failed" in result.stdout
    assert not output.exists()


def test_batch_reports_empty_input_file(tmp_path: Path) -> None:
    links_file = tmp_path / "links.txt"
    links_file.write_text("# route list\n\n")

    result = runner.invoke(cli.app, ["batch", str(links_file)])

    assert result.exit_code == 1
    assert "No links found in file." in result.stdout


def test_batch_writes_all_successful_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = ["https://mapy.com/s/first", "https://mapy.com/s/second"]
    links_file = tmp_path / "links.txt"
    links_file.write_text("\n".join(urls) + "\n")
    out_dir = tmp_path / "gpx"
    responses = list(zip(urls, [b"first gpx", b"second gpx"], strict=True))
    monkeypatch.setattr(
        cli,
        "AsyncMapyGpxClient",
        lambda max_concurrent: StubAsyncMapyGpxClient(responses, max_concurrent),
    )

    result = runner.invoke(
        cli.app,
        ["batch", str(links_file), "--out-dir", str(out_dir), "--concurrency", "2"],
    )

    assert result.exit_code == 0
    assert (out_dir / "first.gpx").read_bytes() == b"first gpx"
    assert (out_dir / "second.gpx").read_bytes() == b"second gpx"
    assert "2 succeeded, 0 failed out of 2." in result.stdout


def test_batch_writes_successes_and_reports_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = ["https://mapy.com/s/good", "https://mapy.com/s/bad"]
    links_file = tmp_path / "links.txt"
    links_file.write_text("\n".join(urls) + "\n")
    out_dir = tmp_path / "gpx"
    responses = [(urls[0], b"good gpx"), (urls[1], GpxExportError("bad route"))]
    monkeypatch.setattr(
        cli,
        "AsyncMapyGpxClient",
        lambda max_concurrent: StubAsyncMapyGpxClient(responses, max_concurrent),
    )

    result = runner.invoke(cli.app, ["batch", str(links_file), "--out-dir", str(out_dir)])

    assert result.exit_code == 1
    assert (out_dir / "good.gpx").read_bytes() == b"good gpx"
    assert not (out_dir / "bad.gpx").exists()
    assert "FAILED" in result.stdout
    assert "bad route" in result.stdout
    assert "1 succeeded, 1 failed out of 2." in result.stdout


def test_batch_ignores_indented_comments_and_uses_deterministic_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_url = "https://mapy.com/s/route?variant=first"
    second_url = "https://mapy.com/s/route#variant=second"
    links_file = tmp_path / "links.txt"
    links_file.write_text(f"  # ignored\n{first_url}\n\t# also ignored\n{second_url}\n")
    out_dir = tmp_path / "gpx"
    out_dir.mkdir()
    (out_dir / "route.gpx").write_bytes(b"pre-existing")
    responses = [(first_url, b"first"), (second_url, b"second")]
    monkeypatch.setattr(
        cli,
        "AsyncMapyGpxClient",
        lambda max_concurrent: StubAsyncMapyGpxClient(responses, max_concurrent),
    )

    result = runner.invoke(cli.app, ["batch", str(links_file), "--out-dir", str(out_dir)])

    assert result.exit_code == 0
    assert (out_dir / "route.gpx").read_bytes() == b"pre-existing"
    assert (out_dir / "route-2.gpx").read_bytes() == b"first"
    assert (out_dir / "route-3.gpx").read_bytes() == b"second"
    assert "2 succeeded, 0 failed out of 2." in result.stdout


def test_batch_reports_input_read_error_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    links_file = tmp_path / "links.txt"

    def fail_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == links_file:
            raise OSError("permission denied")
        return Path.read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_read_text)
    result = runner.invoke(cli.app, ["batch", str(links_file)])

    assert result.exit_code == 1
    assert "Error: could not read input file" in result.stdout
    assert str(links_file) in result.stdout
    assert "permission denied" in result.stdout
    assert "Traceback" not in result.stdout


def test_batch_reports_output_directory_error_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    links_file = tmp_path / "links.txt"
    links_file.write_text("https://mapy.com/s/route\n")
    out_dir = tmp_path / "gpx"
    original_mkdir = Path.mkdir

    def fail_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        if self == out_dir:
            raise OSError("read-only filesystem")
        original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    result = runner.invoke(cli.app, ["batch", str(links_file), "--out-dir", str(out_dir)])

    assert result.exit_code == 1
    assert "Error: could not create output directory" in result.stdout
    assert str(out_dir) in result.stdout
    assert "read-only" in result.stdout
    assert "filesystem" in result.stdout
    assert "Traceback" not in result.stdout


def test_batch_continues_after_file_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    urls = ["https://mapy.com/s/fails", "https://mapy.com/s/succeeds"]
    links_file = tmp_path / "links.txt"
    links_file.write_text("\n".join(urls) + "\n")
    out_dir = tmp_path / "gpx"
    responses = [(urls[0], b"not saved"), (urls[1], b"saved")]
    monkeypatch.setattr(
        cli,
        "AsyncMapyGpxClient",
        lambda max_concurrent: StubAsyncMapyGpxClient(responses, max_concurrent),
    )
    original_write = cli._write_exclusive

    def fail_one(path: Path, content: bytes) -> None:
        if path.name == "fails.gpx":
            raise OSError("disk full")
        original_write(path, content)

    monkeypatch.setattr(cli, "_write_exclusive", fail_one)

    result = runner.invoke(cli.app, ["batch", str(links_file), "--out-dir", str(out_dir)])

    assert result.exit_code == 1
    assert not (out_dir / "fails.gpx").exists()
    assert (out_dir / "succeeds.gpx").read_bytes() == b"saved"
    assert "could not write" in result.stdout
    assert "1 succeeded, 1 failed out of 2." in result.stdout
    assert "Traceback" not in result.stdout


@pytest.mark.parametrize("concurrency", ["0", "-1"])
def test_batch_rejects_non_positive_concurrency(tmp_path: Path, concurrency: str) -> None:
    """Invalid concurrency exits before reading the links file or creating output."""
    links_file = tmp_path / "missing-links.txt"
    out_dir = tmp_path / "output"

    result = runner.invoke(
        cli.app,
        ["batch", str(links_file), "--out-dir", str(out_dir), "--concurrency", concurrency],
    )

    assert result.exit_code == 1
    assert "--concurrency must be at least 1" in result.stdout
    assert not out_dir.exists()


def test_export_dim_url_reports_missing_pyfrpc_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pyfrpc", None)

    result = runner.invoke(
        cli.app,
        [
            "export",
            "https://mapy.com/en/turisticka?planovani-trasy&dim=123456789012345678901234",
        ],
    )

    assert result.exit_code == 1
    assert "Saved routes require the optional FRPC dependency" in result.stdout
    assert 'uv pip install "mapy-gpx-exporter[frpc]"' in result.stdout
    assert 'pip install "mapy-gpx-exporter[frpc]"' in result.stdout
    assert "Traceback" not in result.stdout


def test_batch_dim_url_reports_missing_pyfrpc_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "pyfrpc", None)
    links_file = tmp_path / "links.txt"
    links_file.write_text(
        "https://mapy.com/en/turisticka?planovani-trasy&dim=123456789012345678901234\n"
    )

    result = runner.invoke(cli.app, ["batch", str(links_file), "--out-dir", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert 'uv pip install "mapy-gpx-exporter[frpc]"' in result.stdout
    assert 'pip install "mapy-gpx-exporter[frpc]"' in result.stdout
    assert "0 succeeded, 1 failed out of 1" in result.stdout
    assert "Traceback" not in result.stdout
