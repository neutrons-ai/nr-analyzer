"""Emit AuRE-compatible ``run_info.json`` and ``final_state.json``.

After ``run-fit`` finishes, the bumps export already contains everything
AuRE needs to visualise a fit (data curve, model curve, parameters,
uncertainties, bounds, SLD profile, chi²). This module reads those
files back and rewrites them into the two JSON documents that
``aure serve <dir>`` expects.

Writing into the same ``<results_dir>/<tag>/`` directory means

    aure serve ./results/<tag>

works without copying anything: AuRE picks up ``run_info.json`` and
``final_state.json``, and also has access to ``problem.json`` (its
own fallback path for bounds).
"""

from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Bumps export readers
# ---------------------------------------------------------------------------


def _read_refl_files(directory: Path) -> List[Tuple[Path, np.ndarray]]:
    """Return (path, array) pairs for every ``problem-*-refl.dat`` file."""
    paths = sorted(glob.glob(str(directory / "problem-*-refl.dat")))
    if not paths:
        paths = sorted(glob.glob(str(directory / "*-refl.dat")))
    out: List[Tuple[Path, np.ndarray]] = []
    for p in paths:
        arr = np.loadtxt(p)
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        out.append((Path(p), arr.T))
    return out


def _read_par(directory: Path) -> Dict[str, float]:
    par = directory / "problem.par"
    if not par.exists():
        return {}
    params: Dict[str, float] = {}
    for line in par.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            params[" ".join(parts[:-1])] = float(parts[-1])
        except ValueError:
            continue
    return params


def _read_uncertainties(directory: Path) -> Dict[str, float]:
    err = directory / "problem-err.json"
    if not err.exists():
        return {}
    try:
        data = json.loads(err.read_text())
    except json.JSONDecodeError:
        return {}
    out: Dict[str, float] = {}
    for name, info in data.items():
        if isinstance(info, dict) and "std" in info:
            try:
                out[name] = float(info["std"])
            except (TypeError, ValueError):
                pass
    return out


def _read_bounds(directory: Path) -> Dict[str, List[float]]:
    """Read parameter bounds from any ``problem-*-expt.json``."""
    expt_paths = sorted(glob.glob(str(directory / "problem-*-expt.json")))
    bounds: Dict[str, List[float]] = {}
    for p in expt_paths:
        try:
            data = json.loads(Path(p).read_text())
        except json.JSONDecodeError:
            continue
        for ref in (data.get("references") or {}).values():
            if not isinstance(ref, dict):
                continue
            name = ref.get("name")
            b = ref.get("bounds")
            if not name or not isinstance(b, (list, tuple)) or len(b) < 2:
                continue
            try:
                bounds[name] = [float(b[0]), float(b[1])]
            except (TypeError, ValueError):
                continue
    return bounds


def _read_overall_chi2(directory: Path) -> Optional[float]:
    out = directory / "problem.out"
    if not out.exists():
        return None
    for line in out.read_text().splitlines():
        if "chisq=" in line and "nllf=" in line:
            chunk = line.split("chisq=", 1)[1].split(",", 1)[0]
            chunk = chunk.split("(", 1)[0]
            try:
                return float(chunk)
            except ValueError:
                return None
    return None


def _read_sld_profiles(directory: Path) -> List[Tuple[int, np.ndarray]]:
    """Return list of (idx, profile) where profile is shape (N, ≥2)."""
    paths = sorted(glob.glob(str(directory / "problem-*-profile.dat")))
    out: List[Tuple[int, np.ndarray]] = []
    for p in paths:
        try:
            arr = np.loadtxt(p)
        except Exception:
            continue
        if arr.ndim != 2 or arr.shape[1] < 2:
            continue
        m = re.search(r"problem-(\d+)-profile\.dat$", p)
        idx = int(m.group(1)) if m else len(out) + 1
        out.append((idx, arr))
    return out


