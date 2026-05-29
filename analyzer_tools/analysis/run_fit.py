"""Run a reflectivity fit from a complete refl1d-ready Python script.

The new ``run-fit`` is a thin driver around ``bumps.fitters.fit``. It expects
a script (typically produced by ``create-model``) that defines a module-level
``problem = FitProblem(...)`` and loads its own data. The fit results are
written to ``<results-dir>/<name>`` and an assessment report is generated in
``<reports-dir>``.
"""

from __future__ import annotations

import os
import runpy
from pathlib import Path
from typing import Optional

import click

from analyzer_tools.config_utils import get_config


def _load_problem(script_path: Path):
    """Execute *script_path* and return its module-level ``problem``."""
    ns = runpy.run_path(str(script_path), run_name="__analyzer_run_fit__")
    if "problem" not in ns:
        raise click.ClickException(
            f"Script {script_path} does not define a module-level `problem` "
            f"(expected `problem = FitProblem(...)`)."
        )
    return ns["problem"]


@click.command(context_settings={"show_default": True})
@click.argument(
    "script",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--results-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Parent directory for fit output. Defaults to $ANALYZER_RESULTS_DIR.",
)
@click.option(
    "--reports-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory for the assessment report. Defaults to $ANALYZER_REPORTS_DIR.",
)
@click.option(
    "--name",
    type=str,
    default=None,
    help="Output subfolder name and report tag. Defaults to the script stem.",
)
@click.option(
    "--fit",
    "fitter",
    type=str,
    default="dream",
    help="Bumps fitter (e.g. dream, amoeba, lm, de, newton).",
)
@click.option("--samples", type=int, default=10000, help="DREAM samples.")
@click.option("--burn", type=int, default=5000, help="DREAM burn-in steps.")
@click.option("--steps", type=int, default=0, help="Fitter steps (0 = fitter default).")
@click.option("--pop", type=int, default=0, help="Population size (0 = fitter default).")
@click.option("--init", type=str, default=None, help="DREAM init strategy.")
@click.option("--alpha", type=float, default=1.0, help="DREAM outlier alpha.")
@click.option("--seed", type=int, default=None, help="Random seed.")
@click.option(
    "--no-assess",
    is_flag=True,
    default=False,
    help="Skip post-fit assess-result invocation.",
)
@click.option(
    "--no-aure-export",
    is_flag=True,
    default=False,
    help="Skip writing run_info.json / final_state.json for `aure serve`.",
)
@click.option(
    "--sample-description",
    type=str,
    default="",
    help="Free-text sample description recorded in the AuRE export.",
)
@click.option(
    "--hypothesis",
    type=str,
    default=None,
    help="Optional hypothesis recorded in the AuRE export.",
)
def main(
    script: Path,
    results_dir: Optional[Path],
    reports_dir: Optional[Path],
    name: Optional[str],
    fitter: str,
    samples: int,
    burn: int,
    steps: int,
    pop: int,
    init: Optional[str],
    alpha: float,
    seed: Optional[int],
    no_assess: bool,
    no_aure_export: bool,
    sample_description: str,
    hypothesis: Optional[str],
) -> None:
    """Run a fit on SCRIPT (a complete refl1d-ready Python file).

    \b
    Examples:
        run-fit my_model.py --results-dir ./Results --fit dream --samples 20000
    """
    config = get_config()
    if results_dir is None:
        results_dir = Path(config.get_results_dir())
    if reports_dir is None:
        reports_dir = Path(config.get_reports_dir())

    tag = name or script.stem
    output_dir = results_dir / tag
    output_dir.mkdir(parents=True, exist_ok=True)

    problem = _load_problem(script)

    # Bumps writes export files as ``<problem.name>-*.dat``. If the script
    # does not set a name, bumps falls back to ``None`` which yields
    # ``None-1-refl.dat`` on disk and breaks downstream tools (e.g.
    # ``assess-result`` globs ``problem-*-refl.dat``). Force the basename.
    if not getattr(problem, "name", None):
        problem.name = "problem"

    # Build kwargs for bumps.fitters.fit, omitting zero/None placeholders so
    # the fitter's own defaults apply.
    fit_kwargs = {
        "method": fitter,
        "alpha": alpha,
        "verbose": 1,
        "export": str(output_dir),
    }
    if fitter == "dream":
        fit_kwargs["samples"] = samples
        fit_kwargs["burn"] = burn
    if steps > 0:
        fit_kwargs["steps"] = steps
    if pop > 0:
        fit_kwargs["pop"] = pop
    if init is not None:
        fit_kwargs["init"] = init
    if seed is not None:
        fit_kwargs["seed"] = seed

    from bumps.fitters import fit as _bumps_fit

    click.echo(f"Running fit: script={script} → {output_dir}", err=True)
    _bumps_fit(problem, **fit_kwargs)

    if not no_assess:
        try:
            from .result_assessor import assess_result
        except ImportError:  # pragma: no cover - script-style import
            from analyzer_tools.analysis.result_assessor import assess_result

        assess_result(str(output_dir), str(reports_dir))

    if not no_aure_export:
        try:
            from .aure_export import export_for_aure
        except ImportError:  # pragma: no cover - script-style import
            from analyzer_tools.analysis.aure_export import export_for_aure

        exported = export_for_aure(
            str(output_dir),
            sample_description=sample_description,
            hypothesis=hypothesis,
            fitter=fitter,
            run_id=tag,
        )
        if exported is not None:
            click.echo(f"AuRE export written to {exported}", err=True)


if __name__ == "__main__":
    main()
