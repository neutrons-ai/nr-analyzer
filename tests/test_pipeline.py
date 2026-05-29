"""Tests for the analyzer pipeline orchestrator (YAML-config form)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from analyzer_tools import pipeline as pl


def _write_partial_files(directory: Path, set_id: str = "218281") -> list[str]:
    names = [
        f"REFL_{set_id}_1_{set_id}_partial.txt",
        f"REFL_{set_id}_2_{int(set_id)+1}_partial.txt",
        f"REFL_{set_id}_3_{int(set_id)+2}_partial.txt",
    ]
    for n in names:
        (directory / n).write_text("# Q R dR dQ\n0.01 1.0 0.01 0.001\n")
    return names


def _write_yaml_config(
    path: Path,
    *,
    model_name: str,
    data_files: list[str],
    extra: dict | None = None,
) -> Path:
    cfg: dict = {
        "describe": "Test sample",
        "model_name": model_name,
        "states": [{"name": "state1", "data": data_files}],
    }
    if extra:
        cfg.update(extra)
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    return path


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_sample_file_states_yaml(tmp_path: Path) -> None:
    files = _write_partial_files(tmp_path)
    cfg = _write_yaml_config(
        tmp_path / "sample.yaml", model_name="218281_model", data_files=files
    )
    spec = pl.parse_sample_file(cfg)
    assert spec.tag == "218281_model"
    assert spec.describe == "Test sample"
    assert len(spec.states) == 1


def test_parse_sample_file_requires_states(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("describe: only\n")
    with pytest.raises(Exception):
        pl.parse_sample_file(p)


def test_parse_sample_file_rejects_non_mapping(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- just a list\n")
    with pytest.raises(Exception):
        pl.parse_sample_file(p)


def test_classify_states_partial(tmp_path: Path) -> None:
    files = _write_partial_files(tmp_path, set_id="218281")
    cfg = _write_yaml_config(
        tmp_path / "sample.yaml", model_name="m", data_files=files
    )
    spec = pl.parse_sample_file(cfg)
    classes = pl.classify_states(spec)
    assert classes[0]["kind"] == "partial"
    assert classes[0]["set_id"] == "218281"
    assert classes[0]["partial_dir"] == str(tmp_path)


def test_classify_states_combined(tmp_path: Path) -> None:
    name = "REFL_218281_combined_data_auto.txt"
    (tmp_path / name).write_text("# Q R dR dQ\n0.01 1.0 0.01 0.001\n")
    cfg = _write_yaml_config(
        tmp_path / "sample.yaml", model_name="m", data_files=[name]
    )
    spec = pl.parse_sample_file(cfg)
    classes = pl.classify_states(spec)
    assert classes[0]["kind"] == "combined"
    assert classes[0]["set_id"] == "218281"


# ---------------------------------------------------------------------------
# Reduction-issue gate helpers
# ---------------------------------------------------------------------------


def test_detect_reduction_issues_partial_chi2() -> None:
    metrics = {
        "overlaps": [
            {"parts": [1, 2], "chi2": 1.1, "classification": "good"},
            {"parts": [2, 3], "chi2": 10.0, "classification": "poor"},
        ]
    }
    issues = pl.detect_reduction_issues(
        metrics, None, chi2_threshold=3.0, offset_threshold_deg=0.01
    )
    assert len(issues) == 1
    assert issues[0]["type"] == "partial_overlap_chi2"
    assert pl.should_halt(issues)


def test_detect_reduction_issues_theta_offset() -> None:
    theta = [{"run": "218281", "offset": 0.05}, {"run": "218282", "offset": 0.001}]
    issues = pl.detect_reduction_issues(
        None, theta, chi2_threshold=3.0, offset_threshold_deg=0.01
    )
    assert len(issues) == 1
    assert issues[0]["run"] == "218281"


def test_detect_reduction_issues_none() -> None:
    assert (
        pl.detect_reduction_issues(
            None, None, chi2_threshold=3.0, offset_threshold_deg=0.01
        )
        == []
    )


def test_write_reduction_batch_yaml(tmp_path: Path) -> None:
    path = tmp_path / "reduction_batch.yaml"
    pl.write_reduction_batch_yaml(
        path,
        "tag1",
        [{"run": "218281", "offset": 0.05}, {"run": "218282", "offset": 0.02}],
        set_ids=["218281"],
    )
    data = yaml.safe_load(path.read_text())
    assert "jobs" in data
    assert len(data["jobs"]) == 2
    assert data["jobs"][0]["tool"] == "simple-reduction"
    from analyzer_tools.batch import TOOL_COMMANDS

    assert "simple-reduction" in TOOL_COMMANDS


def test_write_reduction_issues_md(tmp_path: Path) -> None:
    path = tmp_path / "issues.md"
    issues = [
        {
            "type": "partial_overlap_chi2",
            "segments": [1, 2],
            "severity": "block",
            "chi2": 7.5,
            "threshold": 3.0,
            "detail": "Overlap between parts 1 and 2 has chi^2=7.50 (> 3.0).",
        }
    ]
    metrics_list = [
        {
            "set_id": "218281",
            "overlaps": [{"parts": [1, 2], "chi2": 7.5, "classification": "poor"}],
        }
    ]
    pl.write_reduction_issues_md(path, "tag1", issues, metrics_list, None)
    content = path.read_text()
    assert "Reprocessing required" in content
    assert "tag1" in content


# ---------------------------------------------------------------------------
# State cache
# ---------------------------------------------------------------------------


def test_pipeline_state_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = pl.PipelineState(tag="m1", completed_stages=["partial"])
    state.save(path)
    loaded = pl.PipelineState.load(path)
    assert loaded is not None
    assert loaded.tag == "m1"
    assert loaded.completed_stages == ["partial"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run(tmp_path: Path) -> None:
    files = _write_partial_files(tmp_path)
    cfg = _write_yaml_config(
        tmp_path / "sample.yaml", model_name="218281_model", data_files=files
    )
    runner = CliRunner()
    result = runner.invoke(
        pl.main,
        [
            str(cfg),
            "--results-dir", str(tmp_path / "results"),
            "--reports-dir", str(tmp_path / "reports"),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Planned pipeline" in result.output
    assert "218281_model" in result.output


def test_cli_rejects_bare_run_id(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        pl.main,
        [
            "218281",
            "--results-dir", str(tmp_path / "results"),
            "--reports-dir", str(tmp_path / "reports"),
        ],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Pipeline orchestration
# ---------------------------------------------------------------------------


def _make_spec(tmp_path: Path, model_name: str = "m1") -> pl.SampleSpec:
    files = _write_partial_files(tmp_path, set_id="218281")
    cfg = _write_yaml_config(
        tmp_path / "sample.yaml", model_name=model_name, data_files=files
    )
    return pl.parse_sample_file(cfg)


def test_pipeline_halts_on_reduction_issue(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    results = tmp_path / "results"

    def fake_partial(set_id, partial_dir, reports_dir, **kwargs):
        return {
            "set_id": set_id,
            "chi2_threshold": 3.0,
            "overlaps": [{"parts": [1, 2], "chi2": 99.0, "classification": "poor"}],
            "worst_chi2": 99.0,
            "status": "poor",
        }

    monkeypatch.setattr(pl, "_run_partial_assessment_for_set", fake_partial)

    spec = _make_spec(tmp_path, model_name="218281_model")
    state = pl.run_pipeline(
        spec,
        results_root=str(results),
        reports_root=str(reports),
    )
    assert state.status == "needs-reprocessing"
    report_dir = reports / "sample_218281_model"
    assert (report_dir / "reduction_issues.md").exists()
    assert (report_dir / "reduction_batch.yaml").exists()
    assert (report_dir / "sample_218281_model.md").exists()
    sample_json = json.loads((report_dir / "sample_218281_model.json").read_text())
    assert sample_json["state"]["status"] == "needs-reprocessing"


def test_pipeline_proceeds_when_gate_disabled(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    results = tmp_path / "results"

    def fake_partial(set_id, partial_dir, reports_dir, **kwargs):
        return {
            "set_id": set_id,
            "overlaps": [{"parts": [1, 2], "chi2": 99.0, "classification": "poor"}],
            "worst_chi2": 99.0,
            "status": "poor",
        }

    called: dict[str, bool] = {}

    def fake_create_model(spec):
        called["create"] = True
        script = tmp_path / f"{spec.model_name}.py"
        script.write_text("# stub\n")
        return script

    def fake_run_fit(script, results_root, reports_root, tag, **kwargs):
        called["fit"] = True
        (results_root / tag).mkdir(parents=True, exist_ok=True)
        return 0, results_root / tag

    monkeypatch.setattr(pl, "_run_partial_assessment_for_set", fake_partial)
    monkeypatch.setattr(pl, "_run_create_model", fake_create_model)
    monkeypatch.setattr(pl, "_run_fit", fake_run_fit)

    spec = _make_spec(tmp_path, model_name="m1")
    state = pl.run_pipeline(
        spec,
        results_root=str(results),
        reports_root=str(reports),
        reduction_gate=False,
        skip_aure_eval=True,
    )
    assert state.status == "ok"
    assert called.get("create") is True
    assert called.get("fit") is True


def test_pipeline_failed_when_create_model_missing(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "reports"
    results = tmp_path / "results"
    monkeypatch.setattr(pl, "_run_partial_assessment_for_set", lambda *a, **k: None)
    monkeypatch.setattr(pl.shutil, "which", lambda _: None)

    spec = _make_spec(tmp_path, model_name="m1")
    state = pl.run_pipeline(
        spec,
        results_root=str(results),
        reports_root=str(reports),
        skip_aure_eval=True,
    )
    assert state.status == "failed"
    assert "error" in state.stage_outputs.get("create_model", {})


# ---------------------------------------------------------------------------
# Resume / .pipeline_state.json
# ---------------------------------------------------------------------------


def _resume_mocks(tmp_path: Path, monkeypatch, counts: dict) -> None:
    """Patch the three stage workers with call-counting stubs."""

    def fake_partial(set_id, partial_dir, reports_dir, **kwargs):
        counts["partial"] = counts.get("partial", 0) + 1
        return None  # no issues -> gate passes

    def fake_create_model(spec):
        counts["create"] = counts.get("create", 0) + 1
        script = tmp_path / f"{spec.model_name}.py"
        script.write_text("# stub\n")
        return script

    def fake_run_fit(script, results_root, reports_root, tag, **kwargs):
        counts["fit"] = counts.get("fit", 0) + 1
        (results_root / tag).mkdir(parents=True, exist_ok=True)
        return 0, results_root / tag

    monkeypatch.setattr(pl, "_run_partial_assessment_for_set", fake_partial)
    monkeypatch.setattr(pl, "_run_create_model", fake_create_model)
    monkeypatch.setattr(pl, "_run_fit", fake_run_fit)


def _run_kwargs(tmp_path: Path) -> dict:
    return dict(
        results_root=str(tmp_path / "results"),
        reports_root=str(tmp_path / "reports"),
        reduction_gate=False,
        skip_aure_eval=True,
    )


def test_pipeline_resume_skips_completed_stages(tmp_path: Path, monkeypatch) -> None:
    counts: dict = {}
    _resume_mocks(tmp_path, monkeypatch, counts)
    spec = _make_spec(tmp_path, model_name="m1")
    kw = _run_kwargs(tmp_path)

    s1 = pl.run_pipeline(spec, **kw)
    assert s1.status == "ok"
    assert counts == {"partial": 1, "create": 1, "fit": 1}

    state_file = tmp_path / "reports" / "sample_m1" / ".pipeline_state.json"
    assert state_file.exists()
    completed = set(json.loads(state_file.read_text())["completed_stages"])
    assert {"partial", "create_model", "fit"} <= completed

    # Second run with the cache present must short-circuit every stage.
    before = dict(counts)
    s2 = pl.run_pipeline(spec, **kw)
    assert s2.status == "ok"
    assert counts == before  # nothing re-ran


def test_pipeline_force_reruns_all_stages(tmp_path: Path, monkeypatch) -> None:
    counts: dict = {}
    _resume_mocks(tmp_path, monkeypatch, counts)
    spec = _make_spec(tmp_path, model_name="m1")
    kw = _run_kwargs(tmp_path)

    pl.run_pipeline(spec, **kw)
    before = dict(counts)
    pl.run_pipeline(spec, force=True, **kw)

    assert counts["create"] == before["create"] + 1
    assert counts["fit"] == before["fit"] + 1
    assert counts["partial"] == before["partial"] + 1


def test_pipeline_resume_regenerates_stale_cached_script(tmp_path: Path, monkeypatch) -> None:
    counts: dict = {}
    _resume_mocks(tmp_path, monkeypatch, counts)
    spec = _make_spec(tmp_path, model_name="m1")
    kw = _run_kwargs(tmp_path)

    pl.run_pipeline(spec, **kw)
    assert counts["create"] == 1
    before_fit = counts["fit"]

    # The cached model script vanishes -> resume must regenerate it even though
    # the create_model stage is marked complete (exercises the stale-cache branch).
    (tmp_path / "m1.py").unlink()

    s2 = pl.run_pipeline(spec, **kw)
    assert s2.status == "ok"
    assert counts["create"] == 2          # regenerated
    assert counts["fit"] == before_fit    # fit already done -> not re-run
