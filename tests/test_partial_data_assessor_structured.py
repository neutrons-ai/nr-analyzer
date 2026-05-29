"""Tests for the structured output of partial_data_assessor (Phase 2)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from analyzer_tools.analysis import partial_data_assessor as pda


@pytest.fixture
def sample_partial_dir() -> str:
    return os.path.abspath("tests/sample_data/partial")


@patch("matplotlib.pyplot.savefig")
def test_assess_returns_structured_metrics(mock_savefig, tmp_path: Path, sample_partial_dir: str) -> None:
    metrics = pda.assess_data_set(
        "218281",
        sample_partial_dir,
        str(tmp_path),
        llm_commentary=False,
        chi2_threshold=3.0,
    )
    assert metrics is not None
    assert metrics["set_id"] == "218281"
    assert metrics["chi2_threshold"] == 3.0
    assert len(metrics["parts"]) >= 2
    assert len(metrics["overlaps"]) >= 1
    for o in metrics["overlaps"]:
        assert set(o) >= {"parts", "q_min", "q_max", "n_points", "chi2", "classification"}
        assert o["classification"] in ("good", "acceptable", "poor")
    assert metrics["status"] in ("ok", "poor")
    # JSON sidecar written
    sidecar = tmp_path / "partial_metrics_218281.json"
    assert sidecar.exists()
    parsed = json.loads(sidecar.read_text())
    assert parsed["set_id"] == "218281"


def test_compute_metrics_classification() -> None:
    import numpy as np

    data1 = np.array([
        [0.01, 1.0, 0.1, 0.001],
        [0.02, 0.9, 0.08, 0.002],
        [0.03, 0.8, 0.06, 0.003],
    ])
    # data2 matches data1 very closely → good
    data2 = np.array([
        [0.02, 0.91, 0.08, 0.002],
        [0.03, 0.81, 0.06, 0.003],
        [0.04, 0.7, 0.05, 0.004],
    ])
    metrics = pda.compute_metrics("TEST", ["p1", "p2"], [data1, data2], chi2_threshold=3.0)
    assert metrics["overlaps"][0]["classification"] in ("good", "acceptable")
    assert metrics["status"] == "ok"


def test_compute_metrics_detects_bad_overlap() -> None:
    import numpy as np

    data1 = np.array([
        [0.02, 1.0, 0.001, 0.002],
        [0.03, 0.9, 0.001, 0.003],
    ])
    # Wildly inconsistent values with tiny errors → large chi2
    data2 = np.array([
        [0.02, 100.0, 0.001, 0.002],
        [0.03, 90.0, 0.001, 0.003],
    ])
    metrics = pda.compute_metrics("TEST", ["p1", "p2"], [data1, data2], chi2_threshold=3.0)
    assert metrics["overlaps"][0]["classification"] == "poor"
    assert metrics["status"] == "poor"


def test_llm_commentary_disabled_returns_none() -> None:
    metrics = {"set_id": "1", "parts": ["a"], "overlaps": []}
    assert pda.maybe_llm_commentary(metrics, enabled=False) is None


def test_llm_commentary_auto_detect_graceful_without_aure() -> None:
    """When AuRE isn't installed/configured, auto-detect must not raise."""
    metrics = {"set_id": "1", "parts": ["a"], "overlaps": []}
    # enabled=None → auto. Should never raise even if aure absent.
    result = pda.maybe_llm_commentary(metrics, enabled=None)
    # Either a string (if AuRE+LLM configured) or None (more common in CI).
    assert result is None or isinstance(result, str)
