"""
``create-model`` CLI — dispatches between Mode A and Mode B.

Mode A — convert an existing AuRE problem JSON or ModelDefinition::

    create-model path/to/problem.json [-o models/<name>.py]

Mode B — generate a new script via LLM from a sample description and one or
more REF_L data files, driven by a YAML/JSON config file with a ``states:``
list::

    create-model --config model-creation.yaml

The config file's directory is the base for relative paths. See the
``--help`` output below or ``analyzer_tools/skills/create-model/SKILL.md`` for the
full ``states:`` schema.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import yaml


_FORBIDDEN_TOP_KEYS = (
    "data",
    "data_file",
    "data_files",
    "source",
    "jobs",
    "defaults",
)


def _load_config(path: Path) -> Dict[str, Any]:
    """Load a YAML or JSON config file into a plain dict."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise click.BadParameter(
            f"Config file {path} must contain a mapping at the top level."
        )
    return data


def _pick(d: Dict[str, Any], *keys: str) -> Any:
    """Return the first non-None value among ``keys`` in mapping ``d``."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _find_env_near(start: Path) -> Optional[Path]:
    """Walk upward from ``start`` looking for a ``.env`` file.

    Stops at the filesystem root. Returns ``None`` if nothing is found.
    """
    try:
        cur = start.resolve()
    except OSError:
        cur = start
    for d in (cur, *cur.parents):
        candidate = d / ".env"
        if candidate.is_file():
            return candidate
    return None


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("source", required=False, type=click.Path(dir_okay=False))
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="YAML or JSON file describing a multi-state Mode B job. Required "
    "for Mode B; mutually exclusive with the SOURCE argument.",
)
@click.option(
    "--env",
    "env_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Extra .env file loaded at the top of the cascade (after the "
    "process environment, before project and user-global .env). Useful "
    "when running from a data directory that has no .env of its own.",
)
@click.option(
    "-o",
    "--out",
    type=click.Path(dir_okay=False),
    default=None,
    help="Output path for the generated model script. "
    "Default: <ANALYZER_MODELS_DIR>/<name>.py.",
)
@click.option(
    "--model-name",
    type=str,
    default=None,
    help="Name used in the script docstring and default output filename. "
    "Overrides ``model_name`` in the config file.",
)
def main(
    source: Optional[str],
    config_path: Optional[Path],
    env_path: Optional[Path],
    out: Optional[str],
    model_name: Optional[str],
) -> None:
    """Generate a refl1d model script.

    \b
    Two modes:
      A) Convert an AuRE problem JSON — pass SOURCE.
      B) Generate via LLM from a description + data files — pass --config.

    \b
    Mode B uses a YAML/JSON file with a ``states:`` list. Each state groups
    data files that share one physical sample (so one ``Sample`` stack);
    structural parameters are tied across states via ``shared_parameters``
    (whitelist) or ``unshared_parameters`` (blacklist). Within a state,
    files must all be the same kind:

    \b
      - one combined file → single ``QProbe`` segment, OR
      - N partial files sharing one set_id → N ``make_probe`` segments
        per state, with one ``Sample`` reused across them.

    Per-state nuisance parameters (``theta_offset``, ``sample_broadening``)
    are only valid on partial-kind states.

    \b
    Top-level config keys
    ---------------------
      describe:        sample description (required for Mode B). Aliases:
                       description, sample_description.
      states:          list of state mappings (required, see below).
      model_name:      name used in docstring and default filename.
                       Alias: name.
      out:             output script path. CLI ``--out`` overrides.
      data_dir:        if set, emit ``DATA_DIR = "<value>"`` at the top of
                       the generated script and rewrite file paths as
                       ``os.path.join(DATA_DIR, ...)``. Relative values
                       resolve against the config file's directory.
      shared_parameters / unshared_parameters: dotted attribute paths that
                       are tied (whitelist) or excluded from the default
                       tied-set (blacklist). Mutually exclusive.

    \b
    Per-state keys
    --------------
      name:               unique label for the state.
      data / data_files:  list of REF_L files (all combined, or all
                          partials of one set_id).
      extra_description:  optional text appended to the global ``describe``
                          when the LLM is told about this state. Use it
                          to note state-specific conditions (e.g. "in H2O
                          instead of D2O").
      theta_offset:       false / true / {init, min, max} — partials only.
      sample_broadening:  false / true / {init, min, max} — partials only.
      back_reflection:    bool — beam enters through the substrate (per
                          state). Defaults to the LLM's answer.

    \b
    Notes
    -----
    - For batch processing across many samples, use ``analyzer-batch``
      with a manifest that calls ``create-model`` once per job.
    - Within one state, every structural parameter is tied across the
      state's files (one ``Sample`` shared).
    - ``shared_parameters`` and ``unshared_parameters`` are mutually
      exclusive.

    \b
    Examples
    --------
    # Mode A: convert an AuRE problem JSON
    create-model path/to/problem.json -o models/cu_thf.py

    \b
    # Mode B: states-driven config
    create-model --config model-creation.yaml
    """
    from analyzer_tools.config_utils import get_config

    # When a --config FILE is given without an explicit --env, search for a
    # .env walking up from the config file's directory. This makes the
    # config-file's project (e.g. Sample7) the source of truth for paths
    # like ANALYZER_MODELS_DIR, regardless of the current working
    # directory.
    effective_env: Optional[str] = str(env_path) if env_path else None
    env_explicit = effective_env is not None
    if effective_env is None and config_path is not None:
        nearby = _find_env_near(config_path.parent)
        if nearby is not None:
            effective_env = str(nearby)

    cfg_obj = get_config(effective_env)
    models_dir = cfg_obj.get_models_dir()
    if (env_explicit or (config_path and effective_env)) and cfg_obj.loaded_env_files:
        click.echo(
            "Loaded .env files: "
            + ", ".join(str(p) for p in cfg_obj.loaded_env_files),
            err=True,
        )

    if source and config_path:
        raise click.BadParameter(
            "Mode A (SOURCE JSON) and Mode B (--config) are mutually "
            "exclusive."
        )
    if not source and not config_path:
        raise click.BadParameter(
            "Provide either a JSON SOURCE (Mode A) or --config FILE "
            "(Mode B)."
        )

    # ── Mode A ──────────────────────────────────────────────────────────
    if source:
        _run_mode_a(source, out=out, model_name=model_name, models_dir=models_dir)
        return

    # ── Mode B (states-driven config) ───────────────────────────────────
    assert config_path is not None  # for type narrowing
    cfg = _load_config(config_path)

    forbidden = [k for k in _FORBIDDEN_TOP_KEYS if k in cfg]
    if forbidden:
        raise click.BadParameter(
            f"Config file contains unsupported top-level key(s): "
            f"{', '.join(repr(k) for k in forbidden)}. Mode B only accepts "
            "the 'states:' shape. For Mode A, pass the JSON file as the "
            "SOURCE argument. For batch processing, use 'analyzer-batch'."
        )

    states_raw = cfg.get("states")
    if not isinstance(states_raw, list) or not states_raw:
        raise click.BadParameter(
            "Config file must contain a non-empty 'states:' list."
        )

    describe = _pick(cfg, "describe", "description", "sample_description")
    if not describe:
        raise click.BadParameter(
            "Config file must include a 'describe:' (or 'description:' / "
            "'sample_description:') entry for Mode B."
        )

    if model_name is None:
        model_name = _pick(cfg, "model_name", "name")
    if out is None:
        out = _pick(cfg, "out")

    cfg_dir = config_path.parent.resolve()

    # data_dir literal emitted in the generated script. Relative values
    # resolve against the config file's directory.
    data_dir_raw = _pick(cfg, "data_dir")
    data_dir: Optional[str] = None
    data_dir_abs: Optional[str] = None
    if data_dir_raw:
        data_dir = str(data_dir_raw)
        if os.path.isabs(data_dir):
            data_dir_abs = data_dir
        else:
            data_dir_abs = str((cfg_dir / data_dir).resolve())

    # Resolve a relative `out` against cfg_dir.
    if out is not None and not os.path.isabs(out):
        out = str(cfg_dir / out)

    _run_states_mode(
        describe=describe,
        states=states_raw,
        shared=cfg.get("shared_parameters"),
        unshared=cfg.get("unshared_parameters"),
        base_dir=cfg_dir,
        out=out,
        model_name=model_name,
        models_dir=models_dir,
        data_dir=data_dir,
        data_dir_abs=data_dir_abs,
    )


# ---------------------------------------------------------------------------
# Mode A — JSON → script
# ---------------------------------------------------------------------------


def _run_mode_a(
    source: str,
    *,
    out: Optional[str],
    model_name: Optional[str],
    models_dir: str,
) -> None:
    from .model_from_aure import load_definition, write_model_script

    if not os.path.isfile(source):
        raise click.BadParameter(f"SOURCE {source!r} does not exist.")

    definition = load_definition(source)
    data_files = definition.pop("_data_files", None)
    default_name = model_name or Path(source).stem
    if out is None:
        out = os.path.join(models_dir, f"{default_name}.py")
    path = write_model_script(
        definition, out, model_name=default_name, data_files=data_files
    )
    click.echo(f"Wrote analyzer model script: {path}")


# ---------------------------------------------------------------------------
# Mode B helpers
# ---------------------------------------------------------------------------


def _write_script(out: str, script: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(script)
    click.echo(f"Wrote analyzer model script: {os.path.abspath(out)}")


def _handle_llm_failure(exc: Exception) -> None:
    """Re-raise as ClickException with guidance when LLM creds are missing."""
    msg = str(exc)
    if "API_KEY" in msg or "provider" in msg.lower():
        from analyzer_tools.config_utils import get_config

        cfg_obj = get_config()
        loaded = cfg_obj.loaded_env_files
        loaded_str = (
            "\n  ".join(str(p) for p in loaded) if loaded else "(none found)"
        )
        raise click.ClickException(
            f"LLM is not configured: {msg}\n\n"
            "Analyzer loads .env files in this order (highest priority first):\n"
            "  1. process environment\n"
            "  2. --env PATH / $ANALYZER_ENV_FILE\n"
            "  3. nearest .env walking up from the current directory\n"
            "  4. ~/.config/analyzer/.env  (or $ANALYZER_CONFIG_DIR/.env\n"
            "     or $XDG_CONFIG_HOME/analyzer/.env)\n\n"
            f"Files loaded this run:\n  {loaded_str}\n\n"
            "Add LLM_PROVIDER, LLM_MODEL, LLM_API_KEY (and LLM_BASE_URL if\n"
            "using a local endpoint) to one of those files, or pass\n"
            "--env PATH explicitly."
        ) from exc
    raise exc


# ---------------------------------------------------------------------------
# Mode B (multi-state) — YAML "states:" → LLM → script
# ---------------------------------------------------------------------------


def _run_states_mode(
    *,
    describe: str,
    states: List[Dict[str, Any]],
    shared: Any,
    unshared: Any,
    base_dir: Optional[Path],
    out: Optional[str],
    model_name: Optional[str],
    models_dir: str,
    data_dir: Optional[str] = None,
    data_dir_abs: Optional[str] = None,
) -> None:
    from .model_generator import (
        build_state_specs,
        generate_model_script_from_states,
    )

    default_name = model_name or "model"
    if out is None:
        out = os.path.join(models_dir, f"{default_name}.py")

    try:
        state_specs = build_state_specs(states, base_dir=base_dir)
    except ValueError as exc:
        raise click.BadParameter(str(exc)) from exc

    shared_list = _as_str_list("shared_parameters", shared)
    unshared_list = _as_str_list("unshared_parameters", unshared)
    if shared_list is not None and unshared_list is not None:
        raise click.BadParameter(
            "'shared_parameters' and 'unshared_parameters' are mutually exclusive."
        )

    click.echo(
        "Generating multi-state model script via LLM "
        f"({len(state_specs)} state(s), "
        f"{sum(len(s.data_files) for s in state_specs)} file(s))…",
        err=True,
    )
    try:
        script = generate_model_script_from_states(
            description=describe,
            states=state_specs,
            model_name=default_name,
            shared_parameters=shared_list,
            unshared_parameters=unshared_list,
            data_dir=data_dir,
            data_dir_abs=data_dir_abs,
        )
    except ValueError as exc:
        _handle_llm_failure(exc)
        return  # unreachable

    _write_script(out, script)


def _as_str_list(field_name: str, raw: Any) -> Optional[List[str]]:
    if raw is None:
        return None
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, (list, tuple)):
        return [str(v) for v in raw]
    raise click.BadParameter(
        f"{field_name!r} must be a list of strings, got {type(raw).__name__}."
    )


if __name__ == "__main__":
    main()
