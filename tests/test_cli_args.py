"""CLI argument validation for the pipeline tools.

The tools are schema-agnostic: they take explicit arguments and emit a neutral
``ndip-tool-result/1`` manifest (no workflow-state knowledge). These tests guard
the required-argument behaviour. The heavy runtime (Mantid / LLM / refl1d) is
not exercised here; the manifest happy-paths live in ``test_result_manifest.py``.
"""

from __future__ import annotations

from click.testing import CliRunner

from analyzer_tools.analysis.plan_data import main as plan_data
from analyzer_tools.pipeline import main as analyze_sample
from analyzer_tools.reduction.reduction import main as simple_reduction


def test_simple_reduction_requires_event_file():
    result = CliRunner().invoke(simple_reduction, [])
    assert result.exit_code != 0
    assert "event-file is required" in result.output.lower()


def test_simple_reduction_has_no_state_options():
    result = CliRunner().invoke(simple_reduction, ["--help"])
    assert "--state-in" not in result.output
    assert "--state-out" not in result.output
    assert "--result-out" in result.output


def test_plan_data_requires_data_file():
    result = CliRunner().invoke(plan_data, [])
    assert result.exit_code != 0
    assert "DATA_FILE is required" in result.output


def test_plan_data_has_no_state_options():
    result = CliRunner().invoke(plan_data, ["--help"])
    assert "--state-in" not in result.output
    assert "--state-out" not in result.output
    assert "--result-out" in result.output


def test_analyze_sample_requires_config():
    result = CliRunner().invoke(analyze_sample, [])
    assert result.exit_code != 0
    assert "CONFIG is required" in result.output


def test_analyze_sample_has_no_state_options():
    result = CliRunner().invoke(analyze_sample, ["--help"])
    assert "--state-in" not in result.output
    assert "--state-out" not in result.output
    assert "--result-out" in result.output
