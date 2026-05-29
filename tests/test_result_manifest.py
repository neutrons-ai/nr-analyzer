"""Tests for the neutral ndip-tool-result/1 manifest output.

Covers the shared writer plus the ``--result-out`` flag on plan-data and
analyze-sample. The heavy runtime (LLM / Mantid / refl1d) is stubbed, mirroring
the existing ``test_plan_data_state.py`` / ``test_pipeline_state.py`` patterns.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from analyzer_tools.result_manifest import SCHEMA, build_manifest


def test_build_manifest_shape_and_none_dropping():
    m = build_manifest(
        "simple-reduction",
        "ok",
        params={"template_file": "/t.xml", "theta_offset": None},
        artifacts={"partial_file": "/p.txt", "combined_file": None},
        info={"first_run_of_set": 226642},
    )
    assert m["schema"] == SCHEMA
    assert m["tool"] == "simple-reduction"
    assert m["status"] == "ok"
    assert m["exit_code"] == 0
    # None values dropped
    assert m["params"] == {"template_file": "/t.xml"}
    assert m["artifacts"] == {"partial_file": "/p.txt"}
    assert m["info"] == {"first_run_of_set": 226642}
    assert "messages" not in m


def test_build_manifest_with_messages():
    m = build_manifest("x", "failed", exit_code=2,
                        messages=[{"level": "error", "text": "boom"}])
    assert m["messages"] == [{"level": "error", "text": "boom"}]


def test_plan_data_result_out(tmp_path, monkeypatch):
    import analyzer_tools.analysis.plan_data as mod

    data_file = tmp_path / "data.txt"
    data_file.write_text("# q i di\n0.01 1.0 0.01\n")
    context_file = tmp_path / "context.md"
    context_file.write_text("sample")
    output_dir = tmp_path / "plan"

    fake_result = {
        "sequence_id": "Cu-D2O-226642",
        "sequence_number": 3,
        "sequence_complete": True,
        "create_model_ready": True,
        "config": {
            "model_name": "Cu-D2O-226642",
            "describe": "Cu on Ti on Si",
            "states": [{"name": "s1", "data": ["a.txt"]}],
            "metadata": {"perform_assembly": True},
        },
    }
    monkeypatch.setattr(mod, "call_planner_llm", lambda _: fake_result)
    monkeypatch.setattr(mod, "read_header_lines", lambda p: "")
    monkeypatch.setattr(mod, "list_sibling_files", lambda p: [])
    monkeypatch.setattr(mod, "load_skills", lambda names: {})
    monkeypatch.setattr(mod, "build_user_message", lambda **kw: "msg")

    result_out = tmp_path / "result.json"
    runner = CliRunner()
    res = runner.invoke(
        mod.main,
        [
            str(data_file), str(context_file),
            "--output-dir", str(output_dir),
            "--result-out", str(result_out),
        ],
    )
    assert res.exit_code == 0, res.output
    m = json.loads(result_out.read_text())
    assert m["tool"] == "plan-data"
    assert m["schema"] == SCHEMA
    assert m["status"] == "ok"
    assert m["params"]["model_name"] == "Cu-D2O-226642"
    assert m["params"]["perform_assembly"] is True
    assert m["artifacts"]["job_yaml"].endswith("job_Cu-D2O-226642.yaml")
    assert m["info"]["sequence_id"] == "Cu-D2O-226642"
    assert m["info"]["sequence_number"] == 3


def test_plan_data_llm_options_set_env(tmp_path, monkeypatch):
    import analyzer_tools.analysis.plan_data as mod

    data_file = tmp_path / "data.txt"
    data_file.write_text("x")
    context_file = tmp_path / "context.md"
    context_file.write_text("y")

    captured = {}

    def _capture(_msg):
        import os
        captured["provider"] = os.environ.get("LLM_PROVIDER")
        captured["model"] = os.environ.get("LLM_MODEL")
        captured["base_url"] = os.environ.get("LLM_BASE_URL")
        return {"sequence_id": "s", "config": {"states": [{"name": "a", "data": ["d"]}]}}

    monkeypatch.setattr(mod, "call_planner_llm", _capture)
    monkeypatch.setattr(mod, "read_header_lines", lambda p: "")
    monkeypatch.setattr(mod, "list_sibling_files", lambda p: [])
    monkeypatch.setattr(mod, "load_skills", lambda names: {})
    monkeypatch.setattr(mod, "build_user_message", lambda **kw: "msg")

    runner = CliRunner()
    res = runner.invoke(
        mod.main,
        [
            str(data_file), str(context_file),
            "--output-dir", str(tmp_path / "plan"),
            "--llm-provider", "local",
            "--llm-model", "gpt-4",
            "--llm-base-url", "https://x/v1/",
        ],
    )
    assert res.exit_code == 0, res.output
    assert captured == {"provider": "local", "model": "gpt-4", "base_url": "https://x/v1/"}


def _patch_pipeline_runtime(monkeypatch, status="ok"):
    import analyzer_tools.pipeline as mod

    class _FakeState:
        def __init__(self):
            self.status = status
            self.completed_stages = ["partial", "fit"]

    fake = _FakeState()
    monkeypatch.setattr(mod, "run_pipeline", lambda *a, **kw: fake)
    return fake


_MINIMAL_JOB_YAML = """\
model_name: test_model
describe: synthetic
states:
  - name: state_0
    data: ["data_a.txt"]
"""


def test_analyze_sample_result_out_ok(tmp_path, monkeypatch):
    import analyzer_tools.pipeline as mod

    job_yaml = tmp_path / "job_test.yaml"
    job_yaml.write_text(_MINIMAL_JOB_YAML)
    results_dir = tmp_path / "results"
    (results_dir / "test_model").mkdir(parents=True)
    (results_dir / "test_model" / "problem.json").write_text("{}")

    _patch_pipeline_runtime(monkeypatch, status="ok")

    result_out = tmp_path / "result.json"
    runner = CliRunner()
    res = runner.invoke(
        mod.main,
        [
            str(job_yaml),
            "--results-dir", str(results_dir),
            "--reports-dir", str(tmp_path / "reports"),
            "--result-out", str(result_out),
        ],
    )
    assert res.exit_code == 0, res.output
    m = json.loads(result_out.read_text())
    assert m["tool"] == "analyze-sample"
    assert m["status"] == "ok"
    assert m["params"]["model_name"] == "test_model"
    assert m["artifacts"]["problem_json"].endswith("problem.json")
    assert m["artifacts"]["results_dir"].endswith("results")
    assert m["info"]["pipeline_status"] == "ok"
    assert "messages" not in m


def test_analyze_sample_result_out_failure_status(tmp_path, monkeypatch):
    import analyzer_tools.pipeline as mod

    job_yaml = tmp_path / "job_test.yaml"
    job_yaml.write_text(_MINIMAL_JOB_YAML)
    results_dir = tmp_path / "results"

    _patch_pipeline_runtime(monkeypatch, status="needs-reprocessing")

    result_out = tmp_path / "result.json"
    runner = CliRunner()
    res = runner.invoke(
        mod.main,
        [
            str(job_yaml),
            "--results-dir", str(results_dir),
            "--reports-dir", str(tmp_path / "reports"),
            "--result-out", str(result_out),
        ],
    )
    # analyze-sample exits non-zero on needs-reprocessing; the manifest still records it.
    m = json.loads(result_out.read_text())
    assert m["status"] == "needs-reprocessing"
    assert m["messages"][0]["level"] == "error"
