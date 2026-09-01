"""Tests for command-line validation."""

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mapy_gpx_exporter.cli import app

runner = CliRunner()


@pytest.mark.parametrize("concurrency", ["0", "-1"])
def test_batch_rejects_non_positive_concurrency(tmp_path: Path, concurrency: str) -> None:
    """Invalid concurrency exits before reading the links file or creating output."""
    links_file = tmp_path / "missing-links.txt"
    out_dir = tmp_path / "output"

    result = runner.invoke(
        app,
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
        app,
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

    result = runner.invoke(app, ["batch", str(links_file), "--out-dir", str(tmp_path / "out")])

    assert result.exit_code == 1
    assert 'uv pip install "mapy-gpx-exporter[frpc]"' in result.stdout
    assert 'pip install "mapy-gpx-exporter[frpc]"' in result.stdout
    assert "0 succeeded, 1 failed out of 1" in result.stdout
    assert "Traceback" not in result.stdout
