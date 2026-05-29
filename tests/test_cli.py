"""Tests for analyzer_tools.cli console-script wrappers.

The package no longer ships a top-level ``analyzer-tools`` browser command (the
static tool registry it served was retired in favour of per-command ``--help``
and the skill docs). What remains in ``cli.py`` is the set of thin ``*_cli``
wrappers each console script in pyproject.toml points at; this guards that they
import cleanly and stay in sync with the declared entry points.
"""

import importlib

from analyzer_tools import cli

# console-script name -> cli wrapper attribute (mirrors [project.scripts])
WRAPPERS = {
    "run-fit": "run_fit_cli",
    "assess-partial": "assess_partial_cli",
    "assess-result": "result_assessor_cli",
    "create-model": "create_model_cli",
    "theta-offset": "theta_offset_cli",
    "analyzer-batch": "batch_cli",
    "simple-reduction": "simple_reduction_cli",
    "assemble-partials": "assemble_partials_cli",
    "analyze-sample": "analyze_sample_cli",
    "check-llm": "check_llm_cli",
    "plan-data": "plan_data_cli",
}


def test_cli_module_imports_cleanly():
    importlib.reload(cli)


def test_all_wrappers_present_and_callable():
    for script, attr in WRAPPERS.items():
        assert callable(getattr(cli, attr)), f"missing/uncallable wrapper for {script}: {attr}"


def test_no_registry_browser_remains():
    # The retired registry browser must not reappear.
    for gone in ("main", "print_tool_overview"):
        assert not hasattr(cli, gone), f"{gone} should have been removed with the registry"
