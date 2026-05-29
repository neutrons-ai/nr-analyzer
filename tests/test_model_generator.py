"""Tests for analyzer_tools.analysis.model_generator."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List

import pytest
from click.testing import CliRunner

from analyzer_tools.analysis import create_model as cm
from analyzer_tools.analysis import model_generator as mg


SAMPLE_DATA_DIR = Path(__file__).parent / "sample_data"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def model_spec_dict() -> Dict[str, Any]:
    """Stable spec for a Cu/Ti on Si in D2O sample."""
    return {
        "ambient": {
            "name": "D2O",
            "sld": 6.19,
            "sld_min": 5.69,
            "sld_max": 6.69,
            "roughness": 10.0,
            "roughness_min": 1.0,
            "roughness_max": 25.0,
        },
        "substrate": {
            "name": "Si",
            "sld": 2.07,
            "roughness_min": 0.0,
            "roughness_max": 15.0,
        },
        "layers": [
            {
                "name": "CuOx",
                "sld": 5.0,
                "thickness": 30.0,
                "roughness": 10.0,
                "thickness_min": 5.0,
                "thickness_max": 200.0,
                "sld_min": 3.0,
                "sld_max": 7.0,
                "roughness_min": 5.0,
                "roughness_max": 30.0,
            },
            {
                "name": "Cu",
                "sld": 6.4,
                "thickness": 500.0,
                "roughness": 5.0,
                "thickness_min": 250.0,
                "thickness_max": 1000.0,
                "sld_min": 5.0,
                "sld_max": 7.5,
                "roughness_min": 1.0,
                "roughness_max": 12.0,
            },
            {
                "name": "Ti",
                "sld": -1.95,
                "thickness": 35.0,
                "roughness": 5.0,
                "thickness_min": 15.0,
                "thickness_max": 60.0,
                "sld_min": -5.0,
                "sld_max": 1.0,
                "roughness_min": 5.0,
                "roughness_max": 30.0,
            },
        ],
        "intensity": {"value": 1.0, "min": 0.95, "max": 1.05},
        "back_reflection": False,
        "shared_parameters": [
            "Cu.material.rho",
            "Cu.interface",
            "Ti.thickness",
            "Ti.material.rho",
            "Ti.interface",
        ],
    }


@pytest.fixture
def model_spec(model_spec_dict: Dict[str, Any]) -> mg.ModelSpec:
    return mg.model_spec_from_dict(model_spec_dict)


# ---------------------------------------------------------------------------
# detect_case
# ---------------------------------------------------------------------------


def test_detect_case_single_combined(tmp_path: Path) -> None:
    f = tmp_path / "REFL_218281_combined_data_auto.txt"
    f.touch()
    assert mg.detect_case([f]) == mg.CASE_1


def test_detect_case_partial_set(tmp_path: Path) -> None:
    files = [
        tmp_path / "REFL_218281_1_218281_partial.txt",
        tmp_path / "REFL_218281_2_218282_partial.txt",
        tmp_path / "REFL_218281_3_218283_partial.txt",
    ]
    for f in files:
        f.touch()
    assert mg.detect_case(files) == mg.CASE_2


def test_detect_case_multiple_combined(tmp_path: Path) -> None:
    files = [
        tmp_path / "REFL_226642_combined_data_auto.txt",
        tmp_path / "REFL_226652_combined_data_auto.txt",
    ]
    for f in files:
        f.touch()
    assert mg.detect_case(files) == mg.CASE_3


def test_detect_case_rejects_mixed(tmp_path: Path) -> None:
    a = tmp_path / "REFL_218281_combined_data_auto.txt"
    b = tmp_path / "REFL_218281_1_218281_partial.txt"
    a.touch()
    b.touch()
    with pytest.raises(ValueError, match="Mixing"):
        mg.detect_case([a, b])


def test_detect_case_partial_multiple_sets_rejected(tmp_path: Path) -> None:
    a = tmp_path / "REFL_218281_1_218281_partial.txt"
    b = tmp_path / "REFL_218500_1_218500_partial.txt"
    a.touch()
    b.touch()
    with pytest.raises(ValueError, match="multiple set_ids"):
        mg.detect_case([a, b])


def test_detect_case_single_partial_rejected(tmp_path: Path) -> None:
    a = tmp_path / "REFL_218281_1_218281_partial.txt"
    a.touch()
    with pytest.raises(ValueError, match="at least two"):
        mg.detect_case([a])


# ---------------------------------------------------------------------------
# parse_refl_header
# ---------------------------------------------------------------------------


def test_parse_refl_header_combined_has_three_runs() -> None:
    header = mg.parse_refl_header(
        SAMPLE_DATA_DIR / "REFL_218281_combined_data_auto.txt"
    )
    assert header["experiment"] == "IPTS-34347"
    assert header["run"] == "218281"
    assert header["theta_offset"] == 0.0
    assert len(header["runs"]) == 3
    two_thetas = [r["two_theta"] for r in header["runs"]]
    assert two_thetas[0] == pytest.approx(0.899996)
    assert header["runs"][0]["theta"] == pytest.approx(0.899996 / 2)


def test_parse_refl_header_partial_has_one_run() -> None:
    header = mg.parse_refl_header(
        SAMPLE_DATA_DIR / "partial" / "REFL_218281_1_218281_partial.txt"
    )
    assert len(header["runs"]) == 1
    assert header["runs"][0]["two_theta"] == pytest.approx(0.899996)


# ---------------------------------------------------------------------------
# Rendering (parse as Python, inspect content)
# ---------------------------------------------------------------------------


def test_render_case1_is_valid_python_with_qprobe(
    model_spec: mg.ModelSpec, tmp_path: Path
) -> None:
    data_file = tmp_path / "REFL_218281_combined_data_auto.txt"
    script = mg.render_case1_script(model_spec, data_file, model_name="cu_thf")
    ast.parse(script)
    assert "QProbe" in script
    assert "create_fit_experiment" in script
    assert "FitProblem(experiment)" in script
    assert "make_probe" not in script


def test_render_case2_uses_make_probe(model_spec: mg.ModelSpec) -> None:
    files = [f"REFL_218281_{i}_21828{i}_partial.txt" for i in range(1, 4)]
    thetas = [0.45, 1.2, 3.5]
    script = mg.render_case2_script(
        model_spec, files, thetas, model_name="cu_thf_partial"
    )
    ast.parse(script)
    assert "from refl1d.probe import make_probe" in script
    assert "create_probe(data_file, theta)" in script
    assert "create_sample()" in script
    # Three probes / experiments, one shared sample
    assert "probe1 = create_probe(data_file1, theta=0.45)" in script
    assert "probe3 = create_probe(data_file3, theta=3.5)" in script
    assert "experiment3 = Experiment(probe=probe3, sample=sample)" in script


def test_render_case3_emits_constraints_and_list_fitproblem(
    model_spec: mg.ModelSpec,
) -> None:
    files = [
        "REFL_226642_combined_data_auto.txt",
        "REFL_226652_combined_data_auto.txt",
    ]
    script = mg.render_case3_script(model_spec, files, model_name="corefine")
    ast.parse(script)
    assert "FitProblem([experiment, experiment2])" in script
    # Constraints tie shared parameters from experiment2 to experiment
    assert (
        'experiment2.sample[\'Cu\'].material.rho = '
        'experiment.sample[\'Cu\'].material.rho' in script
    )
    assert (
        'experiment2.sample[\'Ti\'].thickness = '
        'experiment.sample[\'Ti\'].thickness' in script
    )
    # Cu.material.rho IS shared; it should NOT be freely re-ranged on experiment2.
    # Intensity should remain per-experiment (each experiment has its own probe).
    assert "create_fit_experiment" in script


# ---------------------------------------------------------------------------
# LLM JSON parsing / retry logic
# ---------------------------------------------------------------------------


class _FakeLLMReply:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    """Minimal LangChain-style chat model stub."""

    def __init__(self, replies: List[str]) -> None:
        self._replies = list(replies)
        self.calls: List[List[Dict[str, str]]] = []

    def invoke(self, messages: List[Dict[str, str]]):
        self.calls.append(list(messages))
        return _FakeLLMReply(self._replies.pop(0))


def test_call_llm_parses_plain_json(model_spec_dict: Dict[str, Any]) -> None:
    import json as _json

    llm = _FakeLLM([_json.dumps(model_spec_dict)])
    spec = mg.call_llm_for_model_spec([{"role": "user", "content": "x"}], llm=llm)
    assert spec.layers[0].name == "CuOx"
    assert spec.shared_parameters[0] == "Cu.material.rho"
    assert len(llm.calls) == 1


def test_call_llm_parses_fenced_json(model_spec_dict: Dict[str, Any]) -> None:
    import json as _json

    fenced = "```json\n" + _json.dumps(model_spec_dict) + "\n```"
    llm = _FakeLLM([fenced])
    spec = mg.call_llm_for_model_spec([{"role": "user", "content": "x"}], llm=llm)
    assert spec.ambient.name == "D2O"


def test_call_llm_retries_once_then_succeeds(
    model_spec_dict: Dict[str, Any],
) -> None:
    import json as _json

    llm = _FakeLLM(["not JSON at all", _json.dumps(model_spec_dict)])
    spec = mg.call_llm_for_model_spec([{"role": "user", "content": "x"}], llm=llm)
    assert spec.layers[0].name == "CuOx"
    # Two invocations — the second one carried the error-feedback message.
    assert len(llm.calls) == 2
    last_user = llm.calls[1][-1]
    assert last_user["role"] == "user"
    assert "could not be parsed" in last_user["content"]


def test_call_llm_gives_up_after_one_retry() -> None:
    llm = _FakeLLM(["not JSON", "still not JSON"])
    with pytest.raises(mg.LLMResponseError):
        mg.call_llm_for_model_spec([{"role": "user", "content": "x"}], llm=llm)
    assert len(llm.calls) == 2


# ---------------------------------------------------------------------------
# CLI (Mode B)
# ---------------------------------------------------------------------------


def test_cli_mode_b_calls_llm_and_writes_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_spec_dict: Dict[str, Any],
) -> None:
    """Mode B: --config with a single-state shape → script written."""
    import yaml

    src = SAMPLE_DATA_DIR / "REFL_218281_combined_data_auto.txt"
    data_path = tmp_path / "REFL_218281_combined_data_auto.txt"
    data_path.write_bytes(src.read_bytes())

    captured: Dict[str, Any] = {}

    def fake_call(messages, *, llm=None, max_retries=1):  # noqa: ARG001
        captured["messages"] = messages
        return mg.model_spec_from_dict(model_spec_dict)

    monkeypatch.setattr(mg, "call_llm_for_model_spec", fake_call)

    out_path = tmp_path / "models" / "generated.py"
    config = {
        "describe": "2 nm CuOx / 50 nm Cu / 3.5 nm Ti on Si in D2O",
        "model_name": "gen_cu",
        "out": str(out_path),
        "states": [
            {"name": "run1", "data": [data_path.name]},
        ],
    }
    cfg_path = tmp_path / "model.yaml"
    cfg_path.write_text(yaml.safe_dump(config))

    runner = CliRunner()
    result = runner.invoke(cm.main, ["--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert out_path.exists()
    content = out_path.read_text()
    assert "FitProblem(" in content
    # LLM saw a user message with the description.
    assert any(
        "2 nm CuOx" in m.get("content", "") for m in captured["messages"]
    )


def test_cli_config_file_drives_mode_b(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_spec_dict: Dict[str, Any],
) -> None:
    """A YAML config with states: drives Mode B end-to-end."""
    import yaml

    src = SAMPLE_DATA_DIR / "REFL_218281_combined_data_auto.txt"
    data_path = tmp_path / "REFL_218281_combined_data_auto.txt"
    data_path.write_bytes(src.read_bytes())

    out_path = tmp_path / "models" / "from_config.py"
    config = {
        "describe": "Cu/Ti on Si in D2O",
        "model_name": "from_cfg",
        "out": str(out_path),
        "states": [
            {"name": "only", "data": [str(data_path)]},
        ],
    }
    cfg_path = tmp_path / "model.yaml"
    cfg_path.write_text(yaml.safe_dump(config))

    monkeypatch.setattr(
        mg,
        "call_llm_for_model_spec",
        lambda messages, **kw: mg.model_spec_from_dict(model_spec_dict),
    )

    runner = CliRunner()
    result = runner.invoke(cm.main, ["--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    assert out_path.exists()
    assert "FitProblem(" in out_path.read_text()


def test_cli_rejects_both_modes_simultaneously(tmp_path: Path) -> None:
    """Mode A SOURCE and Mode B --config are mutually exclusive."""
    import yaml

    json_path = tmp_path / "x.json"
    json_path.write_text('{"substrate": {"name": "Si", "sld": 2.07}, '
                         '"ambient": {"name": "D2O", "sld": 6.19}, '
                         '"layers": []}')
    cfg_path = tmp_path / "m.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "describe": "x",
        "states": [{"name": "a", "data": ["foo.txt"]}],
    }))

    runner = CliRunner()
    result = runner.invoke(
        cm.main, [str(json_path), "--config", str(cfg_path)]
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


def test_cli_rejects_flat_data_in_config(tmp_path: Path) -> None:
    """Top-level 'data:' in a config is rejected — states-only Mode B."""
    import yaml

    cfg_path = tmp_path / "m.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "describe": "x",
        "data": ["some_file.txt"],
    }))
    runner = CliRunner()
    result = runner.invoke(cm.main, ["--config", str(cfg_path)])
    assert result.exit_code != 0
    assert "unsupported top-level key" in result.output


# ---------------------------------------------------------------------------
# Multi-state (YAML "states:" form)
# ---------------------------------------------------------------------------


def _copy_partial_set(src_dir: Path, dst_dir: Path, set_id: str) -> List[Path]:
    """Copy REFL_{set_id}_{part}_*_partial.txt files and return the dst paths."""
    out: List[Path] = []
    for p in sorted(src_dir.iterdir()):
        if p.name.startswith(f"REFL_{set_id}_") and p.name.endswith("_partial.txt"):
            d = dst_dir / p.name
            d.write_bytes(p.read_bytes())
            out.append(d)
    return out


def test_build_state_specs_combined_only(tmp_path: Path) -> None:
    combined = tmp_path / "REFL_226642_combined_data_auto.txt"
    combined.touch()
    states = mg.build_state_specs(
        [{"name": "a", "data": [combined.name]}],
        base_dir=tmp_path,
    )
    assert len(states) == 1
    assert states[0].kind == mg.STATE_COMBINED
    assert states[0].data_files[0] == combined


def test_build_state_specs_partials_reads_thetas() -> None:
    partials = _list_sample_partials()
    states = mg.build_state_specs([
        {"name": "run1", "data": [str(p) for p in partials]},
    ])
    assert states[0].kind == mg.STATE_PARTIALS
    assert len(states[0].thetas) == len(partials)
    assert all(isinstance(t, float) for t in states[0].thetas)


def test_build_state_specs_rejects_mixed_within_state(tmp_path: Path) -> None:
    combined = tmp_path / "REFL_226642_combined_data_auto.txt"
    partial = tmp_path / "REFL_226642_1_226642_partial.txt"
    # Need real header for partial; steal from samples.
    combined.touch()
    partial.write_bytes(
        (SAMPLE_DATA_DIR / "partial" / "REFL_218281_1_218281_partial.txt").read_bytes()
    )
    with pytest.raises(ValueError, match="cannot mix"):
        mg.build_state_specs([
            {"name": "bad", "data": [combined.name, partial.name]}
        ], base_dir=tmp_path)


def test_build_state_specs_theta_offset_requires_partials(tmp_path: Path) -> None:
    combined = tmp_path / "REFL_226642_combined_data_auto.txt"
    combined.touch()
    with pytest.raises(ValueError, match="only meaningful for multi-segment"):
        mg.build_state_specs([
            {
                "name": "a",
                "data": [combined.name],
                "theta_offset": {"min": -0.02, "max": 0.02},
            }
        ], base_dir=tmp_path)


def test_resolve_shared_parameters_whitelist(model_spec: mg.ModelSpec) -> None:
    out = mg.resolve_shared_parameters(
        model_spec, shared=["Cu.thickness", "Ti.material.rho"]
    )
    assert out == ["Cu.thickness", "Ti.material.rho"]


def test_resolve_shared_parameters_blacklist(model_spec: mg.ModelSpec) -> None:
    out = mg.resolve_shared_parameters(
        model_spec, unshared=["CuOx.thickness"]
    )
    assert "CuOx.thickness" not in out
    assert "Cu.thickness" in out
    # Ambient and intensity are not in the default either way.
    assert "D2O.material.rho" not in out


def test_resolve_shared_parameters_mutual_exclusion(
    model_spec: mg.ModelSpec,
) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        mg.resolve_shared_parameters(
            model_spec, shared=["Cu.thickness"], unshared=["Ti.thickness"]
        )


def _list_sample_partials() -> List[Path]:
    return sorted(
        (SAMPLE_DATA_DIR / "partial").glob("REFL_218281_*_partial.txt")
    )


def test_render_states_combined_plus_partials(
    model_spec: mg.ModelSpec, tmp_path: Path
) -> None:
    # State A: 3 partials; State B: one (synthetic) combined file.
    partials = _list_sample_partials()
    combined = tmp_path / "REFL_999999_combined_data_auto.txt"
    combined.touch()

    states = mg.build_state_specs(
        [
            {
                "name": "run_a",
                "data": [str(p) for p in partials],
                "theta_offset": {"init": 0.0, "min": -0.02, "max": 0.02},
                "sample_broadening": {"init": 0.0, "min": 0.0, "max": 0.01},
            },
            {"name": "run_b", "data": [str(combined)]},
        ]
    )

    shared = mg.resolve_shared_parameters(
        model_spec, shared=["Cu.thickness", "Cu.material.rho"]
    )
    script = mg.render_states_script(
        model_spec, states, model_name="cu_multi", shared_parameters=shared
    )
    ast.parse(script)

    # Per-state variables.
    assert "sample_run_a = create_sample(back_reflection=False)" in script
    assert "sample_run_b = create_sample(back_reflection=False)" in script
    # Multi-experiment FitProblem with all 4 experiments (3 partials + 1 combined).
    assert "FitProblem([" in script
    for name in (
        "experiment_run_a_1",
        "experiment_run_a_2",
        "experiment_run_a_3",
        "experiment_run_b",
    ):
        assert name in script

    # theta_offset / sample_broadening only on state a, shared across its probes.
    assert "theta_offset_run_a" in script
    assert "sample_broadening_run_a" in script
    for i in (1, 2, 3):
        assert f"probe_run_a_{i}.theta_offset = theta_offset_run_a" in script
        assert (
            f"probe_run_a_{i}.sample_broadening = sample_broadening_run_a" in script
        )
    # State b has no theta_offset / sample_broadening assignment.
    assert "probe_run_b.theta_offset" not in script

    # Shared parameter constraint from state a's sample onto state b's sample.
    assert (
        "sample_run_b['Cu'].thickness = sample_run_a['Cu'].thickness" in script
    )
    assert (
        "sample_run_b['Cu'].material.rho = sample_run_a['Cu'].material.rho"
        in script
    )

    # Probe helpers are both present because we have both kinds of states.
    assert "def create_q_probe" in script
    assert "def create_angle_probe" in script
    assert "from refl1d.probe import make_probe" in script


def test_cli_states_yaml_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_spec_dict: Dict[str, Any],
) -> None:
    import yaml

    partials = _list_sample_partials()
    # Copy partial files so the config can use relative paths.
    rel_partials: List[str] = []
    for p in partials:
        dst = tmp_path / p.name
        dst.write_bytes(p.read_bytes())
        rel_partials.append(p.name)

    combined = tmp_path / "REFL_999999_combined_data_auto.txt"
    combined.touch()

    out_path = tmp_path / "models" / "multi.py"
    config = {
        "describe": "CuOx / Cu / Ti on Si in D2O",
        "model_name": "multi",
        "out": str(out_path),
        "unshared_parameters": ["CuOx.thickness"],
        "states": [
            {
                "name": "run_a",
                "data": rel_partials,
                "theta_offset": {"init": 0.0, "min": -0.02, "max": 0.02},
            },
            {
                "name": "run_b",
                "data": [combined.name],
            },
        ],
    }
    cfg_path = tmp_path / "model.yaml"
    cfg_path.write_text(yaml.safe_dump(config))

    monkeypatch.setattr(
        mg,
        "call_llm_for_model_spec",
        lambda messages, **kw: mg.model_spec_from_dict(model_spec_dict),
    )

    runner = CliRunner()
    result = runner.invoke(cm.main, ["--config", str(cfg_path)])
    assert result.exit_code == 0, result.output
    content = out_path.read_text()
    ast.parse(content)
    # unshared_parameters removes CuOx.thickness from the default share set.
    assert (
        "sample_run_b['CuOx'].thickness = sample_run_a['CuOx'].thickness"
        not in content
    )
    # Cu.thickness is still shared (it's in the default set).
    assert (
        "sample_run_b['Cu'].thickness = sample_run_a['Cu'].thickness" in content
    )


def test_state_extra_description_in_llm_prompt(tmp_path: Path) -> None:
    """``extra_description`` is appended to the per-state prompt block."""
    combined_a = tmp_path / "REFL_111111_combined_data_auto.txt"
    combined_b = tmp_path / "REFL_222222_combined_data_auto.txt"
    combined_a.touch()
    combined_b.touch()

    states = mg.build_state_specs([
        {
            "name": "D2O",
            "data": [combined_a.name],
            "extra_description": "ambient is D2O (SLD ~6.4)",
        },
        {
            "name": "H2O",
            "data": [combined_b.name],
            "extra_description": "ambient is H2O (SLD ~-0.56)",
        },
    ], base_dir=tmp_path)

    assert states[0].extra_description == "ambient is D2O (SLD ~6.4)"
    assert states[1].extra_description == "ambient is H2O (SLD ~-0.56)"

    messages = mg.build_states_llm_prompt("Cu / Ti on Si", states)
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "ambient is D2O (SLD ~6.4)" in user_msg
    assert "ambient is H2O (SLD ~-0.56)" in user_msg


def test_state_extra_description_must_be_string(tmp_path: Path) -> None:
    combined = tmp_path / "REFL_111111_combined_data_auto.txt"
    combined.touch()
    with pytest.raises(ValueError, match="extra_description"):
        mg.build_state_specs([
            {
                "name": "x",
                "data": [combined.name],
                "extra_description": 42,
            }
        ], base_dir=tmp_path)


def test_back_reflection_per_state(
    model_spec: mg.ModelSpec, tmp_path: Path
) -> None:
    """State-level back_reflection controls stack ORIENTATION only.

    No ``probe.back_reflectivity`` line should ever be emitted. The renderer
    passes the flag into ``create_sample(back_reflection=...)`` per state.
    """
    partials = _list_sample_partials()
    combined = tmp_path / "REFL_999999_combined_data_auto.txt"
    combined.touch()

    states = mg.build_state_specs(
        [
            {
                "name": "front",
                "data": [str(combined)],
                "back_reflection": False,
            },
            {
                "name": "back",
                "data": [str(p) for p in partials],
                "back_reflection": True,
            },
        ]
    )
    assert states[0].back_reflection is False
    assert states[1].back_reflection is True

    script = mg.render_states_script(
        model_spec, states, model_name="br", shared_parameters=[]
    )
    ast.parse(script)
    # The probe.back_reflectivity flag must never be assigned — stack order
    # encodes beam direction instead. (The helper docstring may mention the
    # word, so we check specifically for ``.back_reflectivity =`` assignments.)
    assert ".back_reflectivity =" not in script
    # Each state receives its own create_sample(...) call with the flag.
    assert "sample_front = create_sample(back_reflection=False)" in script
    assert "sample_back = create_sample(back_reflection=True)" in script


def test_back_reflection_inherits_from_spec(
    model_spec_dict: Dict[str, Any], tmp_path: Path
) -> None:
    """When a state omits back_reflection, spec.back_reflection is used."""
    combined = tmp_path / "REFL_999999_combined_data_auto.txt"
    combined.touch()

    spec_dict = dict(model_spec_dict)
    spec_dict["back_reflection"] = True
    spec = mg.model_spec_from_dict(spec_dict)

    states = mg.build_state_specs(
        [{"name": "s1", "data": [str(combined)]}]  # no back_reflection key
    )
    assert states[0].back_reflection is None

    script = mg.render_states_script(
        spec, states, model_name="br2", shared_parameters=[]
    )
    assert ".back_reflectivity =" not in script
    assert "sample_s1 = create_sample(back_reflection=True)" in script


def test_back_reflection_rejects_non_bool(tmp_path: Path) -> None:
    combined = tmp_path / "REFL_999999_combined_data_auto.txt"
    combined.touch()
    with pytest.raises(ValueError, match="back_reflection"):
        mg.build_state_specs(
            [{"name": "x", "data": [str(combined)], "back_reflection": "yes"}]
        )


def test_render_case1_back_reflection_emits_probe_flag(
    model_spec_dict: Dict[str, Any], tmp_path: Path
) -> None:
    """Case-1 with back_reflection=True emits ambient-first stack, no probe flag."""
    spec_dict = dict(model_spec_dict)
    spec_dict["back_reflection"] = True
    spec = mg.model_spec_from_dict(spec_dict)
    data_file = tmp_path / "REFL_218281_combined_data_auto.txt"
    script = mg.render_case1_script(spec, data_file, model_name="c1br")
    ast.parse(script)
    # With back_reflection=True the stack is ambient-first (D2O | ... | Si)
    # which, by refl1d's convention, means beam enters through Si. No probe
    # flag is ever set.
    assert ".back_reflectivity =" not in script
    # D2O must appear before Si on the sample = ... line.
    stack_line = next(ln for ln in script.splitlines() if "sample =" in ln)
    assert stack_line.index("D2O") < stack_line.index("Si")


def test_render_case1_front_reflection_reverses_stack(
    model_spec_dict: Dict[str, Any], tmp_path: Path
) -> None:
    """Case-1 with back_reflection=False emits substrate-first (reversed) stack."""
    spec_dict = dict(model_spec_dict)
    spec_dict["back_reflection"] = False
    spec = mg.model_spec_from_dict(spec_dict)
    data_file = tmp_path / "REFL_218281_combined_data_auto.txt"
    script = mg.render_case1_script(spec, data_file, model_name="c1fr")
    ast.parse(script)
    assert ".back_reflectivity =" not in script
    stack_line = next(ln for ln in script.splitlines() if "sample =" in ln)
    # Reversed: Si comes before D2O.
    assert stack_line.index("Si") < stack_line.index("D2O")


def test_data_dir_variable_case1(
    model_spec: mg.ModelSpec, tmp_path: Path
) -> None:
    """data_dir emits DATA_DIR and rewrites file paths via os.path.join."""
    data_dir_abs = tmp_path / "data"
    data_dir_abs.mkdir()
    data_file = data_dir_abs / "REFL_218281_combined_data_auto.txt"
    data_file.touch()
    script = mg.render_case1_script(
        model_spec,
        data_file,
        model_name="d1",
        data_dir="data",
        data_dir_abs=str(data_dir_abs),
    )
    ast.parse(script)
    assert "DATA_DIR = 'data'" in script
    assert (
        "data_file = os.path.join(DATA_DIR, "
        "'REFL_218281_combined_data_auto.txt')"
    ) in script
    # Ensure the raw absolute path is NOT embedded in the script.
    assert str(data_file) not in script


def test_data_dir_variable_states(
    model_spec: mg.ModelSpec, tmp_path: Path
) -> None:
    """States renderer threads data_dir through every create_*_probe call."""
    partials = _list_sample_partials()
    data_dir_abs = partials[0].parent.resolve()
    states = mg.build_state_specs(
        [{"name": "s1", "data": [str(p) for p in partials]}]
    )
    script = mg.render_states_script(
        model_spec,
        states,
        model_name="ds",
        shared_parameters=[],
        data_dir="data/partial",
        data_dir_abs=str(data_dir_abs),
    )
    ast.parse(script)
    assert "DATA_DIR = 'data/partial'" in script
    for p in partials:
        assert f"'{p.name}'" in script  # basename appears under os.path.join
        # Absolute path should have been stripped.
        assert str(p.parent) not in script or f"os.path.join(DATA_DIR, '{p.name}')" in script


def test_data_dir_omitted_keeps_absolute_paths(
    model_spec: mg.ModelSpec, tmp_path: Path
) -> None:
    """With no data_dir, behaviour is unchanged (no DATA_DIR line)."""
    data_file = tmp_path / "REFL_218281_combined_data_auto.txt"
    data_file.touch()
    script = mg.render_case1_script(model_spec, data_file, model_name="nd")
    ast.parse(script)
    assert "DATA_DIR" not in script
    assert f"data_file = {str(data_file)!r}" in script


def test_data_dir_out_of_tree_falls_back(
    model_spec: mg.ModelSpec, tmp_path: Path
) -> None:
    """Files that don't live under data_dir keep their absolute path."""
    inside = tmp_path / "inside"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    out_file = outside / "REFL_218281_combined_data_auto.txt"
    out_file.touch()
    script = mg.render_case1_script(
        model_spec,
        out_file,
        model_name="oot",
        data_dir="inside",
        data_dir_abs=str(inside),
    )
    ast.parse(script)
    assert "DATA_DIR = 'inside'" in script
    assert f"data_file = {str(out_file)!r}" in script


