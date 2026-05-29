#!/usr/bin/env python3
import os
import sys
import numpy as np
import matplotlib.pyplot as plt
import glob
import re
import json
import shutil
import subprocess
from datetime import datetime
from analyzer_tools.config_utils import get_config
from typing import Any, Dict, List, Optional

import click
from refl1d.names import FitProblem
from refl1d import uncertainty
from bumps import serialize, dream



# Add project root to path to allow importing from other modules
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from ..utils import summary_plots
except ImportError:
    # Fallback for standalone execution
    from analyzer_tools.utils import summary_plots


def load_expt_json(expt_json_file):
    """
    Load the experiment JSON file and return the data.
    Parameters
    ----------
    expt_json_file : str
        Path to the experiment JSON file.
    Returns
    -------
    expt
        Experiment object.
    """
    if not os.path.exists(expt_json_file):
        raise FileNotFoundError(f"Experiment JSON file not found: {expt_json_file}")

    with open(expt_json_file, "r") as input_file:
        serialized = input_file.read()
        serialized_dict = json.loads(serialized)
        expt = serialize.deserialize(serialized_dict, migration=True)
    return expt


def get_sld_contour(
    problem, state, cl=90, npoints=200, trim=1000, portion=0.3, index=1, align="auto"
):
    points, _logp = state.sample(portion=portion)
    points = points[-trim:]
    original = problem.getp()
    _profiles, slabs, Q, residuals = uncertainty.calc_errors(problem, points)
    problem.setp(original)

    profiles = uncertainty.align_profiles(_profiles, slabs, align)

    # Group 1 is rho
    # Group 2 is irho
    # Group 3 is rhoM
    contours = []
    for model, group in profiles.items():
        ## Find limits of all profiles
        z = np.hstack([line[0] for line in group])
        zp = np.linspace(np.min(z), np.max(z), npoints)

        # Columns are z, best, low, high
        data, cols = uncertainty._build_profile_matrix(group, index, zp, [cl])
        contours.append(data)
    return contours


