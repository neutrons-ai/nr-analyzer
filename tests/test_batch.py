"""Tests for the manifest batch runner, focused on for_each expansion."""

from textwrap import dedent

import click
import pytest

from analyzer_tools.batch import (
    TOOL_COMMANDS,
    _expand_for_each,
    load_manifest,
    run_manifest,
)


def test_create_model_maps_to_module_with_main():
    """Regression: create-model must dispatch to a module that has a click main().

    Batch dispatches via ``python -m <module>``. Historically create-model was
    mapped to ``analyzer_tools.analysis.model_from_aure``, which has no ``main()``,
    so the job crashed. It must point at ``analysis.create_model`` (the real CLI).
    """
    import importlib

    module_path = TOOL_COMMANDS["create-model"]
    assert module_path == "analyzer_tools.analysis.create_model"
    mod = importlib.import_module(module_path)
    assert callable(getattr(mod, "main", None))


def test_no_dropped_tools_in_batch_commands():
    """The slimmed package must not advertise the left-behind EIS/iceberg tools."""
    dropped = {"eis-intervals", "eis-reduce-events", "iceberg-packager"}
    assert not (set(TOOL_COMMANDS) & dropped)


def test_expand_for_each_files_shorthand():
    job = {
        "tool": "simple-reduction",
        "args": ["--template", "t.xml"],
        "files": ["a.nxs.h5", "b.nxs.h5"],
    }
    expanded = _expand_for_each(job)

    assert len(expanded) == 2
    assert expanded[0]["args"] == ["--template", "t.xml", "--event-file", "a.nxs.h5"]
    assert expanded[1]["args"] == ["--template", "t.xml", "--event-file", "b.nxs.h5"]
    assert expanded[0]["name"] == "simple-reduction_a"
    assert expanded[1]["name"] == "simple-reduction_b"
    # original job mapping is not mutated
    assert "files" in job
    assert "for_each" not in expanded[0]


def test_expand_for_each_explicit_flag_and_name_prefix():
    job = {
        "name": "fit",
        "tool": "run-fit",
        "args": ["cu_thf"],
        "for_each": {"--run": ["218281", "218386"]},
    }
    expanded = _expand_for_each(job)

    assert [j["name"] for j in expanded] == ["fit_218281", "fit_218386"]
    assert expanded[0]["args"] == ["cu_thf", "--run", "218281"]


def test_expand_for_each_passthrough_when_absent():
    job = {"name": "x", "tool": "run-fit", "args": ["a"]}
    assert _expand_for_each(job) == [job]


def test_expand_for_each_rejects_multi_flag():
    job = {
        "tool": "simple-reduction",
        "for_each": {"--a": [1], "--b": [2]},
    }
    with pytest.raises(click.ClickException):
        _expand_for_each(job)


def test_expand_for_each_rejects_empty_list():
    job = {"tool": "simple-reduction", "for_each": {"--event-file": []}}
    with pytest.raises(click.ClickException):
        _expand_for_each(job)


def test_load_manifest_expands_files(tmp_path):
    manifest = tmp_path / "m.yaml"
    manifest.write_text(dedent("""
        jobs:
          - tool: simple-reduction
            args: [--template, t.xml]
            files: [a.h5, b.h5, c.h5]
    """))

    data = load_manifest(str(manifest))
    assert len(data["jobs"]) == 3
    assert all(j["tool"] == "simple-reduction" for j in data["jobs"])


def test_run_manifest_dry_run_with_files(tmp_path):
    manifest = tmp_path / "m.yaml"
    manifest.write_text(dedent("""
        data_location: /data
        jobs:
          - tool: simple-reduction
            args: [--template, t.xml]
            files: [REF_L_1.nxs.h5, REF_L_2.nxs.h5]
    """))

    results = run_manifest(str(manifest), dry_run=True)

    assert len(results) == 2
    assert results[0]["name"] == "simple-reduction_REF_L_1"
    assert "/data/REF_L_1.nxs.h5" in results[0]["command"]
    assert "/data/REF_L_2.nxs.h5" in results[1]["command"]
