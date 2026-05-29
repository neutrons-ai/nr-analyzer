import os
import glob
import json
import logging
import numpy as np
import matplotlib.pyplot as plt
import re
from datetime import datetime
from analyzer_tools.config_utils import get_config
from typing import Optional

import click

def get_data_files(set_id, data_dir):
    """
    Get the file paths for a given set_id.
    """
    file_pattern = os.path.join(data_dir, f"REFL_{set_id}_*_partial.txt")
    return sorted(glob.glob(file_pattern))

def read_data(file_path):
    """
    Read the 4-column data from a file.
    """
    # Q, R, dR, dQ
    return np.loadtxt(file_path, skiprows=1, usecols=(0,1,2,3))

def find_overlap_regions(data_parts):
    """
    Find the overlapping Q regions between adjacent data parts.
    
    Returns a list of tuples, where each tuple contains the two overlapping data parts.
    """
    if not data_parts or len(data_parts) < 2:
        return []

    overlaps = []
    for i in range(len(data_parts) - 1):
        data1 = data_parts[i]
        data2 = data_parts[i+1]

        q1_min, q1_max = data1[:, 0].min(), data1[:, 0].max()
        q2_min, q2_max = data2[:, 0].min(), data2[:, 0].max()

        overlap_min = max(q1_min, q2_min)
        overlap_max = min(q1_max, q2_max)

        if overlap_min < overlap_max:
            overlap1 = data1[(data1[:, 0] >= overlap_min) & (data1[:, 0] <= overlap_max)]
            overlap2 = data2[(data2[:, 0] >= overlap_min) & (data2[:, 0] <= overlap_max)]
            overlaps.append((overlap1, overlap2))
            
    return overlaps

def calculate_match_metric(overlap_data1, overlap_data2):
    """
    Calculate a metric for how well two overlap regions match.
    A simple metric could be the average of the ratio of the R values.
    """
    if overlap_data1.shape[0] == 0 or overlap_data2.shape[0] == 0:
        return 0

    # Interpolate the second dataset onto the Q values of the first one
    interp_r2 = np.interp(overlap_data1[:, 0], overlap_data2[:, 0], overlap_data2[:, 1])
    
    # Calculate the weighted average of the squared differences
    weights = 1 / (overlap_data1[:, 2]**2 + np.interp(overlap_data1[:, 0], overlap_data2[:, 0], overlap_data2[:, 2])**2)
    weighted_sq_diff = np.sum(weights * (overlap_data1[:, 1] - interp_r2)**2)
    chi2 = weighted_sq_diff / len(overlap_data1)
    
    return chi2

def plot_overlap_regions(data_parts, set_id, output_dir):
    """
    Plot the overlap regions for a given data set.
    """
    fig, ax = plt.subplots(dpi=150, figsize=(6, 4))
    plt.subplots_adjust(left=0.15, right=0.95, top=0.95, bottom=0.15)
    for i, data in enumerate(data_parts):
        ax.errorbar(data[:, 0], data[:, 1], yerr=data[:, 2], fmt='.', label=f'Part {i+1}')

    ax.set_xlabel('Q (1/A)', fontsize=15)
    ax.set_ylabel('Reflectivity', fontsize=15)
    plt.xscale('log')
    plt.yscale('log')
    plt.legend(frameon=False)
    
    plot_path = os.path.join(output_dir, f'reflectivity_curve_{set_id}.svg')
    plt.savefig(plot_path)
    plt.close()
    return plot_path

