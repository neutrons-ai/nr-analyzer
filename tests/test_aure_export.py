"""Tests for the AuRE-compatible JSON export.

Verifies the contract consumed by ``aure serve <dir>``: a ``run_info.json``
with run-level metadata and a ``final_state.json`` whose embedded ``state``
carries Q/R/dR plus a ``fit_results`` list with model curves, parameters,
uncertainties, bounds, and an SLD profile.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest

from analyzer_tools.analysis.aure_export import export_for_aure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_refl(directory: Path, name: str, n: int = 5) -> None:
    """Write a 5-column ``*-refl.dat`` (Q, R, dR, dQ, R_fit)."""
    q = np.linspace(0.01, 0.1, n)
    r = np.exp(-q * 30)
    dr = 0.05 * r
    dq = 0.001 * np.ones_like(q)
    r_fit = 0.99 * r
    arr = np.column_stack([q, r, dr, dq, r_fit])
    np.savetxt(directory / name, arr)


def _write_profile(directory: Path, name: str, n: int = 4) -> None:
    z = np.linspace(0, 100, n)
    rho = np.linspace(2.07, 6.5, n)
    np.savetxt(directory / name, np.column_stack([z, rho]))


def _populate_minimal_bumps_output(directory: Path) -> None:
    _write_refl(directory, "problem-1-refl.dat")
    _write_profile(directory, "problem-1-profile.dat")
    (directory / "problem.par").write_text(
        "intensity 0.95\nCu thickness 500.1\nCu interface 8.2\n"
    )
    (directory / "problem-err.json").write_text(
        json.dumps(
            {
                "intensity": {"std": 0.01, "mean": 0.95},
                "Cu thickness": {"std": 1.2, "mean": 500.1},
            }
        )
    )
    (directory / "problem-1-expt.json").write_text(
        json.dumps(
            {
                "references": {
                    "ref1": {"name": "Cu thickness", "bounds": [400.0, 600.0]},
                    "ref2": {"name": "Cu interface", "bounds": [2.0, 20.0]},
                },
                "probe": {"filename": "/data/REFL_42_combined_data_auto.txt"},
            }
        )
    )
    (directory / "problem.out").write_text(
        "[bumps]\nFinal: chisq=1.45(3), nllf=12.7\n"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAureExport:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())

    def teardown_method(self):
        shutil.rmtree(self.tmp)

    def test_writes_run_info_and_final_state(self):
        _populate_minimal_bumps_output(self.tmp)

        out = export_for_aure(
            self.tmp,
            sample_description="50 nm Cu on Si",
            hypothesis="oxide layer",
            run_id="test_run",
        )
        assert out == self.tmp
        assert (self.tmp / "run_info.json").exists()
        assert (self.tmp / "final_state.json").exists()

    def test_run_info_contract(self):
        _populate_minimal_bumps_output(self.tmp)
        export_for_aure(
            self.tmp,
            sample_description="50 nm Cu on Si",
            hypothesis="oxide layer",
            run_id="test_run",
        )

        info = json.loads((self.tmp / "run_info.json").read_text())
        assert info["run_id"] == "test_run"
        assert info["sample_description"] == "50 nm Cu on Si"
        assert info["hypothesis"] == "oxide layer"
        assert info["data_file"] == "/data/REFL_42_combined_data_auto.txt"
        assert isinstance(info["started_at"], str)
        assert info["checkpoints"] == []

    def test_final_state_contract(self):
        _populate_minimal_bumps_output(self.tmp)
        export_for_aure(self.tmp, sample_description="Cu/Si", fitter="dream")

        doc = json.loads((self.tmp / "final_state.json").read_text())
        assert doc["success"] is True
        assert doc["final_chi2"] == pytest.approx(1.45)

        state = doc["state"]
        assert state["sample_description"] == "Cu/Si"
        assert len(state["Q"]) == 5
        assert len(state["R"]) == 5
        assert len(state["dR"]) == 5

        fits = state["fit_results"]
        assert len(fits) == 1
        fr = fits[0]
        assert fr["method"] == "dream"
        assert fr["converged"] is True
        assert fr["chi_squared"] == pytest.approx(1.45)
        assert fr["parameters"]["Cu thickness"] == pytest.approx(500.1)
        assert fr["uncertainties"]["Cu thickness"] == pytest.approx(1.2)
        assert fr["bounds"]["Cu thickness"] == [400.0, 600.0]
        assert len(fr["Q_fit"]) == 5
        assert len(fr["R_fit"]) == 5
        assert len(fr["sld_z"]) == 4
        assert len(fr["sld_rho"]) == 4
        assert fr["per_file_results"] is None

    def test_multi_experiment_emits_per_file_results(self):
        _write_refl(self.tmp, "problem-1-refl.dat", n=4)
        _write_refl(self.tmp, "problem-2-refl.dat", n=4)
        _write_profile(self.tmp, "problem-1-profile.dat")

        export_for_aure(self.tmp, sample_description="co-refine")

        state = json.loads((self.tmp / "final_state.json").read_text())["state"]
        assert len(state["data_files"]) == 2
        labels = [df["label"] for df in state["data_files"]]
        assert labels == ["experiment-1", "experiment-2"]

        per_file = state["fit_results"][0]["per_file_results"]
        assert per_file is not None
        assert len(per_file) == 2
        assert per_file[0]["chi_squared"] is not None

    def test_missing_refl_dat_returns_none(self):
        # No bumps output at all → nothing to export.
        assert export_for_aure(self.tmp) is None
        assert not (self.tmp / "run_info.json").exists()

    def test_separate_output_dir(self):
        _populate_minimal_bumps_output(self.tmp)
        dest = self.tmp / "aure"
        out = export_for_aure(self.tmp, output_dir=dest)
        assert out == dest
        assert (dest / "run_info.json").exists()
        assert (dest / "final_state.json").exists()
        # Bumps files stay in the source dir.
        assert (self.tmp / "problem.par").exists()