def _data_file_from_expt(directory: Path) -> Optional[str]:
    """Best-effort: extract the data filename recorded in the first expt JSON."""
    paths = sorted(glob.glob(str(directory / "problem-*-expt.json")))
    for p in paths:
        try:
            data = json.loads(Path(p).read_text())
        except json.JSONDecodeError:
            continue
        probe = data.get("probe") or {}
        for key in ("filename", "data_file", "file"):
            val = probe.get(key)
            if isinstance(val, str) and val:
                return val
    return None


# ---------------------------------------------------------------------------
# JSON document builders
# ---------------------------------------------------------------------------


def build_fit_result(
    refl_files: List[Tuple[Path, np.ndarray]],
    sld_profiles: List[Tuple[int, np.ndarray]],
    parameters: Dict[str, float],
    uncertainties: Dict[str, float],
    bounds: Dict[str, List[float]],
    overall_chi2: Optional[float],
    fitter: str,
) -> Dict[str, Any]:
    """Construct the single ``FitResult`` dict AuRE expects."""
    primary_path, primary = refl_files[0]
    Q = primary[0].tolist()
    R = primary[2].tolist() if primary.shape[0] >= 3 else []
    Q_fit = primary[0].tolist()
    R_fit = primary[4].tolist() if primary.shape[0] >= 5 else []

    if primary.shape[0] >= 5:
        residuals = (primary[2] - primary[4]).tolist()
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(primary[4] != 0, primary[2] / primary[4], np.nan)
        residual_ratio = [None if not np.isfinite(v) else float(v) for v in ratio]
    else:
        residuals = []
        residual_ratio = []

    chi2 = overall_chi2
    if chi2 is None and primary.shape[0] >= 5:
        diff = (primary[2] - primary[4]) ** 2
        denom = primary[3] ** 2
        with np.errstate(divide="ignore", invalid="ignore"):
            piece = np.where(denom > 0, diff / denom, np.nan)
        valid = piece[np.isfinite(piece)]
        chi2 = float(np.mean(valid)) if valid.size else None

    sld_z: List[float] = []
    sld_rho: List[float] = []
    if sld_profiles:
        _, prof = sld_profiles[0]
        sld_z = prof[:, 0].tolist()
        sld_rho = prof[:, 1].tolist()

    per_file: List[Dict[str, Any]] = []
    if len(refl_files) > 1:
        for path, arr in refl_files:
            label = _label_from_refl_path(path)
            pf: Dict[str, Any] = {
                "file": str(path),
                "label": label,
                "Q_fit": arr[0].tolist(),
                "R_fit": arr[4].tolist() if arr.shape[0] >= 5 else [],
            }
            if arr.shape[0] >= 5:
                pf["residuals"] = (arr[2] - arr[4]).tolist()
                with np.errstate(divide="ignore", invalid="ignore"):
                    r = np.where(arr[4] != 0, arr[2] / arr[4], np.nan)
                pf["residual_ratio"] = [
                    None if not np.isfinite(v) else float(v) for v in r
                ]
                diff = (arr[2] - arr[4]) ** 2
                denom = arr[3] ** 2
                with np.errstate(divide="ignore", invalid="ignore"):
                    piece = np.where(denom > 0, diff / denom, np.nan)
                valid = piece[np.isfinite(piece)]
                pf["chi_squared"] = (
                    float(np.mean(valid)) if valid.size else None
                )
            per_file.append(pf)

    return {
        "iteration": 0,
        "method": fitter,
        "chi_squared": chi2,
        "converged": True,
        "parameters": parameters,
        "uncertainties": uncertainties or None,
        "bounds": bounds or None,
        "Q_fit": Q_fit,
        "R_fit": R_fit,
        "residuals": residuals,
        "residual_ratio": residual_ratio,
        "sld_z": sld_z or None,
        "sld_rho": sld_rho or None,
        "per_file_results": per_file or None,
        "issues": [],
        "suggestions": [],
    }


def _label_from_refl_path(path: Path) -> str:
    m = re.search(r"problem-(\d+)-refl\.dat$", path.name)
    return f"experiment-{m.group(1)}" if m else path.stem


