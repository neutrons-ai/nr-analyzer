"""Tests for assemble-partials (combine partial segments without Mantid)."""

from __future__ import annotations

import json
from pathlib import Path

import click
import numpy as np
import pytest
from click.testing import CliRunner

from analyzer_tools.analysis import assemble


def _curve(q: np.ndarray) -> np.ndarray:
    # smooth, monotonic-ish reflectivity-like curve
    return np.exp(-q * 80.0)


def _mk_partial(directory: Path, set_id: str, part: int, run: str,
                q: np.ndarray, scale: float = 1.0) -> Path:
    r = _curve(q) * scale
    dr = 0.05 * r
    dq = 0.001 * np.ones_like(q)
    arr = np.column_stack([q, r, dr, dq])
    path = directory / f"REFL_{set_id}_{part}_{run}_partial.txt"
    with open(path, "w") as f:
        f.write("# Q R dR dQ\n")
        np.savetxt(f, arr, fmt="%.8g")
    return path


def _three_segments(directory: Path, set_id: str = "218281", *, seg2_scale: float = 1.0):
    _mk_partial(directory, set_id, 1, set_id, np.linspace(0.010, 0.030, 6))
    _mk_partial(directory, set_id, 2, str(int(set_id) + 1), np.linspace(0.025, 0.050, 6), scale=seg2_scale)
    _mk_partial(directory, set_id, 3, str(int(set_id) + 2), np.linspace(0.045, 0.080, 6))


def test_assemble_concatenates_sorted(tmp_path: Path) -> None:
    _three_segments(tmp_path)
    out = tmp_path / "combined.txt"
    summary = assemble.assemble("218281", data_dir=str(tmp_path), out_path=str(out))

    assert out.exists()
    assert summary["n_segments"] == 3
    data = np.loadtxt(out)
    assert data.shape[0] == summary["n_points"] == 18      # 6 per segment, plain concat
    assert np.all(np.diff(data[:, 0]) >= 0)                 # sorted by Q
    assert len(summary["overlap_chi2"]) == 2                # two adjacent overlaps
    assert summary["scaled"] is False


def test_assemble_scale_corrects_mis_scaled_segment(tmp_path: Path) -> None:
    # segment 2 is 2x too high; scaling should bring it back ~0.5x
    _three_segments(tmp_path, seg2_scale=2.0)
    out = tmp_path / "combined.txt"
    summary = assemble.assemble("218281", data_dir=str(tmp_path), out_path=str(out), scale=True)

    assert summary["scaled"] is True
    assert summary["scale_factors"][1] == pytest.approx(0.5, rel=0.05)


def test_assemble_missing_files_errors(tmp_path: Path) -> None:
    with pytest.raises(click.ClickException):
        assemble.assemble("999999", data_dir=str(tmp_path), out_path=str(tmp_path / "x.txt"))


def test_cli_json_and_output(tmp_path: Path) -> None:
    _three_segments(tmp_path)
    out = tmp_path / "out.txt"
    result = CliRunner().invoke(
        assemble.main,
        ["218281", "--data-dir", str(tmp_path), "--output", str(out), "--json"],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    summary = json.loads(result.output.strip().splitlines()[-1])
    assert summary["n_segments"] == 3
    assert summary["output"] == str(out)


def test_cli_result_out_manifest(tmp_path: Path) -> None:
    _three_segments(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    result = CliRunner().invoke(
        assemble.main,
        ["218281", "--data-dir", str(tmp_path), "--output", str(tmp_path / "c.txt"),
         "--result-out", str(manifest_path)],
    )
    assert result.exit_code == 0, result.output
    manifest = json.loads(manifest_path.read_text())
    assert manifest["tool"] == "assemble-partials"
    assert manifest["schema"] == "ndip-tool-result/1"
    assert manifest["artifacts"]["combined_file"] == str(tmp_path / "c.txt")