def test_cli_states_rejects_shared_and_unshared(tmp_path: Path) -> None:
    import yaml

    partials = _list_sample_partials()
    rel_partials = []
    for p in partials:
        dst = tmp_path / p.name
        dst.write_bytes(p.read_bytes())
        rel_partials.append(p.name)

    config = {
        "describe": "x",
        "states": [{"name": "a", "data": rel_partials}],
        "shared_parameters": ["Cu.thickness"],
        "unshared_parameters": ["Ti.thickness"],
    }
    cfg_path = tmp_path / "model.yaml"
    cfg_path.write_text(yaml.safe_dump(config))

    runner = CliRunner()
    result = runner.invoke(cm.main, ["--config", str(cfg_path)])
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output


# ── Layer-name validation for shared/unshared lists ───────────────


def test_resolve_shared_parameters_rejects_unknown_layer(
    model_spec: mg.ModelSpec,
) -> None:
    """A shared_parameters list referencing a layer the LLM didn't pick raises."""
    with pytest.raises(ValueError, match="layer prefix"):
        mg.resolve_shared_parameters(
            model_spec, shared=["Copper.thickness"]
        )


def test_resolve_unshared_parameters_rejects_unknown_layer(
    model_spec: mg.ModelSpec,
) -> None:
    with pytest.raises(ValueError, match="layer prefix"):
        mg.resolve_shared_parameters(
            model_spec, unshared=["Copper.thickness"]
        )