def generate_markdown_report(set_id, metrics, plot_path, output_dir, *, commentary=None, overlaps=None):
    """
    Generate a markdown report for a given data set.
    """
    report_file = os.path.join(output_dir, f'report_{set_id}.md')
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    new_section_header = "## Partial Data Assessment"
    new_content = (
        f"{new_section_header}\n"
        f"Assessment run on: {now}\n\n"
        f"![Reflectivity Curve]({os.path.basename(plot_path)})\n\n"
        "### Overlap Metrics (Chi-squared)\n\n"
    )
    if overlaps:
        for o in overlaps:
            new_content += (
                f"- Parts {o['parts'][0]}↔{o['parts'][1]}: "
                f"chi2 = {o['chi2']:.4f} ({o['classification']}), "
                f"n = {o['n_points']} over Q ∈ [{o['q_min']:.4f}, {o['q_max']:.4f}]\n"
            )
    else:
        for i, metric in enumerate(metrics):
            new_content += f"- Overlap {i+1}: {metric:.4f}\n"

    if commentary:
        new_content += "\n### Expert Commentary (LLM)\n\n" + commentary.rstrip() + "\n"

    if os.path.exists(report_file):
        with open(report_file, 'r') as f:
            content = f.read()
        
        pattern = re.compile(rf"({re.escape(new_section_header)}.*?)(?=\n## |\Z)", re.DOTALL)
        if pattern.search(content):
            content = pattern.sub(new_content, content)
        else:
            content += "\n" + new_content
        
        with open(report_file, 'w') as f:
            f.write(content)
    else:
        with open(report_file, 'w') as f:
            f.write(f"# Report for Set ID: {set_id}\n\n{new_content}")

    print(f"Report {report_file} updated.")


def assess_data_set(
    set_id,
    data_dir,
    output_dir,
    *,
    llm_commentary: bool | None = None,
    chi2_threshold: float = 3.0,
):
    """
    Main function to assess a data set.

    Returns a structured metrics dict (also written as JSON sidecar).
    """
    # Get data files
    file_paths = get_data_files(set_id, data_dir)
    if len(file_paths) < 2:
        print(f"Not enough data parts for set_id {set_id}")
        return None

    # Read data
    data_parts = [read_data(fp) for fp in file_paths]

    metrics = compute_metrics(set_id, file_paths, data_parts, chi2_threshold=chi2_threshold)

    # Plot overlap regions
    plot_path = plot_overlap_regions(data_parts, set_id, output_dir)
    metrics["plot"] = os.path.basename(plot_path)

    # Optional LLM commentary
    commentary = maybe_llm_commentary(metrics, enabled=llm_commentary)
    if commentary:
        metrics["llm_commentary"] = commentary

    # Structured JSON sidecar
    json_path = write_metrics_json(metrics, set_id, output_dir)
    metrics["metrics_json"] = os.path.basename(json_path)

    # Markdown report (uses the flat list of chi2 values for backwards
    # compatibility with the old report generator).
    chi2_list = [pair["chi2"] for pair in metrics["overlaps"]]
    generate_markdown_report(
        set_id,
        chi2_list,
        plot_path,
        output_dir,
        commentary=commentary,
        overlaps=metrics["overlaps"],
    )
    return metrics


# ---------------------------------------------------------------------------
# Structured metrics, JSON sidecar, optional LLM commentary
# ---------------------------------------------------------------------------


def _classify_chi2(chi2: float, threshold: float) -> str:
    """Match the thresholds documented in analyzer_tools/skills/partial-assessment/SKILL.md."""
    if chi2 < 1.5:
        return "good"
    if chi2 < threshold:
        return "acceptable"
    return "poor"


def compute_metrics(
    set_id: str,
    file_paths: list,
    data_parts: list,
    *,
    chi2_threshold: float = 3.0,
) -> dict:
    """Compute structured overlap metrics for every adjacent pair of parts.

    Returns a dict with keys ``set_id``, ``parts``, ``overlaps``, ``worst_chi2``,
    ``chi2_threshold``. Each ``overlaps`` entry has
    ``parts``, ``q_min``, ``q_max``, ``n_points``, ``chi2``, ``classification``.
    """
    overlap_regions = find_overlap_regions(data_parts)
    overlaps = []
    worst = 0.0
    for i, (o1, o2) in enumerate(overlap_regions):
        chi2 = float(calculate_match_metric(o1, o2))
        worst = max(worst, chi2)
        overlaps.append(
            {
                "parts": [i + 1, i + 2],
                "q_min": float(o1[:, 0].min()) if len(o1) else None,
                "q_max": float(o1[:, 0].max()) if len(o1) else None,
                "n_points": int(len(o1)),
                "chi2": chi2,
                "classification": _classify_chi2(chi2, chi2_threshold),
            }
        )
    return {
        "set_id": str(set_id),
        "chi2_threshold": float(chi2_threshold),
        "parts": [os.path.basename(p) for p in file_paths],
        "overlaps": overlaps,
        "worst_chi2": worst,
        "status": "poor" if worst >= chi2_threshold else "ok",
    }