def build_state(
    refl_files: List[Tuple[Path, np.ndarray]],
    fit_result: Dict[str, Any],
    *,
    sample_description: str,
    hypothesis: Optional[str],
    data_file: str,
) -> Dict[str, Any]:
    """Assemble the ``state`` dict embedded in ``final_state.json``."""
    primary_path, primary = refl_files[0]
    Q = primary[0].tolist()
    R = primary[2].tolist() if primary.shape[0] >= 3 else []
    dR = primary[3].tolist() if primary.shape[0] >= 4 else []

    data_files: List[Dict[str, Any]] = []
    if len(refl_files) > 1:
        for path, arr in refl_files:
            data_files.append(
                {
                    "file": str(path),
                    "label": _label_from_refl_path(path),
                    "Q": arr[0].tolist(),
                    "R": arr[2].tolist() if arr.shape[0] >= 3 else [],
                    "dR": arr[3].tolist() if arr.shape[0] >= 4 else [],
                }
            )

    chi2 = fit_result.get("chi_squared")
    return {
        "data_file": data_file,
        "Q": Q,
        "R": R,
        "dR": dR,
        "sample_description": sample_description,
        "hypothesis": hypothesis,
        "fit_results": [fit_result],
        "current_chi2": chi2,
        "best_chi2": chi2,
        "iteration": 1,
        "max_iterations": 1,
        "workflow_complete": True,
        "error": None,
        "messages": [],
        "llm_calls": [],
        "data_files": data_files,
        "model_history": [],
        "active_skills": [],
        "structural_hypotheses": [],
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def export_for_aure(
    results_dir: str | os.PathLike,
    *,
    output_dir: Optional[str | os.PathLike] = None,
    sample_description: str = "",
    hypothesis: Optional[str] = None,
    data_file: Optional[str] = None,
    fitter: str = "dream",
    run_id: Optional[str] = None,
) -> Optional[Path]:
    """Write ``run_info.json`` and ``final_state.json`` for ``aure serve``.

    Parameters
    ----------
    results_dir
        The bumps export directory (e.g. ``./results/<tag>/``).
    output_dir
        Where to write the two JSON files. Defaults to *results_dir*
        so ``aure serve <results_dir>`` works directly.
    sample_description, hypothesis
        Free-text fields preserved by AuRE for display.
    data_file
        Reflectivity data path to record. If *None*, falls back to a
        path mined from ``problem-*-expt.json`` and then to *results_dir*.
    fitter
        Bumps fitter name recorded in the ``FitResult.method`` field.
    run_id
        Identifier shown in AuRE's history view. Defaults to a UTC
        timestamp.

    Returns
    -------
    Path | None
        The output directory if the export succeeded, ``None`` if no
        reflectivity data was found (nothing to export).
    """
    src = Path(results_dir)
    dst = Path(output_dir) if output_dir is not None else src
    dst.mkdir(parents=True, exist_ok=True)

    refl_files = _read_refl_files(src)
    if not refl_files:
        return None

    parameters = _read_par(src)
    uncertainties = _read_uncertainties(src)
    bounds = _read_bounds(src)
    overall_chi2 = _read_overall_chi2(src)
    sld_profiles = _read_sld_profiles(src)

    fit_result = build_fit_result(
        refl_files,
        sld_profiles,
        parameters,
        uncertainties,
        bounds,
        overall_chi2,
        fitter,
    )

    resolved_data_file = data_file or _data_file_from_expt(src) or str(src)
    state = build_state(
        refl_files,
        fit_result,
        sample_description=sample_description,
        hypothesis=hypothesis,
        data_file=resolved_data_file,
    )

    now = datetime.now(timezone.utc)
    started_at = now.isoformat()
    rid = run_id or now.strftime("%Y%m%d_%H%M%S")
    run_info = {
        "run_id": rid,
        "started_at": started_at,
        "data_file": resolved_data_file,
        "sample_description": sample_description,
        "hypothesis": hypothesis,
        "checkpoints": [],
    }
    final_state = {
        "completed_at": started_at,
        "success": True,
        "error": None,
        "iterations": 1,
        "final_chi2": fit_result.get("chi_squared"),
        "state": state,
    }

    (dst / "run_info.json").write_text(json.dumps(run_info, indent=2))
    (dst / "final_state.json").write_text(json.dumps(final_state, indent=2))
    return dst
