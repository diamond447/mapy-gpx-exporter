"""Tests for command-line validation."""

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