def write_metrics_json(metrics: dict, set_id: str, output_dir: str) -> str:
    """Write *metrics* as JSON alongside the markdown report."""
    path = os.path.join(output_dir, f"partial_metrics_{set_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    return path


def maybe_llm_commentary(metrics: dict, *, enabled: bool | None) -> str | None:
    """Return a short LLM commentary on *metrics*, or None.

    Behaviour is auto-detect by default: returns None unless AuRE's LLM
    module is importable **and** configured.  Explicit ``enabled=False``
    disables entirely; ``enabled=True`` forces the attempt and raises on
    failure.
    """
    if enabled is False:
        return None
    try:
        from aure.llm import get_llm, llm_available  # type: ignore
    except Exception:
        if enabled:
            raise
        return None
    if not llm_available():
        if enabled:
            raise RuntimeError("AuRE LLM is not configured (set LLM_PROVIDER etc.)")
        return None

    summary_lines = [
        f"Set ID: {metrics['set_id']}",
        f"Parts: {', '.join(metrics['parts'])}",
        "Overlap chi-squared per adjacent pair:",
    ]
    for o in metrics["overlaps"]:
        summary_lines.append(
            f"  - parts {o['parts'][0]}↔{o['parts'][1]}: chi2={o['chi2']:.3f} "
            f"({o['classification']}), n={o['n_points']} over Q=[{o['q_min']:.4f}, {o['q_max']:.4f}]"
        )
    prompt = (
        "You are a neutron reflectometry expert reviewing overlap quality "
        "between adjacent partial reflectivity segments. Explain in 2-4 short "
        "sentences whether the data looks internally consistent and what a "
        "problematic pattern would suggest (wrong direct-beam, bad theta "
        "offset, sample change, etc.).\n\n"
        + "\n".join(summary_lines)
    )
    try:
        llm = get_llm()
        from langchain_core.messages import HumanMessage  # type: ignore

        response = llm.invoke([HumanMessage(content=prompt)])
        text = getattr(response, "content", str(response))
        return text.strip() if isinstance(text, str) else str(text).strip()
    except Exception as exc:
        logging.getLogger(__name__).warning("LLM commentary failed: %s", exc)
        if enabled:
            raise
        return None





@click.command()
@click.argument('set_id', type=str)
@click.option(
    '--data-dir',
    type=click.Path(exists=True, file_okay=False),
    default=None,
    help='Directory containing partial data files. Defaults to ANALYZER_PARTIAL_DATA_DIR env var.'
)
@click.option(
    '--output-dir',
    type=click.Path(file_okay=False),
    default=None,
    help='Directory for output reports and plots. Defaults to ANALYZER_REPORTS_DIR env var.'
)
@click.option(
    '--llm-commentary/--no-llm-commentary',
    default=None,
    help='Append an LLM-generated commentary to the report. Auto-detects when omitted '
         '(requires AuRE installed and configured).'
)
@click.option(
    '--chi2-threshold',
    type=float,
    default=3.0,
    show_default=True,
    help='Chi-squared threshold above which overlaps are flagged as "poor".'
)
@click.option(
    '--json',
    'as_json',
    is_flag=True,
    default=False,
    help='Print the structured metrics as JSON to stdout.'
)
def main(set_id: str, data_dir: Optional[str], output_dir: Optional[str],
         llm_commentary: Optional[bool], chi2_threshold: float, as_json: bool):
    """Assess partial data sets for quality and overlap matching.

    SET_ID is the identifier for the data set to assess.

    \b
    Examples:
      assess-partial 218281
      assess-partial 218281 --data-dir ./data/partial --output-dir ./reports
      assess-partial 218281 --llm-commentary --json
    """
    config = get_config()

    if data_dir is None:
        data_dir = config.get_partial_data_dir()
    if output_dir is None:
        output_dir = config.get_reports_dir()
    
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    
    metrics = assess_data_set(
        set_id,
        data_dir,
        output_dir,
        llm_commentary=llm_commentary,
        chi2_threshold=chi2_threshold,
    )
    if as_json and metrics is not None:
        click.echo(json.dumps(metrics, indent=2))


if __name__ == '__main__':
    main()