def assess_result(directory, reports_dir):
    """
    Reads the *-refl.dat file, plots the data, and updates the report.

    The report tag is derived from the basename of *directory*. Generated
    files use this tag, e.g. ``report_<tag>.md``,
    ``fit_result_<tag>_reflectivity.svg``, ``fit_result_<tag>_profile.svg``,
    ``sld_uncertainty_<tag>.txt``.

    Parameters
    ----------
    directory : str
        The directory containing the fit results.
    reports_dir : str
        The directory where reports are saved.
    """
    tag = os.path.basename(os.path.normpath(directory))
    # Find reflectivity data files. Multi-experiment fits (co-refines, partial
    # data sets) emit ``problem-1-refl.dat`` … ``problem-N-refl.dat``; older
    # single-experiment fits emit a single ``*-refl.dat``.
    refl_files = sorted(glob.glob(os.path.join(directory, "problem-*-refl.dat")))
    if not refl_files:
        refl_files = sorted(glob.glob(os.path.join(directory, "*-refl.dat")))
    if not refl_files:
        print(f"Error: No *-refl.dat file found in {directory}.")
        return

    # Concatenate all data sets to compute an overall chi-squared estimate.
    all_data = [np.loadtxt(f).T for f in refl_files]
    data = all_data[0]  # backwards-compat: parameter table sees the first set
    chisq_pieces = [
        ((d[2] - d[4]) ** 2 / d[3] ** 2) for d in all_data if d.shape[0] >= 5
    ]
    if chisq_pieces:
        chisq = float(np.mean(np.concatenate(chisq_pieces)))
    else:
        chisq = float("nan")

    # Read detailed fit results from parameter, JSON error, and experiment files
    par_file = os.path.join(directory, "problem.par")
    err_json_file = os.path.join(directory, "problem-err.json")
    expt_json_file = os.path.join(directory, "problem-1-expt.json")
    out_file = os.path.join(directory, "problem.out")

    fit_params = {}
    fit_quality = {}

    # Parse parameter values
    if os.path.exists(par_file):
        with open(par_file, "r") as f:
            for line in f:
                if line.strip():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        param_name = " ".join(parts[:-1])
                        param_value = float(parts[-1])
                        fit_params[param_name] = param_value

    # Parse uncertainties from JSON file
    param_uncertainties = {}
    if os.path.exists(err_json_file):
        try:
            with open(err_json_file, "r") as f:
                err_data = json.load(f)
                for param_name, param_info in err_data.items():
                    if isinstance(param_info, dict) and "std" in param_info:
                        param_uncertainties[param_name] = param_info["std"]
        except (json.JSONDecodeError, KeyError):
            print(f"Warning: Could not parse {err_json_file} for uncertainties")

    # Parse parameter ranges from experiment JSON file
    param_ranges = {}
    if os.path.exists(expt_json_file):
        try:
            with open(expt_json_file, "r") as f:
                expt_data = json.load(f)
                references = expt_data.get("references", {})
                for ref_id, ref_data in references.items():
                    if "bounds" in ref_data and ref_data["bounds"] is not None:
                        param_name = ref_data.get("name", "")
                        bounds = ref_data["bounds"]
                        if len(bounds) >= 2:
                            param_ranges[param_name] = (bounds[0], bounds[1])
        except (json.JSONDecodeError, KeyError):
            print(f"Warning: Could not parse {expt_json_file} for parameter ranges")

    # Parse overall fit quality from output file
    if os.path.exists(out_file):
        with open(out_file, "r") as f:
            content = f.read()
            # Look for chisq line
            for line in content.split("\n"):
                if "chisq=" in line and "nllf=" in line:
                    # Extract chisq value and uncertainty
                    chisq_part = line.split("chisq=")[1].split(",")[0]
                    if "(" in chisq_part:
                        chisq_val = float(chisq_part.split("(")[0])
                        chisq_unc = chisq_part.split("(")[1].split(")")[0]
                        fit_quality["chisq"] = chisq_val
                        fit_quality["chisq_unc"] = chisq_unc
                    break

    # Create the reflectivity plot — overlay every experiment in the fit.
    fig, ax = plt.subplots(dpi=150, figsize=(6, 4))
    plt.subplots_adjust(left=0.15, right=0.95, top=0.95, bottom=0.15)

    single = len(all_data) == 1
    for i, (rfile, d) in enumerate(zip(refl_files, all_data), start=1):
        label_data = "Data" if single else f"Data {i}"
        label_fit = "Fit" if single else f"Fit {i}"
        line = plt.errorbar(d[0], d[2], yerr=d[3], fmt=".", label=label_data)
        color = line.lines[0].get_color() if hasattr(line, "lines") else None
        if d.shape[0] >= 5:
            plt.plot(d[0], d[4], label=label_fit, color=color)
    plt.xlabel("Q (1/A)", fontsize=15)
    plt.ylabel("Reflectivity", fontsize=15)
    plt.xscale("log")
    plt.yscale("log")
    plt.legend(frameon=False, fontsize=8 if not single else 10)

    if not os.path.exists(reports_dir):
        os.makedirs(reports_dir)
    image_filename = f"fit_result_{tag}_reflectivity.svg"
    image_path = os.path.join(reports_dir, image_filename)
    plt.savefig(image_path, format="svg")
    print(f"Plot saved to {image_path}")

    # Plot the SLD profile(s) with uncertainty bands. Co-refined fits share
    # one Sample object across several Experiments, so multiple profile files
    # may be byte-identical — dedupe by fingerprinting the rho column.
    fig, ax = plt.subplots(dpi=150, figsize=(6, 4))
    plt.subplots_adjust(left=0.15, right=0.95, top=0.95, bottom=0.15)

    profile_files = sorted(glob.glob(os.path.join(directory, "problem-*-profile.dat")))
    if not profile_files:
        # Fall back to the legacy single-profile name.
        profile_files = [os.path.join(directory, "problem-1-profile.dat")]

    seen_fingerprints: set = set()
    unique_profiles: List[tuple] = []  # list of (idx, profile_path)
    for pfile in profile_files:
        if not os.path.exists(pfile):
            continue
        try:
            arr = np.loadtxt(pfile)
            fp = hash(np.round(arr[:, 1], 4).tobytes()) if arr.ndim == 2 and arr.shape[1] >= 2 else hash(arr.tobytes())
        except Exception:
            fp = pfile  # fall back to per-file uniqueness
        if fp in seen_fingerprints:
            continue
        seen_fingerprints.add(fp)
        m = re.search(r"problem-(\d+)-profile\.dat$", pfile)
        idx = int(m.group(1)) if m else len(unique_profiles) + 1
        unique_profiles.append((idx, pfile))

    multi_state = len(unique_profiles) > 1

    # Try to load the dream state once for CL bands; reuse across experiments.
    dream_state = None
    state_root = os.path.join(directory, "problem")
    try:
        dream_state = dream.state.load_state(state_root)
    except Exception as exc:  # pragma: no cover - depends on bumps state files
        print(f"Could not load DREAM state for SLD bands: {exc}")

    sld_txt_filename = f"sld_uncertainty_{tag}.txt"
    sld_txt_path = os.path.join(reports_dir, sld_txt_filename)
    if not os.path.exists(reports_dir):
        os.makedirs(reports_dir)
    sld_txt_handle = open(sld_txt_path, "w")
    sld_txt_handle.write("# state \t z \t best \t low (90% CL)\t high (90% CL)\n")

    for state_num, (idx, pfile) in enumerate(unique_profiles, start=1):
        label = f"State {state_num}" if multi_state else "SLD best"
        # Best-curve line (no CL band) — provided by summary_plots.plot_sld.
        summary_plots.plot_sld(pfile, label, show_cl=False, z_offset=0.0)

        # Confidence-limit band, if we can build the experiment + state.
        expt_json = os.path.join(directory, f"problem-{idx}-expt.json")
        if dream_state is None or not os.path.exists(expt_json):
            continue
        try:
            experiment = load_expt_json(expt_json)
            problem = FitProblem(experiment)
            contours = get_sld_contour(problem, dream_state, cl=90, align=-1)
            if not contours:
                continue
            z, best, low, high = contours[0]

            start_idx = len(best) - 1
            for k in range(len(best) - 1, 0, -1):
                if np.fabs(best[k] - best[k - 1]) > 0.001:
                    start_idx = k
                    break
            shifted_z = z[start_idx] - z
            color = plt.gca().lines[-1].get_color()
            plt.fill_between(
                shifted_z[:start_idx],
                low[:start_idx],
                high[:start_idx],
                alpha=0.2,
                color=color,
            )
            for zi, bi, lo, hi in zip(
                shifted_z[:start_idx], best[:start_idx], low[:start_idx], high[:start_idx]
            ):
                sld_txt_handle.write(f"{state_num} {zi:.6f} {bi:.6f} {lo:.6f} {hi:.6f}\n")
        except Exception as exc:
            print(f"Could not plot SLD uncertainty band for state {state_num}: {exc}")

    sld_txt_handle.close()
    print(f"SLD uncertainty bands saved to {sld_txt_path}")

    plt.xlabel("z ($\\AA$)", fontsize=15)
    plt.ylabel("SLD ($10^{-6}/{\\AA}^2$)", fontsize=15)
    ax.legend()

    image_filename = f"fit_result_{tag}_profile.svg"
    sld_image_path = os.path.join(reports_dir, image_filename)

    plt.savefig(sld_image_path, format="svg")
    print(f"Plot saved to {sld_image_path}")

    # Update the report
    report_file = os.path.join(reports_dir, f"report_{tag}.md")

    new_section_header = "## Fit results"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Format fit quality information
    fit_quality_text = ""
    if "chisq" in fit_quality:
        fit_quality_text = f"**Final Chi-squared**: {fit_quality['chisq']:.3f}({fit_quality['chisq_unc']}) - "
        chisq_val = fit_quality["chisq"]
        if chisq_val < 2.0:
            fit_quality_text += "Excellent fit quality"
        elif chisq_val < 3.0:
            fit_quality_text += "Good fit quality"
        elif chisq_val < 5.0:
            fit_quality_text += "Acceptable fit quality"
        else:
            fit_quality_text += "Poor fit quality - consider model revision"
    else:
        fit_quality_text = f"**Chi-squared**: {chisq:.2g}"

    # Create detailed parameter table
    param_table = (
        "| Layer | Parameter | Fitted Value | Uncertainty | Min | Max | Units |\n"
    )
    param_table += (
        "|-------|-----------|--------------|-------------|-----|-----|-------|\n"
    )

    # Group parameters by layer/component
    layers = {}
    for param_name, value in fit_params.items():
        if " " in param_name:
            layer_name = param_name.split()[0]
            param_type = " ".join(param_name.split()[1:])
        else:
            layer_name = "Beam"
            param_type = param_name

        if layer_name not in layers:
            layers[layer_name] = {}
        layers[layer_name][param_type] = value

    # Format table rows
    for layer_name, params in layers.items():
        for param_type, value in params.items():
            param_name = (
                f"{layer_name} {param_type}" if layer_name != "Beam" else param_type
            )

            # Try to find matching uncertainty (be flexible with parameter name matching)
            uncertainty = 0
            for unc_param_name, unc_value in param_uncertainties.items():
                if param_name == unc_param_name or (
                    layer_name in unc_param_name and param_type in unc_param_name
                ):
                    uncertainty = unc_value
                    break

            # Try to find matching parameter ranges
            param_min, param_max = None, None
            for range_param_name, (min_val, max_val) in param_ranges.items():
                if param_name == range_param_name or (
                    layer_name in range_param_name and param_type in range_param_name
                ):
                    param_min, param_max = min_val, max_val
                    break

            # Determine units
            units = "-"
            if "thickness" in param_type.lower():
                units = "Å"
            elif "interface" in param_type.lower():
                units = "Å"
            elif "rho" in param_type.lower():
                units = "×10⁻⁶ Å⁻²"

            # Format value and uncertainty
            if uncertainty > 0:
                if abs(value) >= 1:
                    value_str = f"{value:.2f}"
                    unc_str = f"±{uncertainty:.2f}"
                else:
                    value_str = f"{value:.4f}"
                    unc_str = f"±{uncertainty:.4f}"
            else:
                if abs(value) >= 1:
                    value_str = f"{value:.2f}"
                else:
                    value_str = f"{value:.4f}"
                unc_str = "N/A"

            # Format min/max values
            if param_min is not None and param_max is not None:
                if abs(param_min) >= 1 and abs(param_max) >= 1:
                    min_str = f"{param_min:.1f}"
                    max_str = f"{param_max:.1f}"
                else:
                    min_str = f"{param_min:.2f}"
                    max_str = f"{param_max:.2f}"
            else:
                min_str = "Fixed"
                max_str = "Fixed"

            param_table += f"| **{layer_name}** | {param_type} | {value_str} | {unc_str} | {min_str} | {max_str} | {units} |\n"

    new_content = (
        f"{new_section_header}\n"
        f"**Assessment run on**: {now}\n\n"
        f"### ✅ Fit Quality\n"
        f"{fit_quality_text}\n\n"
        f"### 📊 Fitted Parameters with Uncertainties\n\n"
        f"{param_table}\n"
        f"### 📁 File Locations\n"
        f"**Fit data location**: `{os.path.abspath(directory)}`\n\n"
        f"### 📈 Generated Plots\n"
        f"![Fit result]({os.path.relpath(image_path, reports_dir)})\n\n"
        f"![SLD profile]({os.path.relpath(sld_image_path, reports_dir)})\n\n"
        f"### 📝 Analysis Notes\n"
        f"- Fit converged successfully with {len(fit_params)} parameters\n"
        f"- Parameter uncertainties calculated from MCMC sampling\n"
        f"- Parameter ranges show fitting constraints used during optimization\n"
        f"- All parameters appear within reasonable physical ranges\n"
    )

    if os.path.exists(report_file):
        with open(report_file, "r") as f:
            content = f.read()

        # Use regex to find and replace the section for the same model
        pattern = re.compile(
            rf"({re.escape(new_section_header)}.*?)(?=\n## |\Z)", re.DOTALL
        )
        if pattern.search(content):
            content = pattern.sub(new_content, content)
        else:
            content += "\n" + new_content

        with open(report_file, "w") as f:
            f.write(content)
    else:
        with open(report_file, "w") as f:
            f.write(f"# Report for {tag}\n\n{new_content}")

    print(f"Report {report_file} updated.")





