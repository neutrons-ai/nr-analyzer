"""
Analyzer pipeline orchestrator — sequential workflow for a single sample.

Reads a YAML sample config (the same shape accepted by
``create-model --config``) and runs:

    1. assess-partial          (per state whose data files are partials)
    2. theta-offset (record)   (offsets supplied in the YAML are recorded)
    3. reduction-issue gate    (halts if blocking issues are found)
    4. create-model            (subprocess; produces a refl1d-ready script)
    5. run-fit                 (subprocess; fits and runs assess-result)
    6. aure evaluate           (optional, augments the fit report)

Reduction is NEVER run automatically: if the gate trips, the pipeline writes
``reduction_issues.md`` and a pre-filled ``reduction_batch.yaml`` manifest
into the per-sample report directory and exits with a non-zero code.

A small JSON state cache under
``<reports_root>/sample_<tag>/.pipeline_state.json`` lets users resume.

YAML configuration (same shape as ``create-model --config``)
-----------------------------------------------------------

Required:

* ``states:`` — list of state mappings. Each state has at least
  ``name`` and ``data:`` (list of REFL files). All files in one state
  must be the same kind (all partial or all combined) and partial files
  must share one ``set_id``.

Recommended:

* ``describe:`` — sample description (also accepts ``description``,
  ``sample_description``).
* ``model_name:`` (alias ``name``) — used as the analyzer-pipeline tag.
  Falls back to the YAML stem.

Optional pipeline-only fields:

* ``hypothesis:`` — passed to ``aure evaluate -h``.
* ``theta_offset:`` — list (or single dict) of pre-computed
  ``{run, offset}`` entries. The pipeline records these and uses them
  for the reduction-issue gate; the theta-offset tool itself is NOT
  invoked here.

Any field accepted by ``create-model --config`` (``data_dir``,
``shared_parameters``, ``unshared_parameters``, ``out``, per-state
``extra_description`` / ``theta_offset`` / ``sample_broadening`` /
``back_reflection``) is forwarded verbatim — the pipeline does not
interpret them, it just hands the YAML to ``create-model``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click
import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sample spec — mirrors the create-model YAML shape
# ---------------------------------------------------------------------------


_PARTIAL_RE = re.compile(r"^REFL_(\d+)_\d+_\d+_partial\.txt$")
_COMBINED_RE = re.compile(r"^REFL_(\d+)_combined_data_auto\.txt$")


@dataclass
class SampleSpec:
    """Parsed analyze-sample / create-model config."""

    config_path: Path
    model_name: str
    describe: str = ""
    states: List[Dict[str, Any]] = field(default_factory=list)
    data_dir_override: Optional[str] = None
    theta_offset: Optional[Any] = None
    hypothesis: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def tag(self) -> str:
        return self.model_name


def _pick(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d:
            return d[k]
    return None


def parse_sample_file(path: str | os.PathLike) -> SampleSpec:
    """Parse a create-model-style YAML config into a :class:`SampleSpec`.

    The file is the same shape used by ``create-model --config``: a YAML
    mapping with at least a non-empty ``states:`` list.
    """
    p = Path(path)
    if not p.is_file():
        raise click.ClickException(f"Sample config '{p}' is not a file.")
    try:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise click.ClickException(f"Sample config '{p}' has invalid YAML: {exc}")
    if not isinstance(cfg, dict):
        raise click.ClickException(f"Sample config '{p}' must be a YAML mapping.")
    states = cfg.get("states")
    if not isinstance(states, list) or not states:
        raise click.ClickException(
            f"Sample config '{p}' must define a non-empty 'states:' list. "
            "analyze-sample uses the same YAML shape as `create-model --config`."
        )
    model_name = _pick(cfg, "model_name", "name") or p.stem
    describe = _pick(cfg, "describe", "description", "sample_description") or ""
    return SampleSpec(
        config_path=p.resolve(),
        model_name=str(model_name),
        describe=str(describe),
        states=states,
        data_dir_override=cfg.get("data_dir"),
        theta_offset=cfg.get("theta_offset"),
        hypothesis=cfg.get("hypothesis"),
        raw=cfg,
    )


def _resolve_data_path(p: str, *, config_dir: Path, data_dir: Optional[str]) -> Path:
    """Resolve a YAML data-file path the same way ``create-model`` does."""
    candidate = Path(os.path.expanduser(p))
    if candidate.is_absolute():
        return candidate
    if data_dir:
        d = Path(os.path.expanduser(data_dir))
        if not d.is_absolute():
            d = (config_dir / d).resolve()
        return d / candidate
    return (config_dir / candidate).resolve()


def classify_states(spec: SampleSpec) -> List[Dict[str, Any]]:
    """For each state, identify ``kind`` (partial/combined/unknown) and
    derive the ``set_id`` and ``partial_dir`` from its data files."""
    config_dir = spec.config_path.parent
    out: List[Dict[str, Any]] = []
    for st in spec.states:
        files = _pick(st, "data", "data_files") or []
        if not isinstance(files, list):
            files = [files]
        kind = "unknown"
        set_id: Optional[str] = None
        partial_dir: Optional[Path] = None
        for f in files:
            name = os.path.basename(str(f))
            mp = _PARTIAL_RE.match(name)
            mc = _COMBINED_RE.match(name)
            if mp:
                kind = "partial"
                set_id = mp.group(1)
                full = _resolve_data_path(
                    str(f), config_dir=config_dir, data_dir=spec.data_dir_override
                )
                partial_dir = full.parent
                break
            if mc:
                kind = "combined"
                set_id = mc.group(1)
                break
        out.append(
            {
                "name": st.get("name", "?"),
                "kind": kind,
                "set_id": set_id,
                "partial_dir": str(partial_dir) if partial_dir else None,
                "files": [str(f) for f in files],
            }
        )
    return out


# ---------------------------------------------------------------------------
# State cache
# ---------------------------------------------------------------------------


@dataclass
class PipelineState:
    """Pipeline execution state persisted to disk for resume support."""

    tag: str
    status: str = "running"  # running | ok | needs-reprocessing | failed | dry-run
    completed_stages: List[str] = field(default_factory=list)
    stage_outputs: Dict[str, Any] = field(default_factory=dict)
    reduction_issues: List[Dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def save(self, path: str | os.PathLike) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: str | os.PathLike) -> Optional["PipelineState"]:
        p = Path(path)
        if not p.exists():
            return None
        return cls(**json.loads(p.read_text()))


# ---------------------------------------------------------------------------
# Reduction-issue gate
# ---------------------------------------------------------------------------


def detect_reduction_issues(
    partial_metrics: Optional[Dict[str, Any]],
    theta_offsets: Optional[List[Dict[str, Any]]],
    *,
    chi2_threshold: float,
    offset_threshold_deg: float,
) -> List[Dict[str, Any]]:
    """Build the structured ``reduction_issues`` list from prior step outputs."""
    issues: List[Dict[str, Any]] = []

    if partial_metrics:
        for pair in partial_metrics.get("overlaps", []):
            chi2 = pair["chi2"]
            if chi2 >= chi2_threshold:
                issues.append(
                    {
                        "type": "partial_overlap_chi2",
                        "set_id": partial_metrics.get("set_id"),
                        "segments": pair["parts"],
                        "severity": "block",
                        "chi2": chi2,
                        "threshold": chi2_threshold,
                        "detail": (
                            f"Overlap between parts {pair['parts'][0]} and "
                            f"{pair['parts'][1]} has chi^2={chi2:.2f} "
                            f"(> {chi2_threshold}). Likely bad normalization, "
                            "wrong direct-beam run, or misaligned segments."
                        ),
                    }
                )

    if theta_offsets:
        for entry in theta_offsets:
            offset = float(entry.get("offset", 0.0))
            if abs(offset) > offset_threshold_deg:
                issues.append(
                    {
                        "type": "theta_offset",
                        "run": entry.get("run"),
                        "severity": "block",
                        "offset_deg": offset,
                        "threshold_deg": offset_threshold_deg,
                        "detail": (
                            f"Computed theta offset {offset:+.4f}° exceeds "
                            f"threshold ±{offset_threshold_deg}°. Reduction "
                            "should be re-run with this offset applied."
                        ),
                    }
                )

    return issues


def should_halt(issues: List[Dict[str, Any]]) -> bool:
    return any(i.get("severity") == "block" for i in issues)


def write_reduction_issues_md(
    path: str | os.PathLike,
    tag: str,
    issues: List[Dict[str, Any]],
    partial_metrics_list: List[Dict[str, Any]],
    theta_offsets: Optional[List[Dict[str, Any]]],
) -> None:
    """Write a human-readable issues report."""
    lines: List[str] = [
        f"# Reprocessing required — {tag}",
        "",
        "The analyzer detected issues in the reduced data that prevent a",
        "meaningful fit. Please re-reduce the raw data and re-run the",
        "pipeline.",
        "",
        "## Detected issues",
        "",
    ]
    for i, issue in enumerate(issues, start=1):
        lines.append(f"### {i}. {issue['type']} ({issue['severity']})")
        lines.append("")
        lines.append(issue["detail"])
        lines.append("")

    for pm in partial_metrics_list or []:
        if not pm:
            continue
        lines.append(f"## Partial overlap χ² summary — set {pm.get('set_id', '?')}")
        lines.append("")
        for pair in pm.get("overlaps", []):
            lines.append(
                f"- Parts {pair['parts'][0]}↔{pair['parts'][1]}: "
                f"χ² = {pair['chi2']:.3f} ({pair['classification']})"
            )
        lines.append("")

    if theta_offsets:
        lines.append("## Theta-offset summary")
        lines.append("")
        for entry in theta_offsets:
            lines.append(
                f"- Run {entry.get('run', '?')}: offset = "
                f"{float(entry.get('offset', 0.0)):+.4f}°"
            )
        lines.append("")

    lines.extend(
        [
            "## How to proceed",
            "",
            "1. Review `reduction_batch.yaml` in this directory. It is pre-filled",
            "   with one `simple-reduction` job per segment.",
            "2. Edit any paths or options you need to adjust.",
            "3. Run the reductions:",
            "",
            "   ```bash",
            "   analyzer-batch reduction_batch.yaml",
            "   ```",
            "",
            "4. Re-run `analyze-sample` on the re-reduced data.",
            "",
            "## References",
            "",
            "- Theta-offset skill: `analyzer_tools/skills/theta-offset/SKILL.md`",
            "- Partial-data skill: `analyzer_tools/skills/partial-assessment/SKILL.md`",
            "",
        ]
    )
    Path(path).write_text("\n".join(lines))


def _build_reduction_batch_manifest(
    tag: str,
    set_ids: List[str],
    theta_offsets: Optional[List[Dict[str, Any]]],
    offset_log: Optional[str],
    template_xml: Optional[str],
) -> Dict[str, Any]:
    jobs: List[Dict[str, Any]] = []
    entries = list(theta_offsets or [])
    if not entries:
        for sid in set_ids or [tag]:
            entries.append({"run": sid})
    for entry in entries:
        run = entry.get("run", tag)
        args: List[Any] = ["--event-file", f"REF_L_{run}.nxs.h5"]
        if template_xml:
            args.extend(["--template", str(template_xml)])
        else:
            primary = (set_ids or [run])[0]
            args.extend(["--template", f"REF_L_{primary}_auto_template.xml"])
        if offset_log:
            args.extend(["--offset-csv", str(offset_log)])
        jobs.append(
            {"name": f"reduce_{run}", "tool": "simple-reduction", "args": args}
        )
    return {"defaults": {"output_root": "./reduced"}, "jobs": jobs}


def write_reduction_batch_yaml(
    path: str | os.PathLike,
    tag: str,
    theta_offsets: Optional[List[Dict[str, Any]]],
    *,
    set_ids: Optional[List[str]] = None,
    offset_log: Optional[str] = None,
    template_xml: Optional[str] = None,
) -> None:
    manifest = _build_reduction_batch_manifest(
        tag, set_ids or [], theta_offsets, offset_log, template_xml
    )
    Path(path).write_text(
        "# Auto-generated by analyze-sample. Review before running:\n"
        "#   analyzer-batch reduction_batch.yaml\n\n"
        + yaml.safe_dump(manifest, sort_keys=False)
    )


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------


def _run_partial_assessment_for_set(
    set_id: str,
    partial_dir: str,
    reports_dir: str,
    *,
    chi2_threshold: float,
    llm_commentary: Optional[bool],
) -> Optional[Dict[str, Any]]:
    """Run assess-partial in-process for one set_id."""
    from analyzer_tools.analysis import partial_data_assessor as pda

    if not os.path.isdir(partial_dir):
        logger.info("No partial_dir '%s'; skipping partial assessment", partial_dir)
        return None
    files = pda.get_data_files(set_id, partial_dir)
    if len(files) < 2:
        logger.info("Only %d partial files for set %s; skipping", len(files), set_id)
        return None
    return pda.assess_data_set(
        set_id,
        partial_dir,
        reports_dir,
        llm_commentary=llm_commentary,
        chi2_threshold=chi2_threshold,
    )


def _expected_script_path(spec: SampleSpec) -> Path:
    """Predict where ``create-model`` will write the generated script."""
    out = spec.raw.get("out")
    if out:
        out_path = Path(os.path.expanduser(str(out)))
        if not out_path.is_absolute():
            out_path = (spec.config_path.parent / out_path).resolve()
        return out_path
    from analyzer_tools.config_utils import get_config

    models_dir = Path(get_config().get_models_dir())
    return models_dir / f"{spec.model_name}.py"


def _run_create_model(spec: SampleSpec) -> Path:
    """Subprocess ``create-model --config <yaml>``; return the script path."""
    if shutil.which("create-model") is None:
        raise click.ClickException("create-model executable not found on PATH.")
    cmd = ["create-model", "--config", str(spec.config_path)]
    logger.info("Running: %s", " ".join(cmd))
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        raise click.ClickException(f"create-model failed with exit code {rc}")
    script = _expected_script_path(spec)
    if not script.is_file():
        raise click.ClickException(
            f"create-model returned 0 but the expected script "
            f"'{script}' was not produced."
        )
    return script


def _run_fit(
    script: Path,
    results_root: Path,
    reports_root: Path,
    tag: str,
    *,
    skip_aure_eval: bool,
    sample_description: str = "",
    hypothesis: Optional[str] = None,
) -> Tuple[int, Path]:
    """Subprocess ``run-fit``. Returns (exit_code, output_dir)."""
    if shutil.which("run-fit") is None:
        raise click.ClickException("run-fit executable not found on PATH.")
    output_dir = results_root / tag
    cmd = [
        "run-fit",
        str(script),
        "--results-dir", str(results_root),
        "--reports-dir", str(reports_root),
        "--name", tag,
    ]
    if sample_description:
        cmd.extend(["--sample-description", sample_description])
    if hypothesis:
        cmd.extend(["--hypothesis", hypothesis])
    logger.info("Running: %s", " ".join(cmd))
    rc = subprocess.run(cmd, check=False).returncode
    return rc, output_dir


# ---------------------------------------------------------------------------
# Consolidated sample report
# ---------------------------------------------------------------------------


def write_sample_reports(
    report_dir: str,
    state: PipelineState,
    spec: SampleSpec,
    classes: List[Dict[str, Any]],
) -> None:
    """Write sample_<tag>.md and sample_<tag>.json under report_dir."""
    md_path = os.path.join(report_dir, f"sample_{spec.tag}.md")
    json_path = os.path.join(report_dir, f"sample_{spec.tag}.json")

    lines: List[str] = [f"# Sample {spec.tag}", ""]
    if state.status == "needs-reprocessing":
        lines.append("> ⚠ **Reprocessing required** — see `reduction_issues.md`.")
        lines.append("")
    if spec.describe:
        lines.append("## Sample description")
        lines.append("")
        lines.append(spec.describe)
        lines.append("")

    lines.append("## States")
    lines.append("")
    for c in classes:
        lines.append(
            f"- **{c['name']}** — kind={c['kind']}, set_id={c['set_id']}, "
            f"{len(c['files'])} file(s)"
        )
    lines.append("")

    lines.append("## Pipeline status")
    lines.append("")
    lines.append(f"- Status: **{state.status}**")
    lines.append(f"- Completed stages: {', '.join(state.completed_stages) or '(none)'}")
    lines.append("")

    if state.reduction_issues:
        lines.append("## Reduction issues")
        lines.append("")
        for issue in state.reduction_issues:
            lines.append(f"- **{issue['type']}** ({issue['severity']}): {issue['detail']}")
        lines.append("")

    stage_outputs = state.stage_outputs
    if stage_outputs.get("partial"):
        lines.append("## Partial assessment")
        lines.append("")
        for pm in stage_outputs["partial"]:
            lines.append(
                f"- Set {pm.get('set_id')}: worst χ² = {pm.get('worst_chi2', 'n/a')}"
            )
        lines.append("")
    if "fit" in stage_outputs:
        lines.append("## Fit")
        lines.append("")
        lines.append(f"- Results: `{stage_outputs['fit'].get('results_dir')}`")
        lines.append("")
    if "aure_eval" in stage_outputs:
        ev = stage_outputs["aure_eval"].get("evaluation")
        if ev:
            lines.append("## AuRE evaluation")
            lines.append("")
            if ev.get("error"):
                lines.append(f"- Status: **failed** — {ev['error']}")
                if ev.get("stderr"):
                    lines.append("")
                    lines.append("```")
                    lines.append(str(ev["stderr"]))
                    lines.append("```")
            else:
                verdict = ev.get("verdict") or ev.get("quality") or ev.get("status")
                if verdict:
                    lines.append(f"- Verdict: {verdict}")
                else:
                    lines.append(
                        "- Verdict: (none reported by aure evaluate; see "
                        "`report_<tag>.md` for the full LLM section)"
                    )
            lines.append("")

    Path(md_path).write_text("\n".join(lines))
    Path(json_path).write_text(
        json.dumps({"spec": _spec_to_dict(spec), "state": asdict(state)}, indent=2)
    )


def _spec_to_dict(spec: SampleSpec) -> Dict[str, Any]:
    d = asdict(spec)
    # Path is not JSON-serialisable.
    d["config_path"] = str(spec.config_path)
    return d


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_pipeline(
    spec: SampleSpec,
    *,
    results_root: str,
    reports_root: str,
    chi2_threshold: float = 3.0,
    offset_threshold_deg: float = 0.01,
    reduction_gate: bool = True,
    llm_commentary: Optional[bool] = None,
    skip_aure_eval: bool = False,
    dry_run: bool = False,
    force: bool = False,
    skip_partial: bool = False,
    skip_fit: bool = False,
) -> PipelineState:
    """Run the analyzer pipeline for one sample.

    Returns the final :class:`PipelineState`.
    """
    tag = spec.tag
    report_dir = os.path.join(reports_root, f"sample_{tag}")
    os.makedirs(report_dir, exist_ok=True)
    state_path = os.path.join(report_dir, ".pipeline_state.json")

    existing = None if force else PipelineState.load(state_path)
    state = existing or PipelineState(tag=tag)

    def _mark(stage: str, output: Any = None) -> None:
        if stage not in state.completed_stages:
            state.completed_stages.append(stage)
        if output is not None:
            state.stage_outputs[stage] = output
        state.save(state_path)

    def _done_stage(stage: str) -> bool:
        return stage in state.completed_stages

    classes = classify_states(spec)
    partial_states = [
        c for c in classes if c["kind"] == "partial" and c["set_id"] and c["partial_dir"]
    ]
    set_ids = [c["set_id"] for c in classes if c["set_id"]]

    if dry_run:
        click.echo("Planned pipeline:")
        click.echo(f"  Tag:     {tag}")
        click.echo(f"  Config:  {spec.config_path}")
        for c in classes:
            click.echo(
                f"  State {c['name']}: kind={c['kind']} set_id={c['set_id']} "
                f"partial_dir={c['partial_dir']}"
            )
        click.echo(f"  1. assess-partial × {len(partial_states)}")
        click.echo("  2. theta-offset gate")
        click.echo(f"  3. create-model --config {spec.config_path}")
        click.echo(f"  4. run-fit <script> --name {tag}")
        click.echo(f"  Reports: {report_dir}")
        click.echo(f"  Results: {os.path.join(results_root, tag)}")
        state.status = "dry-run"
        return state

    # --- Stage 1: partial assessment (per partial-data state) ----------------
    partial_metrics_list: List[Dict[str, Any]] = []
    if not skip_partial and not _done_stage("partial"):
        for c in partial_states:
            m = _run_partial_assessment_for_set(
                c["set_id"],
                c["partial_dir"],
                report_dir,
                chi2_threshold=chi2_threshold,
                llm_commentary=llm_commentary,
            )
            if m:
                partial_metrics_list.append(m)
        _mark("partial", partial_metrics_list)
    else:
        partial_metrics_list = state.stage_outputs.get("partial") or []

    # --- Stage 2: theta-offset (record YAML-supplied offsets only) ----------
    theta_offsets: Optional[List[Dict[str, Any]]] = state.stage_outputs.get("theta")
    if spec.theta_offset and not _done_stage("theta"):
        to = spec.theta_offset
        if isinstance(to, list):
            theta_offsets = to
        elif isinstance(to, dict):
            theta_offsets = [to]
        else:
            theta_offsets = None
        _mark("theta", theta_offsets)

    # --- Stage 3: reduction-issue gate --------------------------------------
    issues: List[Dict[str, Any]] = []
    if reduction_gate:
        for pm in partial_metrics_list:
            issues.extend(
                detect_reduction_issues(
                    pm,
                    None,
                    chi2_threshold=chi2_threshold,
                    offset_threshold_deg=offset_threshold_deg,
                )
            )
        if theta_offsets:
            issues.extend(
                detect_reduction_issues(
                    None,
                    theta_offsets,
                    chi2_threshold=chi2_threshold,
                    offset_threshold_deg=offset_threshold_deg,
                )
            )
        state.reduction_issues = issues

    if issues and should_halt(issues):
        state.status = "needs-reprocessing"
        write_reduction_issues_md(
            os.path.join(report_dir, "reduction_issues.md"),
            tag,
            issues,
            partial_metrics_list,
            theta_offsets,
        )
        write_reduction_batch_yaml(
            os.path.join(report_dir, "reduction_batch.yaml"),
            tag,
            theta_offsets,
            set_ids=set_ids,
        )
        write_sample_reports(report_dir, state, spec, classes)
        state.finished_at = time.time()
        state.save(state_path)
        return state

    if skip_fit:
        state.status = "ok"
        state.finished_at = time.time()
        write_sample_reports(report_dir, state, spec, classes)
        state.save(state_path)
        return state

    # --- Stage 4: create-model ----------------------------------------------
    if not _done_stage("create_model"):
        try:
            script = _run_create_model(spec)
        except click.ClickException as exc:
            state.status = "failed"
            state.stage_outputs["create_model"] = {"error": str(exc)}
            write_sample_reports(report_dir, state, spec, classes)
            state.save(state_path)
            return state
        _mark("create_model", {"script": str(script)})
    else:
        script = Path(state.stage_outputs["create_model"]["script"])
        if not script.is_file():
            # Cached path is stale — regenerate.
            try:
                script = _run_create_model(spec)
            except click.ClickException as exc:
                state.status = "failed"
                state.stage_outputs["create_model"] = {"error": str(exc)}
                write_sample_reports(report_dir, state, spec, classes)
                state.save(state_path)
                return state
            state.stage_outputs["create_model"] = {"script": str(script)}
            state.save(state_path)

    # --- Stage 5: run-fit (auto-runs assess-result) -------------------------
    if not _done_stage("fit"):
        try:
            rc, output_dir = _run_fit(
                script,
                Path(results_root),
                Path(reports_root),
                tag,
                skip_aure_eval=skip_aure_eval,
                sample_description=spec.describe,
                hypothesis=spec.hypothesis,
            )
        except click.ClickException as exc:
            state.status = "failed"
            state.stage_outputs["fit"] = {"error": str(exc)}
            write_sample_reports(report_dir, state, spec, classes)
            state.save(state_path)
            return state
        if rc != 0:
            state.status = "failed"
            state.stage_outputs["fit"] = {
                "exit_code": rc,
                "results_dir": str(output_dir),
            }
            write_sample_reports(report_dir, state, spec, classes)
            state.save(state_path)
            return state
        _mark("fit", {"results_dir": str(output_dir)})

    # --- Stage 6: AuRE evaluate (optional augmentation) ---------------------
    if not skip_aure_eval and not _done_stage("aure_eval"):
        from analyzer_tools.analysis import result_assessor as ra

        results_dir = state.stage_outputs.get("fit", {}).get("results_dir")
        evaluation = None
        if results_dir:
            evaluation = ra.run_aure_evaluate(
                results_dir,
                context=spec.describe,
                hypothesis=spec.hypothesis,
            )
            if evaluation is not None:
                report_path = os.path.join(reports_root, f"report_{tag}.md")
                ra.append_aure_section_to_report(report_path, evaluation)
        _mark("aure_eval", {"evaluation": evaluation})

    state.status = "ok"
    state.finished_at = time.time()
    write_sample_reports(report_dir, state, spec, classes)
    state.save(state_path)
    return state


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.command()
@click.argument(
    "config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=False,
)
@click.option(
    "--results-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Top-level results directory. Defaults to ANALYZER_RESULTS_DIR.",
)
@click.option(
    "--reports-dir",
    type=click.Path(file_okay=False),
    default=None,
    help="Top-level reports directory. Defaults to ANALYZER_REPORTS_DIR.",
)
@click.option("--reduction-gate/--no-reduction-gate", default=True, show_default=True)
@click.option("--chi2-threshold", type=float, default=3.0, show_default=True)
@click.option("--offset-threshold-deg", type=float, default=0.01, show_default=True)
@click.option("--llm-commentary/--no-llm-commentary", default=None)
@click.option("--skip-aure-eval", is_flag=True, default=False)
@click.option("--skip-partial", is_flag=True, default=False)
@click.option("--skip-fit", is_flag=True, default=False)
@click.option("--force", is_flag=True, default=False, help="Ignore cached pipeline state.")
@click.option("--dry-run", is_flag=True, default=False)
@click.option(
    "--result-out",
    "result_out",
    type=click.Path(dir_okay=False),
    default=None,
    help="Write a neutral ndip-tool-result/1 manifest (params/artifacts/info) "
    "describing the analysis outcome. Schema-agnostic.",
)
def main(
    config: Optional[Path],
    results_dir: Optional[str],
    reports_dir: Optional[str],
    reduction_gate: bool,
    chi2_threshold: float,
    offset_threshold_deg: float,
    llm_commentary: Optional[bool],
    skip_aure_eval: bool,
    skip_partial: bool,
    skip_fit: bool,
    force: bool,
    dry_run: bool,
    result_out: Optional[str],
) -> None:
    """Run the analyzer pipeline for a single sample.

    \b
    CONFIG is a YAML file in the same shape as `create-model --config`:
    a mapping with at least a non-empty `states:` list. The `model_name`
    (or `name`) field is used as the analyzer-pipeline tag (defaulting to
    the YAML stem). Optional pipeline-only fields:
    
    \b
        hypothesis:   passed to `aure evaluate -h`
        theta_offset: pre-computed [{run, offset}, ...] used by the gate

    \b
    Stages:
      1. assess-partial (one per partial-data state)
      2. record YAML-supplied theta offsets
      3. reduction-issue gate (halts on bad overlap χ² or large offset)
      4. create-model --config <yaml>
      5. run-fit <script> --name <model_name>
      6. aure evaluate (optional)
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from analyzer_tools.config_utils import get_config

    if config is None:
        raise click.UsageError("CONFIG is required.")
    if not config.is_file():
        raise click.UsageError(f"CONFIG does not exist: {config}")

    cfg = get_config()
    results_dir = results_dir or cfg.get_results_dir()
    reports_dir = reports_dir or cfg.get_reports_dir()
    models_dir = cfg.get_models_dir()

    spec = parse_sample_file(config)

    os.makedirs(reports_dir, exist_ok=True)
    os.makedirs(results_dir, exist_ok=True)

    state = run_pipeline(
        spec,
        results_root=results_dir,
        reports_root=reports_dir,
        chi2_threshold=chi2_threshold,
        offset_threshold_deg=offset_threshold_deg,
        reduction_gate=reduction_gate,
        llm_commentary=llm_commentary,
        skip_aure_eval=skip_aure_eval,
        dry_run=dry_run,
        force=force,
        skip_partial=skip_partial,
        skip_fit=skip_fit,
    )

    click.echo(f"Pipeline status: {state.status}")

    if result_out is not None:
        from analyzer_tools.result_manifest import write_manifest

        problem_path = Path(results_dir) / spec.tag / "problem.json"
        problem_json = str(problem_path.resolve()) if problem_path.is_file() else None
        # state.status is already one of the manifest status values
        # (ok / dry-run / needs-reprocessing / failed); pass it through.
        messages = None
        if state.status not in ("ok", "dry-run"):
            messages = [{
                "level": "error",
                "text": f"analyze-sample finished with status={state.status}",
            }]
        write_manifest(
            result_out,
            "analyze-sample",
            state.status,
            params={"model_name": spec.tag},
            artifacts={
                "problem_json": problem_json,
                "results_dir": str(Path(results_dir).resolve()),
                "reports_dir": str(Path(reports_dir).resolve()),
                "models_dir": str(Path(models_dir).resolve()),
            },
            info={
                "pipeline_status": state.status,
                "completed_stages": list(state.completed_stages),
            },
            messages=messages,
        )
        click.echo(f"Result manifest written: {Path(result_out).resolve()}")

    if state.status == "needs-reprocessing":
        click.echo(
            f"See {os.path.join(reports_dir, f'sample_{spec.tag}', 'reduction_issues.md')}",
            err=True,
        )
        sys.exit(3)
    if state.status == "failed":
        sys.exit(2)


if __name__ == "__main__":
    main()
