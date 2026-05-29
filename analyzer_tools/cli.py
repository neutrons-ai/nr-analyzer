#!/usr/bin/env python3
"""
Command-line interfaces for analyzer tools.

Each console script in ``pyproject.toml`` maps to one of the thin ``*_cli``
wrappers below, which lazy-import and invoke the corresponding tool's ``main()``.
Tool discovery is via each command's ``--help`` and the skill docs under
``analyzer_tools/skills/``.
"""

import sys
import os

# Add the project root to the path for backward compatibility
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# ============================================================================
# CLI entry point wrappers
# ============================================================================


def run_fit_cli():
    """Command-line interface for run_fit."""
    from .analysis.run_fit import main
    main()


def assess_partial_cli():
    """Command-line interface for partial data assessor."""
    from .analysis.partial_data_assessor import main
    main()


def create_model_cli():
    """Command-line interface for create-model (Mode A JSON / Mode B LLM)."""
    from .analysis.create_model import main
    main()


def result_assessor_cli():
    """Command-line interface for result assessor."""
    from .analysis.result_assessor import main
    main()


def theta_offset_cli():
    """Command-line interface for theta offset calculator."""
    from .analysis.theta_offset import main
    main()


def batch_cli():
    """Command-line interface for manifest batch runner."""
    from .batch import main
    main()


def simple_reduction_cli():
    """Command-line interface for Mantid simple reduction."""
    from .reduction.reduction import main
    main()


def analyze_sample_cli():
    """Command-line interface for the sample pipeline orchestrator."""
    from .pipeline import main
    main()


def check_llm_cli():
    """Command-line interface for the LLM health check."""
    from .analysis.check_llm import main
    main()


def plan_data_cli():
    """Command-line interface for the data planner."""
    from .analysis.plan_data import main
    main()