def test_resolve_shared_parameters_accepts_substrate(
    model_spec: mg.ModelSpec,
) -> None:
    """Substrate.interface is a valid shared path (it's the substrate's name)."""
    out = mg.resolve_shared_parameters(
        model_spec, shared=[f"{model_spec.substrate.name}.interface"]
    )
    assert out == [f"{model_spec.substrate.name}.interface"]


def test_layer_names_in_paths_extracts_unique_prefixes() -> None:
    out = mg._layer_names_in_paths([
        "Cu.thickness", "Cu.material.rho", "Ti.interface", "garbage",
    ])
    assert out == ["Cu", "Ti"]


def test_build_states_llm_prompt_injects_required_names(tmp_path: Path) -> None:
    partials = _list_sample_partials()
    states = mg.build_state_specs(
        [{"name": "a", "data": [str(p) for p in partials]}]
    )
    msgs = mg.build_states_llm_prompt(
        "desc", states, required_layer_names=["Cu", "Ti"]
    )
    user = msgs[-1]["content"]
    assert "REQUIRED LAYER NAMES" in user
    assert "'Cu'" in user and "'Ti'" in user


def test_build_states_llm_prompt_no_constraint_when_none(tmp_path: Path) -> None:
    partials = _list_sample_partials()
    states = mg.build_state_specs(
        [{"name": "a", "data": [str(p) for p in partials]}]
    )
    msgs = mg.build_states_llm_prompt("desc", states)
    assert "REQUIRED LAYER NAMES" not in msgs[-1]["content"]


