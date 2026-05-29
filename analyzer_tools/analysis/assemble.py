"""Assemble partial reflectivity segments into a combined R(Q) file.

A Mantid-free way to produce the combined-data file the reduction normally
emits: load the ``REFL_<set_id>_*_partial.txt`` segments, optionally rescale
each to its predecessor's overlap region, then concatenate and sort by Q.

Reuses the partial-data loaders and overlap helpers from
``partial_data_assessor`` so the file/column conventions stay in one place.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional, Tuple

import click
import numpy as np

from analyzer_tools.config_utils import get_config
from analyzer_tools.analysis.partial_data_assessor import (
    calculate_match_metric,
    find_overlap_regions,
    get_data_files,
    read_data,
)


def _overlap_scale(prev: np.ndarray, cur: np.ndarray) -> Optional[float]:
    """Weighted least-squares scale ``s`` so that ``s * cur`` best matches
    ``prev`` over their shared Q range. Returns ``None`` if they don't overlap."""
    q_min = max(prev[:, 0].min(), cur[:, 0].min())
    q_max = min(prev[:, 0].max(), cur[:, 0].max())
    if not q_min < q_max:
        return None
    op = prev[(prev[:, 0] >= q_min) & (prev[:, 0] <= q_max)]
    if op.shape[0] == 0:
        return None
    r_cur = np.interp(op[:, 0], cur[:, 0], cur[:, 1])
    dr_cur = np.interp(op[:, 0], cur[:, 0], cur[:, 2])
    weights = 1.0 / (op[:, 2] ** 2 + dr_cur ** 2)
    denom = float(np.sum(weights * r_cur * r_cur))
    if denom <= 0:
        return None
    return float(np.sum(weights * op[:, 1] * r_cur) / denom)


def _sorted_by_q(segments: List[np.ndarray]) -> List[np.ndarray]:
    return [s[np.argsort(s[:, 0])] for s in segments]


def stitch(segments: List[np.ndarray], *, scale: bool = False) -> Tuple[np.ndarray, List[float]]:
    """Concatenate Q-sorted segments into one curve.

    With ``scale=True``, rescale each segment (cumulatively) so its overlap
    region matches the previous segment. Returns ``(combined, scale_factors)``.
    """
    segs = _sorted_by_q(segments)
    factors = [1.0] * len(segs)
    if scale and len(segs) > 1:
        cum = 1.0
        scaled = [segs[0]]
        for i in range(1, len(segs)):
            s = _overlap_scale(scaled[-1], segs[i])
            if s:
                cum *= s
            factors[i] = cum
            seg = segs[i].copy()
            seg[:, 1] *= cum
            seg[:, 2] *= cum
            scaled.append(seg)
        segs = scaled
    combined = np.vstack(segs)
    return combined[np.argsort(combined[:, 0])], factors


def overlap_chi2(segments: List[np.ndarray]) -> List[float]:
    """Adjacent-segment overlap χ² (same metric as assess-partial)."""
    return [calculate_match_metric(a, b) for a, b in find_overlap_regions(_sorted_by_q(segments))]


def assemble(
    set_id: str,
    data_dir: Optional[str] = None,
    out_path: Optional[str] = None,
    *,
    scale: bool = False,
) -> dict:
    """Build the combined file for *set_id* and return a summary dict."""
    cfg = get_config()
    data_dir = data_dir or cfg.get_partial_data_dir()
    files = get_data_files(set_id, data_dir)
    if not files:
        raise click.ClickException(
            f"No partial files for set {set_id} in {data_dir} "
            f"(expected REFL_{set_id}_*_partial.txt)."
        )
    segments = [read_data(f) for f in files]
    chi2 = overlap_chi2(segments)
    combined, factors = stitch(segments, scale=scale)

    if out_path is None:
        out_dir = cfg.get_combined_data_dir()
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, cfg.get_combined_data_template().format(set_id=set_id))
    # Combined files carry no header (matches the reduction's output convention).
    np.savetxt(out_path, combined, fmt="%.8g")

    return {
        "set_id": str(set_id),
        "n_segments": len(files),
        "segments": [os.path.basename(f) for f in files],
        "n_points": int(combined.shape[0]),
        "scaled": bool(scale),
        "scale_factors": factors,
        "overlap_chi2": chi2,
        "output": str(out_path),
    }


@click.command()
@click.argument("set_id")
@click.option("--data-dir", default=None,
              help="Directory holding the partial files. Defaults to the configured partial-data dir.")
@click.option("-o", "--output", "output", default=None,
              help="Output combined file. Defaults to <combined-dir>/<combined template>.")
@click.option("--scale/--no-scale", default=False, show_default=True,
              help="Rescale each segment to its predecessor's overlap region before combining.")
@click.option("--json", "as_json", is_flag=True, help="Print a JSON summary to stdout.")
@click.option("--result-out", "result_out", default=None,
              help="Write an ndip-tool-result/1 manifest to this path.")
def main(set_id, data_dir, output, scale, as_json, result_out):
    """Assemble REFL_<SET_ID>_*_partial.txt segments into a combined R(Q) file."""
    summary = assemble(set_id, data_dir=data_dir, out_path=output, scale=scale)

    if result_out:
        from analyzer_tools.result_manifest import write_manifest

        worst = max(summary["overlap_chi2"]) if summary["overlap_chi2"] else None
        write_manifest(
            result_out,
            "assemble-partials",
            "ok",
            params={"set_id": summary["set_id"], "data_dir": data_dir, "scale": scale},
            artifacts={"combined_file": summary["output"]},
            info={
                "n_segments": summary["n_segments"],
                "n_points": summary["n_points"],
                "worst_overlap_chi2": worst,
            },
        )

    if as_json:
        click.echo(json.dumps(summary))
    else:
        click.echo(
            f"Combined {summary['n_segments']} segments -> {summary['output']} "
            f"({summary['n_points']} points)"
        )
        if summary["overlap_chi2"]:
            click.echo("Adjacent-overlap chi2: "
                       + ", ".join(f"{c:.2f}" for c in summary["overlap_chi2"]))
        if scale:
            click.echo("Scale factors: "
                       + ", ".join(f"{x:.4f}" for x in summary["scale_factors"]))


if __name__ == "__main__":
    main()
