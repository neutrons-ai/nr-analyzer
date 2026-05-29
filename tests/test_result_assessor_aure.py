"""Tests for the AuRE-evaluate augmentation in result_assessor (Phase 4)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from analyzer_tools.analysis import result_assessor as ra


def test_render_aure_section_with_issues_and_suggestions() -> None:
    evaluation = {
        "verdict": "acceptable",
        "chi2": 1.23,
        "issues": ["Parameter X at upper bound"],
        "suggestions": ["Widen range to (300, 1200)"],
        "physical_plausibility": {"Cu thickness": "reasonable"},
        "summary": "Fit is acceptable but parameter X is at its bound.",
    }
    md = ra._render_aure_section(evaluation)
    assert "## LLM Evaluation (AuRE)" in md
    assert "acceptable" in md
    assert "Parameter X at upper bound" in md
    assert "Widen range" in md
    assert "Cu thickness" in md
    assert "Fit is acceptable" in md


def test_render_aure_section_error() -> None:
    md = ra._render_aure_section({"error": "boom"})
    assert "boom" in md
    assert "## LLM Evaluation (AuRE)" in md


def test_append_aure_section_creates_file(tmp_path: Path) -> None:
    report = tmp_path / "report_1.md"
    report.write_text("# Report for Set 1\n\n## Fit Result Assessment\nfoo\n")
    ra.append_aure_section_to_report(
        str(report), {"verdict": "good", "issues": [], "suggestions": ["do X"]}
    )
    content = report.read_text()
    assert "## LLM Evaluation (AuRE)" in content
    assert "do X" in content
    # Called again: should replace in place, not duplicate
    ra.append_aure_section_to_report(
        str(report), {"verdict": "poor", "issues": ["y"], "suggestions": []}
    )
    content = report.read_text()
    assert content.count("## LLM Evaluation (AuRE)") == 1
    assert "poor" in content


def test_run_aure_evaluate_no_aure(monkeypatch) -> None:
    monkeypatch.setattr(ra.shutil, "which", lambda _cmd: None)
    assert ra.run_aure_evaluate("some_dir") is None


def test_run_aure_evaluate_parses_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ra.shutil, "which", lambda _cmd: "/usr/bin/aure")
    payload = {"verdict": "acceptable", "chi2": 1.5, "issues": [], "suggestions": []}
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = json.dumps(payload)
    fake_result.stderr = ""
    monkeypatch.setattr(ra.subprocess, "run", lambda *a, **kw: fake_result)

    result = ra.run_aure_evaluate(str(tmp_path), context="desc")
    assert result == payload


def test_run_aure_evaluate_non_json(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ra.shutil, "which", lambda _cmd: "/usr/bin/aure")
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "not json"
    fake_result.stderr = ""
    monkeypatch.setattr(ra.subprocess, "run", lambda *a, **kw: fake_result)

    result = ra.run_aure_evaluate(str(tmp_path))
    assert result is not None
    assert "error" in result