# ---------------------------------------------------------------------------
# Layer-name sanitization
# ---------------------------------------------------------------------------


def test_layer_names_sanitized_to_python_identifiers(tmp_path: Path) -> None:
    """LLM names with spaces/punctuation must not break the generated script.

    Regression: the LLM was free to emit names like ``"Copper oxide"``, which
    rendered as ``Copper oxide(15.0, 8.0)`` in the ``sample = ... | ...``
    line and produced a SyntaxError at fit time.
    """
    spec_dict: Dict[str, Any] = {
        "ambient": {"name": "D2O", "sld": 6.19},
        "substrate": {"name": "Silicon", "sld": 2.07},
        "layers": [
            {
                "name": "Copper oxide",
                "sld": 5.0,
                "thickness": 15.0,
                "roughness": 8.0,
            },
            {
                "name": "Copper",
                "sld": 6.55,
                "thickness": 500.0,
                "roughness": 10.0,
            },
            {
                "name": "Titanium adhesion",
                "sld": -1.95,
                "thickness": 30.0,
                "roughness": 8.0,
            },
        ],
        "intensity": {"value": 1.0, "min": 0.95, "max": 1.05},
        "shared_parameters": [
            "Copper oxide.thickness",
            "Titanium adhesion.material.rho",
        ],
    }
    spec = mg.model_spec_from_dict(spec_dict)

    names = [layer.name for layer in spec.layers]
    assert names == ["Copper_oxide", "Copper", "Titanium_adhesion"]
    assert spec.shared_parameters == [
        "Copper_oxide.thickness",
        "Titanium_adhesion.material.rho",
    ]

    data_file = tmp_path / "REFL_218281_combined_data_auto.txt"
    script = mg.render_case1_script(spec, data_file, model_name="sanitize")
    # Must parse cleanly — no spaces in identifiers on the stack line.
    ast.parse(script)
    assert "Copper oxide(" not in script
    assert "Titanium adhesion(" not in script


def test_sanitize_layer_name_leading_digit_and_unicode() -> None:
    assert mg._sanitize_layer_name("2nd layer") == "_2nd_layer"
    assert mg._sanitize_layer_name("  Cu/oxide  ") == "Cu_oxide"
    assert mg._sanitize_layer_name("") == "layer"
