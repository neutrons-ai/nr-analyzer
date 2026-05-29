#!/usr/bin/env python3
"""
Command-line interfaces for analyzer tools.
"""

import sys
import os

import click

# Add the project root to the path for backward compatibility
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# ============================================================================
# Presentation helpers (moved from registry / welcome)
# ============================================================================


def print_tool_overview():
    """Print a comprehensive overview of all available tools."""
    from .registry import TOOLS, WORKFLOWS
    from .config_utils import get_data_organization_info

    try:
        data_org = get_data_organization_info()
    except Exception:
        data_org = {
            "combined_data_dir": "data/combined",
            "partial_data_dir": "data/partial",
            "reports_dir": "reports",
            "combined_data_template": "REFL_{set_id}_combined_data_auto.txt",
            "models_dir": "models",
        }

    print("=" * 70)
    print("NEUTRON REFLECTOMETRY DATA ANALYSIS TOOLS")
    print("=" * 70)
    print()

    print("\U0001f4ca AVAILABLE ANALYSIS TOOLS:")
    print("-" * 40)

    for _name, tool in TOOLS.items():
        print(f"\n\U0001f527 {tool.name}")
        print(f"   {tool.description}")
        print(f"   Data type: {tool.data_type}")
        print(f"   Usage: {tool.usage}")
        if tool.examples:
            print(f"   Example: {tool.examples[0]}")

    print("\n\U0001f4cb ANALYSIS WORKFLOWS:")
    print("-" * 40)

    for _wf_name, workflow in WORKFLOWS.items():
        print(f"\n\U0001f504 {workflow['name']}")
        print(f"   {workflow['description']}")
        print(f"   Tools used: {', '.join(workflow['tools'])}")

    print("\n\U0001f4c1 DATA ORGANIZATION:")
    print("-" * 40)
    print(
        f"   \u2022 Partial data: {data_org['partial_data_dir']}/"
        " (REFL_<set_ID>_<part_ID>_<run_ID>_partial.txt)"
    )
    print(
        f"   \u2022 Combined data: {data_org['combined_data_dir']}/"
        f" ({data_org['combined_data_template']})"
    )
    print(
        f"   \u2022 Models: {data_org['models_dir']}/"
        " (Python files with reflectivity models)"
    )
    print(
        f"   \u2022 Reports: {data_org['reports_dir']}/"
        " (Generated analysis reports and plots)"
    )

    print("\n\U0001f680 QUICK START:")
    print("-" * 40)
    print("   1. For partial data quality: assess-partial 218281")
    print("   2. For reflectivity fitting: run-fit 218281 cu_thf")
    print("   3. For result assessment: assess-result 218281 cu_thf")

    print("\n" + "=" * 70)


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


# ============================================================================
# Main CLI command
# ============================================================================


@click.command()
@click.option('--list-tools', 'list_tools', is_flag=True,
              help='List all available analysis tools')
@click.option('--help-tool', 'help_tool', type=str, metavar='TOOL',
              help='Get detailed help for a specific tool')
def main(list_tools: bool, help_tool: str):
    """Neutron Reflectometry Data Analysis Tools.

    \b
    Examples:
      analyzer-tools --list-tools              # Show all available tools
      analyzer-tools --help-tool partial       # Get help for partial data assessor
    """
    if list_tools:
        print_tool_overview()
        return

    if help_tool:
        try:
            from .registry import get_all_tools
        except ImportError:
            from analyzer_tools.registry import get_all_tools

        tools = get_all_tools()
        tool_key = None

        # Find tool by partial name match
        for key, tool in tools.items():
            if help_tool.lower() in key.lower() or help_tool.lower() in tool.name.lower():
                tool_key = key
                break

        if tool_key:
            tool = tools[tool_key]
            print(f"\n\U0001f527 {tool.name}")
            print("=" * (len(tool.name) + 3))
            print(f"Description: {tool.description}")
            print(f"Data type: {tool.data_type}")
            print(f"Usage: {tool.usage}")
            print("\nExamples:")
            for example in tool.examples:
                print(f"  {example}")
            print()
        else:
            print(f"Tool '{help_tool}' not found.")
            print("Available tools:")
            for key, tool in tools.items():
                print(f"  {key}: {tool.name}")
        return

    # If no arguments provided, show overview
    print_tool_overview()


if __name__ == "__main__":
    main()