# ---------------------------------------------------------------------------
# AuRE evaluate augmentation
# ---------------------------------------------------------------------------


def _read_context(context: Optional[str], context_file: Optional[str]) -> Optional[str]:
    """Resolve sample description from inline text or a markdown file."""
    if context_file:
        with open(context_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return context


def run_aure_evaluate(
    results_dir: str,
    *,
    context: Optional[str] = None,
    hypothesis: Optional[str] = None,
    aure_executable: str = "aure",
    timeout: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Run ``aure evaluate <results_dir> --json`` and return parsed output.

    Returns ``None`` silently when AuRE is not installed or the call fails;
    the caller can inspect the return value and decide whether to skip the
    LLM section.  Errors from AuRE are captured in the returned dict under
    ``error`` when execution happens but fails parsing.
    """
    if shutil.which(aure_executable) is None:
        return None
    cmd = [aure_executable, "evaluate", str(results_dir), "--json"]
    if context:
        cmd.extend(["-c", context])
    if hypothesis:
        cmd.extend(["-h", hypothesis])
    try:
        completed = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.SubprocessError as exc:
        return {"error": f"subprocess failure: {exc}"}
    if completed.returncode != 0:
        return {
            "error": f"aure evaluate exited with code {completed.returncode}",
            "stderr": completed.stderr.strip(),
        }
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"error": "non-JSON output from aure evaluate", "stdout": completed.stdout.strip()}


def _render_aure_section(evaluation: Dict[str, Any]) -> str:
    """Render an AuRE evaluation dict as a markdown section."""
    lines: List[str] = ["## LLM Evaluation (AuRE)", ""]
    if evaluation.get("error"):
        lines.append(f"> AuRE evaluate did not run successfully: `{evaluation['error']}`")
        if evaluation.get("stderr"):
            lines.append("")
            lines.append("```")
            lines.append(evaluation["stderr"])
            lines.append("```")
        return "\n".join(lines) + "\n"

    verdict = evaluation.get("verdict") or evaluation.get("quality") or evaluation.get("status")
    if verdict:
        lines.append(f"**Verdict**: {verdict}")
    if "chi2" in evaluation:
        lines.append(f"**χ²**: {evaluation['chi2']}")

    issues = evaluation.get("issues") or []
    if issues:
        lines.append("")
        lines.append("### Issues")
        for item in issues:
            lines.append(f"- {item}")

    suggestions = evaluation.get("suggestions") or []
    if suggestions:
        lines.append("")
        lines.append("### Suggestions")
        for item in suggestions:
            lines.append(f"- {item}")

    plausibility = evaluation.get("physical_plausibility") or evaluation.get("plausibility")
    if plausibility:
        lines.append("")
        lines.append("### Physical Plausibility")
        if isinstance(plausibility, dict):
            for k, v in plausibility.items():
                lines.append(f"- **{k}**: {v}")
        else:
            lines.append(str(plausibility))

    summary = evaluation.get("summary") or evaluation.get("narrative")
    if summary:
        lines.append("")
        lines.append("### Narrative")
        lines.append(str(summary))

    lines.append("")
    return "\n".join(lines)


def append_aure_section_to_report(report_path: str, evaluation: Dict[str, Any]) -> None:
    """Append or replace an AuRE Evaluation section in *report_path*."""
    section = _render_aure_section(evaluation)
    header = "## LLM Evaluation (AuRE)"
    if os.path.exists(report_path):
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()
        pattern = re.compile(
            rf"({re.escape(header)}.*?)(?=\n## |\Z)", re.DOTALL
        )
        if pattern.search(content):
            content = pattern.sub(section.rstrip() + "\n", content)
        else:
            content = content.rstrip() + "\n\n" + section
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
    else:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(section)


@click.command()
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Directory to save reports. Defaults to ANALYZER_REPORTS_DIR env var.",
)
@click.option(
    "--skip-aure-eval",
    is_flag=True,
    default=False,
    help="Skip the AuRE `evaluate` augmentation step.",
)
@click.option(
    "--context",
    type=str,
    default=None,
    help="Sample description passed to `aure evaluate -c`.",
)
@click.option(
    "--sample-description",
    "context_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Markdown file whose contents are used as the AuRE context.",
)
@click.option(
    "--hypothesis",
    type=str,
    default=None,
    help="Optional hypothesis passed to `aure evaluate -h`.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Print a machine-readable summary (analyzer + AuRE) to stdout.",
)
def main(
    directory: str,
    output_dir: Optional[str],
    skip_aure_eval: bool,
    context: Optional[str],
    context_file: Optional[str],
    hypothesis: Optional[str],
    as_json: bool,
):
    """
    Assess the result of a reflectivity fit.

    DIRECTORY: Directory containing the fit results. The directory's
    basename is used as the report tag (e.g. ``results/cu_thf`` →
    ``report_cu_thf.md``).
    """
    config = get_config()

    if output_dir is None:
        output_dir = config.get_reports_dir()

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    tag = os.path.basename(os.path.normpath(directory))

    assess_result(directory, output_dir)

    evaluation: Optional[Dict[str, Any]] = None
    if not skip_aure_eval:
        ctx = _read_context(context, context_file)
        evaluation = run_aure_evaluate(
            directory,
            context=ctx,
            hypothesis=hypothesis,
        )
        if evaluation is not None:
            report_path = os.path.join(output_dir, f"report_{tag}.md")
            append_aure_section_to_report(report_path, evaluation)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "tag": tag,
                    "results_dir": os.path.abspath(directory),
                    "report": os.path.join(output_dir, f"report_{tag}.md"),
                    "aure_evaluation": evaluation,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
