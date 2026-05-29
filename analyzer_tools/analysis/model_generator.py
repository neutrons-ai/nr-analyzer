"""
LLM-driven refl1d model script generator (``create-model`` Mode B).

Given a natural-language sample description and one or more REF_L data files,
this module:

1. Detects which fitting *case* applies (see below).
2. Calls the configured LLM (via ``aure.llm``) with a strict JSON-output prompt
   to obtain a :class:`ModelSpec` (materials, layer stack, bounds, optional
   case-3 shared-parameter list).
3. Renders an analyzer-convention refl1d script from that spec using a
   case-specific template — the Python is always produced by our code so the
   LLM cannot emit arbitrary code.

Cases
-----
* **case1** — one combined data file, Q-based ``QProbe``.
* **case2** — multiple ``REFL_{set}_{part}_{run}_partial.txt`` files sharing
  a single ``set_id``; angle-based probes built with ``make_probe``.
* **case3** — multiple combined data files representing distinct measurements
  to be co-refined with shared structural parameters (not supported by AuRE).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CASE_1 = "case1"
CASE_2 = "case2"
CASE_3 = "case3"

_PARTIAL_RE = re.compile(r"REFL_(\d+)_(\d+)_(\d+)_partial\.txt$", re.IGNORECASE)
_COMBINED_RE = re.compile(r"REFL_(\d+)_combined_data_auto\.txt$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------


def _classify_file(path: Path) -> Tuple[str, Dict[str, str]]:
    """Return ("partial"|"combined", metadata) for a REF_L file path."""
    name = path.name
    m = _PARTIAL_RE.search(name)
    if m:
        return "partial", {
            "set_id": m.group(1),
            "part_id": m.group(2),
            "run_id": m.group(3),
        }
    m = _COMBINED_RE.search(name)
    if m:
        return "combined", {"set_id": m.group(1)}
    raise ValueError(
        f"Unrecognised REF_L filename: {name!r}. Expected "
        "'REFL_{set}_combined_data_auto.txt' or "
        "'REFL_{set}_{part}_{run}_partial.txt'."
    )


def detect_case(data_files: Sequence[Path | str]) -> str:
    """Choose case1 / case2 / case3 from *data_files*."""
    if not data_files:
        raise ValueError("At least one data file is required.")
    paths = [Path(f) for f in data_files]
    kinds = [_classify_file(p) for p in paths]

    kinds_only = {k for k, _ in kinds}
    if len(kinds_only) > 1:
        raise ValueError(
            "Mixing partial and combined data files is not supported. "
            "Provide either a single combined file, several partial files "
            "from the same set, or several combined files."
        )

    kind = next(iter(kinds_only))
    if kind == "combined":
        return CASE_1 if len(paths) == 1 else CASE_3

    # Partial files: must share a single set_id.
    set_ids = {meta["set_id"] for _, meta in kinds}
    if len(set_ids) > 1:
        raise ValueError(
            "Partial files span multiple set_ids: "
            f"{sorted(set_ids)}. All partial files must share the "
            "same set_id (the first run number)."
        )
    if len(paths) < 2:
        raise ValueError(
            "Case 2 (multi-segment co-refinement) needs at least two partial "
            "files from the same set; only one was given."
        )
    return CASE_2


def _resolve_data_path_from_env(path: Path) -> Optional[Path]:
    """Try to locate *path* in the analyzer-configured data directories.

    Looks under ``ANALYZER_PARTIAL_DATA_DIR`` and
    ``ANALYZER_COMBINED_DATA_DIR`` (via :class:`Config`). Both the raw
    relative path and its basename are tried, so ``Rawdata/foo_partial.txt``
    will resolve to ``$ANALYZER_PARTIAL_DATA_DIR/foo_partial.txt`` when the
    Rawdata sub-folder is not present at the search root.
    Returns the first existing file, or ``None``.
    """
    try:
        from analyzer_tools.config_utils import get_config
        cfg = get_config()
        candidates_roots = [
            cfg.get_partial_data_dir(),
            cfg.get_combined_data_dir(),
        ]
    except Exception:
        return None

    rel_variants = [path, Path(path.name)]
    for root in candidates_roots:
        if not root:
            continue
        root_path = Path(root)
        for rel in rel_variants:
            candidate = root_path / rel
            if candidate.is_file():
                return candidate
    return None


# ---------------------------------------------------------------------------
# REF_L header parsing
# ---------------------------------------------------------------------------


_RUN_ROW_RE = re.compile(
    r"^#\s*(\d+)\s+(\d+)\s+([0-9.+\-eE]+)\s+"
    r"([0-9.+\-eE]+)\s+([0-9.+\-eE]+)"
)


def parse_refl_header(path: Path | str) -> Dict[str, Any]:
    """Extract experiment/run metadata from a REF_L data file header.

    Returns a dict with keys:

    * ``experiment`` — e.g. ``"IPTS-34347"``
    * ``run`` — top-level run number from the header line
    * ``theta_offset`` — float (degrees) if present, else ``0.0``
    * ``runs`` — list of ``{data_run, norm_run, two_theta, theta, lambda_min, lambda_max}``
      for every row of the header table (1 row for partials, N for combined).
    """
    path = Path(path)
    experiment: Optional[str] = None
    run: Optional[str] = None
    theta_offset = 0.0
    runs: List[Dict[str, Any]] = []

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("#"):
                break
            stripped = line.lstrip("# ").rstrip()
            if stripped.startswith("Experiment "):
                parts = stripped.split()
                # "Experiment IPTS-xxxx Run yyyy"
                if len(parts) >= 2:
                    experiment = parts[1]
                if len(parts) >= 4 and parts[2].lower() == "run":
                    run = parts[3]
            elif stripped.startswith("Theta offset"):
                _, _, val = stripped.partition(":")
                try:
                    theta_offset = float(val.strip())
                except ValueError:
                    theta_offset = 0.0
            else:
                m = _RUN_ROW_RE.match(line)
                if m:
                    two_theta = float(m.group(3))
                    runs.append(
                        {
                            "data_run": m.group(1),
                            "norm_run": m.group(2),
                            "two_theta": two_theta,
                            "theta": two_theta / 2.0,
                            "lambda_min": float(m.group(4)),
                            "lambda_max": float(m.group(5)),
                        }
                    )

    return {
        "experiment": experiment,
        "run": run,
        "theta_offset": theta_offset,
        "runs": runs,
    }


# ---------------------------------------------------------------------------
# Structured model specification (LLM output schema)
# ---------------------------------------------------------------------------


@dataclass
class LayerSpec:
    name: str
    sld: float
    thickness: float = 0.0
    roughness: float = 5.0
    thickness_min: Optional[float] = None
    thickness_max: Optional[float] = None
    sld_min: Optional[float] = None
    sld_max: Optional[float] = None
    roughness_min: Optional[float] = None
    roughness_max: Optional[float] = None


@dataclass
class ModelSpec:
    ambient: LayerSpec
    substrate: LayerSpec
    layers: List[LayerSpec]  # ambient-adjacent → substrate-adjacent (top-to-bottom)
    intensity: Dict[str, float] = field(
        default_factory=lambda: {"value": 1.0, "min": 0.9, "max": 1.1}
    )
    back_reflection: bool = False
    # Case-3 only: list of per-layer attribute paths to tie across experiments,
    # e.g. ["Cu.material.rho", "Cu.interface", "Ti.thickness"].
    shared_parameters: List[str] = field(default_factory=list)


def _sanitize_layer_name(name: str) -> str:
    """Coerce *name* into a valid Python identifier.

    Layer names are used both as the SLD ``name=`` keyword and — critically —
    as Python identifiers in the generated ``sample = A | B(...) | C`` stack
    expression. Names with spaces or other non-identifier characters (e.g.
    ``"Copper oxide"``) would produce a SyntaxError at fit time. We replace
    non-alphanumerics with underscores and prefix a leading digit with one.
    """
    raw = str(name).strip()
    cleaned = "".join(c if c.isalnum() or c == "_" else "_" for c in raw)
    if not cleaned:
        return "layer"
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"_{cleaned}"
    return cleaned


def _layer_from_dict(d: Dict[str, Any]) -> LayerSpec:
    return LayerSpec(
        name=_sanitize_layer_name(d["name"]),
        sld=float(d["sld"]),
        thickness=float(d.get("thickness", 0.0)),
        roughness=float(d.get("roughness", 5.0)),
        thickness_min=_opt_float(d.get("thickness_min")),
        thickness_max=_opt_float(d.get("thickness_max")),
        sld_min=_opt_float(d.get("sld_min")),
        sld_max=_opt_float(d.get("sld_max")),
        roughness_min=_opt_float(d.get("roughness_min")),
        roughness_max=_opt_float(d.get("roughness_max")),
    )


def _opt_float(v: Any) -> Optional[float]:
    return float(v) if v is not None else None


def model_spec_from_dict(d: Dict[str, Any]) -> ModelSpec:
    """Validate an LLM JSON response and coerce into a :class:`ModelSpec`.

    Raises ``ValueError`` on missing/invalid keys.
    """
    for key in ("ambient", "substrate", "layers"):
        if key not in d:
            raise ValueError(f"ModelSpec JSON is missing required key {key!r}.")
    if not isinstance(d["layers"], list) or len(d["layers"]) == 0:
        raise ValueError("ModelSpec 'layers' must be a non-empty list.")

    ambient = _layer_from_dict(d["ambient"])
    substrate = _layer_from_dict(d["substrate"])
    layers = [_layer_from_dict(layer) for layer in d["layers"]]

    intensity = d.get("intensity") or {}
    intensity_out = {
        "value": float(intensity.get("value", 1.0)),
        "min": float(intensity.get("min", 0.9)),
        "max": float(intensity.get("max", 1.1)),
    }

    # Layer names in shared_parameters paths must match the sanitized
    # LayerSpec names, otherwise _validate_shared_paths rejects them. Build a
    # raw→sanitized prefix map from the original dicts and rewrite each path's
    # leading segment.
    name_map: Dict[str, str] = {}
    for raw_layer in (
        [d["ambient"], d["substrate"]] + list(d["layers"])
    ):
        raw_name = str(raw_layer.get("name", ""))
        name_map[raw_name] = _sanitize_layer_name(raw_name)

    def _remap_path(path: str) -> str:
        head, sep, tail = str(path).partition(".")
        if sep and head in name_map:
            return f"{name_map[head]}{sep}{tail}"
        return str(path)

    shared_paths = [_remap_path(p) for p in (d.get("shared_parameters") or [])]

    return ModelSpec(
        ambient=ambient,
        substrate=substrate,
        layers=layers,
        intensity=intensity_out,
        back_reflection=bool(d.get("back_reflection", False)),
        shared_parameters=shared_paths,
    )


# ---------------------------------------------------------------------------
# Multi-state specification (YAML "states:" form)
# ---------------------------------------------------------------------------


STATE_COMBINED = "combined"
STATE_PARTIALS = "partials"


@dataclass
class StateSpec:
    """One measurement state in a co-refinement.

    A state corresponds to a single physical "state" of the sample. All files
    within a state see the same sample and any ``theta_offset`` /
    ``sample_broadening`` nuisance parameters are shared across segments
    inside the state. Structural parameters are tied *across* states via
    ``shared_parameters`` / ``unshared_parameters`` on the parent spec.
    """

    name: str
    data_files: List[Path]
    kind: str  # "combined" (1 file) or "partials" (N files, shared set_id)
    thetas: List[float] = field(default_factory=list)  # for kind=="partials"
    theta_offset: Optional[Dict[str, float]] = None
    sample_broadening: Optional[Dict[str, float]] = None
    # True when the neutron beam enters through the substrate side (e.g.
    # solid/liquid interface illuminated through a silicon block). The
    # renderer uses this flag to choose stack ORIENTATION only — it never
    # touches ``probe.back_reflectivity``, so refl1d's default
    # interpretation (``sample[0]`` = substrate / beam exit, ``sample[-1]`` =
    # surface / beam entry) always gives correct physics. ``None`` means
    # "inherit from the top-level ModelSpec".
    back_reflection: Optional[bool] = None
    # Free-form text appended to the global ``describe`` when this state
    # is presented to the LLM. Use it to record state-specific conditions
    # (e.g. solvent change, temperature) that the shared sample stack
    # alone cannot express.
    extra_description: Optional[str] = None


def _normalise_state_param(
    raw: Any, *, state_name: str, field_name: str
) -> Optional[Dict[str, float]]:
    """Coerce a YAML value into ``{init, min, max}`` or raise."""
    if raw is None or raw is False:
        return None
    if raw is True:
        # Sensible defaults for the two supported fields.
        if field_name == "theta_offset":
            return {"init": 0.0, "min": -0.02, "max": 0.02}
        if field_name == "sample_broadening":
            return {"init": 0.0, "min": 0.0, "max": 0.01}
    if isinstance(raw, dict):
        init = float(raw.get("init", raw.get("value", 0.0)))
        lo = raw.get("min")
        hi = raw.get("max")
        if lo is None or hi is None:
            raise ValueError(
                f"State {state_name!r}: {field_name!r} must include 'min' and 'max'."
            )
        return {"init": init, "min": float(lo), "max": float(hi)}
    raise ValueError(
        f"State {state_name!r}: invalid {field_name!r} value ({raw!r}); "
        "expected a mapping with min/max or a boolean."
    )


def build_state_specs(
    states: List[Dict[str, Any]],
    *,
    base_dir: Optional[Path] = None,
) -> List[StateSpec]:
    """Validate a YAML ``states:`` list and produce :class:`StateSpec` objects.

    File paths relative to ``base_dir`` are resolved against it.
    """
    if not states:
        raise ValueError("'states' list is empty; need at least one state.")

    specs: List[StateSpec] = []
    used_names: set[str] = set()
    for i, entry in enumerate(states):
        if not isinstance(entry, dict):
            raise ValueError(f"states[{i}] must be a mapping.")

        name = str(entry.get("name") or f"state{i + 1}")
        if name in used_names:
            raise ValueError(f"Duplicate state name: {name!r}.")
        used_names.add(name)

        raw_files = entry.get("data") or entry.get("data_files") or []
        if isinstance(raw_files, (str, Path)):
            raw_files = [raw_files]
        if not raw_files:
            raise ValueError(f"State {name!r}: no data files.")

        paths: List[Path] = []
        for f in raw_files:
            p = Path(f)
            if not p.is_absolute() and base_dir is not None:
                candidate = base_dir / p
                # Fall back to the analyzer-configured data directories
                # (ANALYZER_PARTIAL_DATA_DIR / ANALYZER_COMBINED_DATA_DIR)
                # when the relative path doesn't resolve against base_dir.
                if not candidate.is_file():
                    fallback = _resolve_data_path_from_env(p)
                    if fallback is not None:
                        candidate = fallback
                p = candidate
            paths.append(p)

        kinds = {_classify_file(p)[0] for p in paths}
        if len(kinds) > 1:
            raise ValueError(
                f"State {name!r}: cannot mix combined and partial files "
                "within a single state."
            )
        kind = next(iter(kinds))

        thetas: List[float] = []
        if kind == "partial":
            set_ids = {_classify_file(p)[1]["set_id"] for p in paths}
            if len(set_ids) > 1:
                raise ValueError(
                    f"State {name!r}: partial files span multiple set_ids "
                    f"({sorted(set_ids)}); all must share one set_id."
                )
            for p in paths:
                header = parse_refl_header(p)
                runs = header.get("runs") or []
                if not runs:
                    raise ValueError(
                        f"State {name!r}: partial file {p.name!r} has no "
                        "2θ row in its header."
                    )
                thetas.append(float(runs[0]["theta"]))
            state_kind = STATE_PARTIALS
        else:
            if len(paths) != 1:
                raise ValueError(
                    f"State {name!r}: 'combined' kind expects exactly one "
                    f"data file; got {len(paths)}."
                )
            state_kind = STATE_COMBINED

        theta_off = _normalise_state_param(
            entry.get("theta_offset"),
            state_name=name,
            field_name="theta_offset",
        )
        samp_broad = _normalise_state_param(
            entry.get("sample_broadening"),
            state_name=name,
            field_name="sample_broadening",
        )
        if (theta_off or samp_broad) and state_kind != STATE_PARTIALS:
            raise ValueError(
                f"State {name!r}: theta_offset / sample_broadening are only "
                "meaningful for multi-segment (partial) data."
            )

        back_refl_raw = entry.get("back_reflection")
        if back_refl_raw is None:
            back_refl: Optional[bool] = None
        elif isinstance(back_refl_raw, bool):
            back_refl = back_refl_raw
        else:
            raise ValueError(
                f"State {name!r}: 'back_reflection' must be a boolean, got "
                f"{back_refl_raw!r}."
            )

        extra_desc_raw = entry.get("extra_description")
        if extra_desc_raw is None:
            extra_desc: Optional[str] = None
        elif isinstance(extra_desc_raw, str):
            extra_desc = extra_desc_raw.strip() or None
        else:
            raise ValueError(
                f"State {name!r}: 'extra_description' must be a string, got "
                f"{type(extra_desc_raw).__name__}."
            )

        specs.append(
            StateSpec(
                name=name,
                data_files=paths,
                kind=state_kind,
                thetas=thetas,
                theta_offset=theta_off,
                sample_broadening=samp_broad,
                back_reflection=back_refl,
                extra_description=extra_desc,
            )
        )
    return specs


def _layer_names_in_paths(paths: Sequence[str]) -> List[str]:
    """Extract unique layer-name prefixes from dotted shared/unshared paths.

    Each path is expected to look like ``<layer>.<attr>`` (e.g.
    ``Cu.thickness``). Paths that don't parse are skipped — validation
    happens later in :func:`_validate_shared_paths`.
    """
    seen: List[str] = []
    seen_set: set[str] = set()
    for path in paths:
        m = _SHARED_PATH_RE.match(path)
        if not m:
            continue
        layer = m.group("layer")
        if layer not in seen_set:
            seen_set.add(layer)
            seen.append(layer)
    return seen


def _validate_shared_paths(spec: ModelSpec, paths: Sequence[str], *,
                           field: str) -> None:
    """Raise ``ValueError`` when any path's layer prefix isn't in *spec*.

    The renderer indexes ``sample[<layer>]`` by exact name, so a mismatch
    between the YAML's layer prefix (e.g. ``Cu``) and the LLM's chosen
    layer name (e.g. ``Copper``) would produce a script that crashes at
    fit time. We catch it here with a clear message instead.
    """
    valid = {layer.name for layer in spec.layers}
    valid.add(spec.substrate.name)
    bad: List[str] = []
    for path in paths:
        m = _SHARED_PATH_RE.match(path)
        if not m or m.group("layer") not in valid:
            bad.append(path)
    if bad:
        raise ValueError(
            f"{field}: layer prefix(es) not found in the generated model. "
            f"Offending entries: {bad}. "
            f"Available layer/substrate names from the LLM: "
            f"{sorted(valid)}. "
            "Either edit your YAML to use the LLM's names, or add the "
            "intended names to the sample description so the LLM picks them."
        )


def default_shared_parameters(spec: ModelSpec) -> List[str]:
    """Return the default list of dotted paths tied across states.

    Every layer's ``thickness``, ``material.rho``, and ``interface`` are
    shared by default. The substrate's ``interface`` is also shared. The
    ambient and per-probe ``intensity`` are intentionally NOT shared.
    """
    paths: List[str] = []
    for layer in spec.layers:
        paths.append(f"{layer.name}.thickness")
        paths.append(f"{layer.name}.material.rho")
        paths.append(f"{layer.name}.interface")
    paths.append(f"{spec.substrate.name}.interface")
    return paths


def resolve_shared_parameters(
    spec: ModelSpec,
    *,
    shared: Optional[List[str]] = None,
    unshared: Optional[List[str]] = None,
) -> List[str]:
    """Resolve the effective shared-parameter list for multi-state rendering.

    Precedence:

    1. If ``shared`` is given, use it verbatim (explicit whitelist).
    2. Else start from :func:`default_shared_parameters` and subtract
       ``unshared`` if given.
    3. If neither is given, fall back to ``spec.shared_parameters`` (from
       the LLM) so case-3-style single-file-per-state inputs keep working.
    """
    if shared is not None and unshared is not None:
        raise ValueError(
            "'shared_parameters' and 'unshared_parameters' are mutually "
            "exclusive; pick one."
        )
    if shared is not None:
        _validate_shared_paths(spec, shared, field="shared_parameters")
        return list(shared)
    if unshared is not None:
        _validate_shared_paths(spec, unshared, field="unshared_parameters")
        skip = set(unshared)
        return [p for p in default_shared_parameters(spec) if p not in skip]
    if spec.shared_parameters:
        return list(spec.shared_parameters)
    return default_shared_parameters(spec)


# ---------------------------------------------------------------------------
# LLM prompting
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """\
You are a neutron reflectometry expert helping to construct refl1d model
specifications. You MUST reply with a single JSON object conforming to the
schema provided in the user message — no prose, no code fences, no commentary.

Naming rules (CRITICAL — names are used as Python identifiers):
- Every "name" field (ambient, substrate, each layer) MUST be a valid Python
  identifier: ASCII letters, digits, and underscores only, and must not start
  with a digit. NO spaces, hyphens, parentheses, slashes, or other punctuation.
- Prefer short element/compound tokens: "Cu", "Ti", "Si", "D2O", "SiO2",
  "CuO", "TiO2". Use underscores to join words if needed: "Cu_oxide",
  "Ti_adhesion". Never use "Copper oxide" or "Titanium adhesion layer".
- Layer names must be unique within a single model.

Domain rules (apply to every layer):
- Minimum roughness: 5 Å. Typical range: 5–30 Å.
- Roughness bounds must stay below half the thickness of adjacent layers.
- SLD bounds: at least ±2 × 10⁻⁶ Å⁻² around nominal. For adhesion layers
  (Ti etc.), use ±3 or wider.
- Never vary the substrate SLD. Its roughness sits on the last layer's
  interface; do not add a substrate-thickness parameter.
- Minimum layer thickness: 5 Å. Do NOT add SiO₂ on silicon unless the user
  explicitly mentions it.

Common SLD (×10⁻⁶ Å⁻²): Silicon 2.07, Gold 4.5, Copper 6.55, Titanium −1.95,
Platinum 6.288, D2O 6.19, THF 5.8, Air 0.0.
"""


_JSON_SCHEMA_DESCRIPTION = """\
Respond with a JSON object of shape:

{
  "ambient":   {"name": str, "sld": float,
                "sld_min": float?, "sld_max": float?,
                "roughness": float?, "roughness_min": float?, "roughness_max": float?},
  "substrate": {"name": str, "sld": float,
                "roughness_min": float?, "roughness_max": float?},
  "layers": [
    {"name": str, "sld": float, "thickness": float, "roughness": float,
     "thickness_min": float, "thickness_max": float,
     "sld_min": float, "sld_max": float,
     "roughness_min": float, "roughness_max": float}
  ],
  "intensity":       {"value": float, "min": float, "max": float},
  "back_reflection": bool,
  "shared_parameters": [str]   // case 3 only; ignored otherwise
}

Layer ordering: the "layers" list goes from the ambient-adjacent layer
(first) to the substrate-adjacent layer (last). Do NOT include the ambient
or the substrate inside "layers".

"back_reflection" describes probe geometry. Set it to true when the neutron
beam enters through the substrate (e.g. a silicon block illuminated from
the bulk side so the reflection occurs at the buried solid/liquid
interface); set it to false for standard front-reflection geometry (beam
enters through the ambient side). The renderer uses this flag to choose
stack orientation so that refl1d's default ``probe.back_reflectivity=False``
always gives correct physics — never set ``back_reflectivity`` on the probe
yourself.
"""


def _case_instructions(case: str, n_files: int) -> str:
    if case == CASE_1:
        return (
            "Case 1: a single combined data file. Produce a layer stack for "
            "a standard Q-based QProbe fit. Leave 'shared_parameters' empty."
        )
    if case == CASE_2:
        return (
            f"Case 2: {n_files} partial (single-angle) files from the same "
            "measurement. All segments see the same sample, so produce one "
            "layer stack. Leave 'shared_parameters' empty — the renderer "
            "automatically shares the sample object across probes."
        )
    return (
        f"Case 3: {n_files} combined data files to be co-refined. Produce one "
        "shared layer stack, and in 'shared_parameters' list the dotted "
        "attribute paths that should be *tied* across all experiments, e.g. "
        '"Cu.material.rho", "Cu.interface", "Ti.thickness", "Ti.material.rho", '
        '"Ti.interface". Intensity and ambient SLD should normally be '
        "per-experiment (do NOT list them in shared_parameters)."
    )


def build_llm_prompt(
    case: str,
    description: str,
    data_files: Sequence[Path],
    headers: Sequence[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Build a (system, user) message pair for the LLM call."""
    header_blocks = []
    for path, header in zip(data_files, headers):
        runs = header.get("runs") or []
        run_lines = "\n".join(
            f"    - data_run={r['data_run']}, 2θ={r['two_theta']:.4f}°, θ={r['theta']:.4f}°"
            for r in runs
        )
        header_blocks.append(
            f"- {path.name} (experiment {header.get('experiment')}, "
            f"run {header.get('run')}, theta_offset={header.get('theta_offset')}):\n"
            f"{run_lines or '    (no run table rows)'}"
        )
    header_block = "\n".join(header_blocks)

    user = (
        f"Sample description (from the user):\n"
        f"{description.strip()}\n\n"
        f"Data files:\n{header_block}\n\n"
        f"{_case_instructions(case, len(data_files))}\n\n"
        f"{_JSON_SCHEMA_DESCRIPTION}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


class LLMResponseError(RuntimeError):
    """Raised when the LLM reply cannot be parsed into a ModelSpec."""


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(content: str) -> Dict[str, Any]:
    """Pull a JSON object out of the LLM response."""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(content)
    if m:
        return json.loads(m.group(1))
    # Try bracket-balanced first-object extraction as a last resort.
    start = content.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(content)):
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(content[start : i + 1])
    raise LLMResponseError(f"No JSON object found in LLM response: {content[:200]!r}")


def call_llm_for_model_spec(
    messages: List[Dict[str, str]],
    *,
    llm: Any = None,
    max_retries: int = 1,
) -> ModelSpec:
    """Invoke the configured LLM and parse the reply into a :class:`ModelSpec`.

    On the first parse/validation failure, append the error to the
    conversation and retry once. Raises :class:`LLMResponseError` if the
    second attempt also fails.
    """
    if llm is None:  # pragma: no cover - real LLM is opt-in
        from aure.llm import get_llm

        llm = get_llm(temperature=0.0)

    history = list(messages)
    last_error: Optional[str] = None
    for attempt in range(max_retries + 1):
        reply = llm.invoke(history)
        content = getattr(reply, "content", reply)
        if isinstance(content, list):  # Some providers return segmented content.
            content = "".join(
                seg.get("text", "") if isinstance(seg, dict) else str(seg)
                for seg in content
            )
        try:
            data = _extract_json(str(content))
            return model_spec_from_dict(data)
        except (LLMResponseError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            if attempt >= max_retries:
                break
            history = history + [
                {"role": "assistant", "content": str(content)},
                {
                    "role": "user",
                    "content": (
                        "Your previous reply could not be parsed: "
                        f"{last_error}. Please respond with ONLY a valid JSON "
                        "object matching the schema. No prose, no code fences."
                    ),
                },
            ]
    raise LLMResponseError(
        f"LLM reply still invalid after {max_retries + 1} attempt(s): {last_error}"
    )


# ---------------------------------------------------------------------------
# Script rendering
# ---------------------------------------------------------------------------


def _format_float(value: float) -> str:
    """Compact float repr suitable for embedding in generated source."""
    return repr(float(value))


def _layer_var(layer: LayerSpec, used: set[str]) -> str:
    base = "".join(c if c.isalnum() or c == "_" else "_" for c in layer.name.strip())
    if not base or not (base[0].isalpha() or base[0] == "_"):
        base = f"layer_{len(used)}"
    candidate = base
    i = 2
    while candidate in used:
        candidate = f"{base}_{i}"
        i += 1
    used.add(candidate)
    return candidate


def _materials_lines(spec: ModelSpec, indent: str) -> List[str]:
    used: set[str] = set()
    lines: List[str] = []
    for layer in [spec.ambient] + spec.layers + [spec.substrate]:
        var = _layer_var(layer, used)
        lines.append(
            f"{indent}{var} = SLD(name={layer.name!r}, rho={_format_float(layer.sld)})"
        )
    return lines


def _stack_line(spec: ModelSpec, indent: str, *, back_reflection: bool = False) -> str:
    """Emit the ``sample = ...`` line.

    refl1d's Experiment treats ``sample[0]`` as the substrate (beam exit) and
    ``sample[-1]`` as the surface (beam entry). We therefore choose stack
    orientation from the physical ``back_reflection`` flag so that the probe's
    default (``back_reflectivity=False``) always gives correct physics — no
    ``probe.back_reflectivity = True`` is ever emitted.

    * back_reflection=False (beam enters from ambient — standard front
      reflection): emit substrate first, ambient last. The substrate slab has
      no parameters; the ambient slab carries its roughness.
    * back_reflection=True (beam enters through substrate — buried interface):
      emit ambient first, substrate last. The substrate slab has no parameters;
      the ambient slab carries its roughness.
    """
    if back_reflection:
        # Buried-interface layout: ambient | ... | substrate.
        parts: List[str] = [
            f"{spec.ambient.name}(0, {_format_float(spec.ambient.roughness)})"
        ]
        for layer in spec.layers:
            parts.append(
                f"{layer.name}({_format_float(layer.thickness)}, "
                f"{_format_float(layer.roughness)})"
            )
        parts.append(spec.substrate.name)
    else:
        # Front-reflection layout: substrate | ... | ambient (reversed).
        parts = [spec.substrate.name]
        for layer in reversed(spec.layers):
            parts.append(
                f"{layer.name}({_format_float(layer.thickness)}, "
                f"{_format_float(layer.roughness)})"
            )
        parts.append(
            f"{spec.ambient.name}(0, {_format_float(spec.ambient.roughness)})"
        )
    return f"{indent}sample = " + " | ".join(parts)


def _ambient_interface_line(
    spec: ModelSpec, indent: str, *, sample_var: str = "sample"
) -> Optional[str]:
    """Return the ambient ``.interface.range(...)`` line, or ``None`` if absent.

    Falls back to the substrate's roughness bounds when the ambient's own
    bounds are not specified — the ambient interface on the beam-exit side
    (back-reflection mode) should always be floated, and a sensible default
    is the same range the substrate uses for the beam-entry side.
    """
    amb = spec.ambient
    r_min, r_max = amb.roughness_min, amb.roughness_max
    if r_min is None or r_max is None:
        sub = spec.substrate
        r_min, r_max = sub.roughness_min, sub.roughness_max
    if r_min is None or r_max is None:
        return None
    return (
        f'{indent}{sample_var}[{amb.name!r}].interface.range('
        f"{_format_float(r_min)}, {_format_float(r_max)})"
    )


def _substrate_interface_line(
    spec: ModelSpec, indent: str, *, sample_var: str = "sample"
) -> Optional[str]:
    """Return the substrate ``.interface.range(...)`` line, or ``None`` if absent.

    Falls back to the ambient's roughness bounds when the substrate's own
    bounds are not specified.
    """
    sub = spec.substrate
    r_min, r_max = sub.roughness_min, sub.roughness_max
    if r_min is None or r_max is None:
        amb = spec.ambient
        r_min, r_max = amb.roughness_min, amb.roughness_max
    if r_min is None or r_max is None:
        return None
    return (
        f'{indent}{sample_var}[{sub.name!r}].interface.range('
        f"{_format_float(r_min)}, "
        f"{_format_float(r_max)})"
    )


def _range_lines(
    spec: ModelSpec,
    indent: str,
    *,
    sample_var: str = "sample",
    include_ambient_interface: bool = True,
    include_substrate_interface: bool = True,
) -> List[str]:
    out: List[str] = []
    amb = spec.ambient
    if amb.sld_min is not None and amb.sld_max is not None:
        out.append(
            f'{indent}{sample_var}[{amb.name!r}].material.rho.range('
            f"{_format_float(amb.sld_min)}, {_format_float(amb.sld_max)})"
        )
    if include_ambient_interface:
        line = _ambient_interface_line(spec, indent, sample_var=sample_var)
        if line is not None:
            out.append(line)
    for layer in spec.layers:
        if layer.thickness_min is not None and layer.thickness_max is not None:
            out.append(
                f'{indent}{sample_var}[{layer.name!r}].thickness.range('
                f"{_format_float(layer.thickness_min)}, "
                f"{_format_float(layer.thickness_max)})"
            )
        if layer.sld_min is not None and layer.sld_max is not None:
            out.append(
                f'{indent}{sample_var}[{layer.name!r}].material.rho.range('
                f"{_format_float(layer.sld_min)}, "
                f"{_format_float(layer.sld_max)})"
            )
        if layer.roughness_min is not None and layer.roughness_max is not None:
            out.append(
                f'{indent}{sample_var}[{layer.name!r}].interface.range('
                f"{_format_float(layer.roughness_min)}, "
                f"{_format_float(layer.roughness_max)})"
            )
    if include_substrate_interface:
        line = _substrate_interface_line(spec, indent, sample_var=sample_var)
        if line is not None:
            out.append(line)
    return out


_HEADER = """\
\"\"\"Auto-generated analyzer model ({model_name}) — created by create-model.\"\"\"

import os
import numpy as np
from bumps.fitters import fit
from refl1d.names import *
"""


def _portable_path_expr(path: str) -> str:
    """Return a Python expression that evaluates to ``path`` at runtime.

    Absolute paths under the user's home directory are rewritten as
    ``os.path.join(os.path.expanduser('~'), '<rest>')`` so the generated
    script can be shared between users whose home directories differ. All
    other paths are rendered verbatim with ``repr``.
    """
    home = os.path.expanduser("~")
    if home and home != "~" and path == home:
        return "os.path.expanduser('~')"
    if home and home != "~" and path.startswith(home + os.sep):
        rest = path[len(home) + 1 :]
        return f"os.path.join(os.path.expanduser('~'), {rest!r})"
    return repr(path)


def _data_dir_lines(data_dir: Optional[Path | str]) -> List[str]:
    """Emit the ``DATA_DIR = ...`` top-of-script line when configured.

    Users can edit this single variable to point the script at a new data
    directory without touching any other line. Home-relative absolute paths
    are emitted as ``os.path.join(os.path.expanduser('~'), '<rest>')`` so
    the script remains portable between users; all other values are
    rendered verbatim.
    """
    if data_dir is None:
        return []
    return [
        "",
        "# ── Data location (edit to point at your local data copy) ───",
        f"DATA_DIR = {_portable_path_expr(str(data_dir))}",
        "",
    ]


def _data_file_ref(
    path: Path | str,
    data_dir: Optional[Path | str],
    data_dir_abs: Optional[Path | str] = None,
) -> str:
    """Return the source-code expression used in place of a raw path string.

    * ``data_dir`` unset → a portable literal (home-relative paths use
      ``os.path.expanduser('~')``).
    * ``data_dir`` set and ``path`` lives under ``data_dir_abs`` (or
      ``data_dir`` when no anchor is provided) →
      ``os.path.join(DATA_DIR, "<relpath>")``.
    * ``data_dir`` set but ``path`` is elsewhere → a portable literal so the
      script still resolves, at the cost of that one file being
      non-portable beyond ``$HOME`` substitution.

    ``data_dir_abs`` is used only for relpath math; the rendered DATA_DIR
    string keeps the literal ``data_dir`` value (so a short relative like
    ``"data"`` stays short in the generated script).
    """
    if data_dir is None:
        return _portable_path_expr(str(path))
    anchor = str(data_dir_abs) if data_dir_abs is not None else str(data_dir)
    try:
        rel = os.path.relpath(str(path), anchor)
    except ValueError:
        return _portable_path_expr(str(path))
    if rel.startswith(".."):
        return _portable_path_expr(str(path))
    return f"os.path.join(DATA_DIR, {rel!r})"


def render_case1_script(
    spec: ModelSpec,
    data_file: Path | str,
    *,
    model_name: str = "model",
    data_dir: Optional[Path | str] = None,
    data_dir_abs: Optional[Path | str] = None,
) -> str:
    """Render a case-1 (single combined file, QProbe) script."""
    lines: List[str] = [_HEADER.format(model_name=model_name)]
    lines.extend(_data_dir_lines(data_dir))
    if data_dir is None:
        lines.append("")
    lines.append("def create_fit_experiment(q, dq, data, errors):")
    lines.append('    """Build an analyzer-convention refl1d Experiment.')
    lines.append("")
    lines.append("    Parameters")
    lines.append("    ----------")
    lines.append("    q, dq, data, errors : array-like")
    lines.append("        Columns Q, dQ, R, dR from the data file. dq is assumed to be FWHM;")
    lines.append("        it is converted to 1-sigma internally.")
    lines.append('    """')
    lines.append("    # Go from FWHM to 1-sigma")
    lines.append("    dq = dq / 2.355")
    lines.append("    probe = QProbe(q, dq, data=(data, errors))")
    lines.append(
        f"    probe.intensity = Parameter(value={_format_float(spec.intensity['value'])}, "
        f'name="intensity")'
    )
    lines.append(
        f"    probe.intensity.range({_format_float(spec.intensity['min'])}, "
        f"{_format_float(spec.intensity['max'])})"
    )
    lines.append("")
    lines.extend(_materials_lines(spec, "    "))
    lines.append("")
    lines.append(_stack_line(spec, "    ", back_reflection=spec.back_reflection))
    lines.append("")
    lines.append("    experiment = Experiment(probe=probe, sample=sample)")
    lines.append("")
    lines.append("    # Parameter ranges")
    lines.extend(_range_lines(
        spec,
        "    ",
        include_ambient_interface=spec.back_reflection,
        include_substrate_interface=not spec.back_reflection,
    ))
    lines.append("")
    lines.append("    return experiment")
    lines.append("")
    lines.append("")
    lines.append(f"data_file = {_data_file_ref(data_file, data_dir, data_dir_abs)}")
    lines.append("")
    lines.append("_refl = np.loadtxt(data_file).T")
    lines.append(
        "experiment = create_fit_experiment(_refl[0], _refl[3], _refl[1], _refl[2])"
    )
    lines.append("")
    lines.append("problem = FitProblem(experiment)")
    lines.append("")
    return "\n".join(lines)


def render_case2_script(
    spec: ModelSpec,
    data_files: Sequence[Path | str],
    thetas: Sequence[float],
    *,
    model_name: str = "model",
    data_dir: Optional[Path | str] = None,
    data_dir_abs: Optional[Path | str] = None,
) -> str:
    """Render a case-2 (multi-segment partials, make_probe) script."""
    if len(data_files) != len(thetas):
        raise ValueError("data_files and thetas must be the same length")

    lines: List[str] = [_HEADER.format(model_name=model_name)]
    lines.append("from refl1d.probe import make_probe")
    lines.extend(_data_dir_lines(data_dir))
    lines.append("")
    lines.append("def create_probe(data_file, theta):")
    lines.append('    """Build an angle-based probe from one REF_L partial file."""')
    lines.append("    q, data, errors, dq = np.loadtxt(data_file).T")
    lines.append("    wl = 4 * np.pi * np.sin(np.pi / 180 * theta) / q")
    lines.append("    dT = dq / q * np.tan(np.pi / 180 * theta) * 180 / np.pi")
    lines.append("    dL = 0 * q  # wavelength resolution placeholder")
    lines.append("    probe = make_probe(")
    lines.append("        T=theta, dT=dT, L=wl, dL=dL,")
    lines.append("        data=(data, errors),")
    lines.append('        radiation="neutron",')
    lines.append('        resolution="uniform",')
    lines.append("    )")
    lines.append(
        f"    probe.intensity = Parameter(value={_format_float(spec.intensity['value'])}, "
        f'name="intensity")'
    )
    lines.append(
        f"    probe.intensity.range({_format_float(spec.intensity['min'])}, "
        f"{_format_float(spec.intensity['max'])})"
    )
    lines.append("    return probe")
    lines.append("")
    lines.append("")
    lines.append("def create_sample():")
    lines.append('    """Build the shared sample stack (one stack, all probes)."""')
    lines.extend(_materials_lines(spec, "    "))
    lines.append("")
    lines.append(_stack_line(spec, "    ", back_reflection=spec.back_reflection))
    lines.append("")
    lines.append("    # Parameter ranges")
    lines.extend(_range_lines(
        spec,
        "    ",
        include_ambient_interface=spec.back_reflection,
        include_substrate_interface=not spec.back_reflection,
    ))
    lines.append("")
    lines.append("    return sample")
    lines.append("")
    lines.append("")
    for i, path in enumerate(data_files, start=1):
        lines.append(f"data_file{i} = {_data_file_ref(path, data_dir, data_dir_abs)}")
    lines.append("")
    lines.append("sample = create_sample()")
    lines.append("")
    probe_names: List[str] = []
    for i, theta in enumerate(thetas, start=1):
        probe_names.append(f"probe{i}")
        lines.append(
            f"probe{i} = create_probe(data_file{i}, theta={_format_float(theta)})"
        )
    lines.append("")
    experiment_names: List[str] = []
    for i, pname in enumerate(probe_names, start=1):
        ename = "experiment" if i == 1 else f"experiment{i}"
        experiment_names.append(ename)
        lines.append(f"{ename} = Experiment(probe={pname}, sample=sample)")
    lines.append("")
    lines.append(
        "# To enable shared sample_broadening / theta_offset, uncomment:"
    )
    lines.append(f"# {probe_names[0]}.sample_broadening.range(0.0, 0.5)")
    for other in probe_names[1:]:
        lines.append(
            f"# {other}.sample_broadening = {probe_names[0]}.sample_broadening"
        )
    lines.append(f"# {probe_names[0]}.theta_offset.range(-0.02, 0.02)")
    for other in probe_names[1:]:
        lines.append(
            f"# {other}.theta_offset = {probe_names[0]}.theta_offset"
        )
    lines.append("")
    lines.append("problem = FitProblem(" + experiment_names[0] + ")")
    lines.append("")
    return "\n".join(lines)


_SHARED_PATH_RE = re.compile(
    r"^\s*(?P<layer>[^.\s]+)\.(?P<attr>material\.rho|thickness|interface)\s*$"
)


def _shared_constraint_line(i: int, path: str) -> Optional[str]:
    m = _SHARED_PATH_RE.match(path)
    if not m:
        return None
    layer = m.group("layer")
    attr = m.group("attr")
    expt_i = f"experiment{i}" if i > 1 else "experiment"  # not used for i==1
    return (
        f'{expt_i}.sample[{layer!r}].{attr} = '
        f'experiment.sample[{layer!r}].{attr}'
    )


def render_case3_script(
    spec: ModelSpec,
    data_files: Sequence[Path | str],
    *,
    model_name: str = "model",
    data_dir: Optional[Path | str] = None,
    data_dir_abs: Optional[Path | str] = None,
) -> str:
    """Render a case-3 (multiple combined files, co-refined) script."""
    if len(data_files) < 2:
        raise ValueError("case 3 requires at least two data files")

    lines: List[str] = [_HEADER.format(model_name=model_name)]
    lines.extend(_data_dir_lines(data_dir))
    if data_dir is None:
        lines.append("")
    lines.append("def create_fit_experiment(q, dq, data, errors):")
    lines.append('    """Build a refl1d Experiment with an INDEPENDENT sample copy.')
    lines.append("")
    lines.append("    Each experiment gets its own sample stack; shared structural")
    lines.append("    parameters are tied explicitly below with assignments of the")
    lines.append("    form ``experimentN.sample[\"Layer\"].attr = experiment.sample[...]``.")
    lines.append('    """')
    lines.append("    dq = dq / 2.355  # FWHM → 1-sigma")
    lines.append("    probe = QProbe(q, dq, data=(data, errors))")
    lines.append(
        f"    probe.intensity = Parameter(value={_format_float(spec.intensity['value'])}, "
        f'name="intensity")'
    )
    lines.append(
        f"    probe.intensity.range({_format_float(spec.intensity['min'])}, "
        f"{_format_float(spec.intensity['max'])})"
    )
    lines.append("")
    lines.extend(_materials_lines(spec, "    "))
    lines.append("")
    lines.append(_stack_line(spec, "    ", back_reflection=spec.back_reflection))
    lines.append("")
    lines.append("    experiment = Experiment(probe=probe, sample=sample)")
    lines.append("")
    lines.append("    # Parameter ranges")
    lines.extend(_range_lines(
        spec,
        "    ",
        include_ambient_interface=spec.back_reflection,
        include_substrate_interface=not spec.back_reflection,
    ))
    lines.append("")
    lines.append("    return experiment")
    lines.append("")
    lines.append("")
    for i, path in enumerate(data_files, start=1):
        lines.append(f"data_file{i} = {_data_file_ref(path, data_dir, data_dir_abs)}")
    lines.append("")
    experiment_names: List[str] = []
    for i in range(1, len(data_files) + 1):
        ename = "experiment" if i == 1 else f"experiment{i}"
        experiment_names.append(ename)
        lines.append(f"_refl = np.loadtxt(data_file{i}).T")
        lines.append(
            f"{ename} = create_fit_experiment(_refl[0], _refl[3], _refl[1], _refl[2])"
        )
    lines.append("")
    if spec.shared_parameters:
        lines.append("# Shared structural parameters across experiments")
        for i in range(2, len(data_files) + 1):
            for path in spec.shared_parameters:
                constraint = _shared_constraint_line(i, path)
                if constraint is not None:
                    lines.append(constraint)
        lines.append("")
    lines.append(
        "problem = FitProblem([" + ", ".join(experiment_names) + "])"
    )
    lines.append("")
    return "\n".join(lines)


def render_script(
    case: str,
    spec: ModelSpec,
    data_files: Sequence[Path | str],
    *,
    thetas: Optional[Sequence[float]] = None,
    model_name: str = "model",
    data_dir: Optional[Path | str] = None,
    data_dir_abs: Optional[Path | str] = None,
) -> str:
    if case == CASE_1:
        return render_case1_script(
            spec,
            data_files[0],
            model_name=model_name,
            data_dir=data_dir,
            data_dir_abs=data_dir_abs,
        )
    if case == CASE_2:
        if thetas is None:
            raise ValueError("case 2 rendering requires thetas")
        return render_case2_script(
            spec,
            data_files,
            thetas,
            model_name=model_name,
            data_dir=data_dir,
            data_dir_abs=data_dir_abs,
        )
    if case == CASE_3:
        return render_case3_script(
            spec,
            data_files,
            model_name=model_name,
            data_dir=data_dir,
            data_dir_abs=data_dir_abs,
        )
    raise ValueError(f"Unknown case {case!r}")


# ---------------------------------------------------------------------------
# Multi-state rendering (YAML "states:" form)
# ---------------------------------------------------------------------------


def _state_var(state_name: str) -> str:
    """Safe Python identifier derived from a state name."""
    base = "".join(c if c.isalnum() or c == "_" else "_" for c in state_name)
    if not base or not (base[0].isalpha() or base[0] == "_"):
        base = f"state_{base}"
    return base


def build_states_llm_prompt(
    description: str,
    states: Sequence[StateSpec],
    *,
    required_layer_names: Optional[Sequence[str]] = None,
) -> List[Dict[str, str]]:
    """Build the LLM prompt for the multi-state path.

    ``required_layer_names`` (when provided) is a list of layer names the
    user has already referenced in their ``shared_parameters`` /
    ``unshared_parameters`` YAML. These are passed to the LLM as a hard
    constraint so the generated layer stack uses the same names — without
    this, the LLM might pick e.g. ``Copper`` while the YAML says ``Cu``,
    and the renderer's ``sample['Cu']`` lookup would fail at fit time.
    """
    blocks: List[str] = []
    for s in states:
        file_lines: List[str] = []
        for p in s.data_files:
            header = parse_refl_header(p)
            runs = header.get("runs") or []
            run_str = ", ".join(f"2θ={r['two_theta']:.3f}°" for r in runs) or "—"
            file_lines.append(f"    * {p.name}  [{run_str}]")
        head = f"- State {s.name!r} ({s.kind}, {len(s.data_files)} file(s))"
        if s.extra_description:
            head += f" — {s.extra_description}"
        head += ":"
        blocks.append(head + "\n" + "\n".join(file_lines))
    states_block = "\n".join(blocks)

    shared_instr = (
        "You may leave 'shared_parameters' empty — the caller will fill it in "
        "from YAML. If the description makes it obvious which layers change "
        "between states, you MAY suggest a default list."
    )

    name_constraint = ""
    if required_layer_names:
        names_csv = ", ".join(repr(n) for n in required_layer_names)
        name_constraint = (
            "REQUIRED LAYER NAMES: the user has already referenced specific "
            "layer names in their configuration. Your 'layers' (and the "
            "substrate when listed) MUST use EXACTLY these names — same "
            "spelling, same case — so downstream code can index into them: "
            f"{names_csv}. You may add additional layers with names of your "
            "choosing, but every name listed here MUST appear.\n\n"
        )

    user = (
        f"Sample description (from the user):\n{description.strip()}\n\n"
        f"This is a MULTI-STATE co-refinement of {len(states)} state(s):\n"
        f"{states_block}\n\n"
        "Produce ONE shared layer stack that describes the sample. Each state "
        "gets its own probe(s); structural parameters will be tied across "
        "states by the caller.\n\n"
        f"{name_constraint}"
        f"{shared_instr}\n\n"
        f"{_JSON_SCHEMA_DESCRIPTION}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _states_sample_fn(spec: ModelSpec) -> List[str]:
    """Emit a ``def create_sample(back_reflection):`` helper that returns a stack.

    Each state in a multi-state co-refinement calls this with its own
    ``back_reflection`` flag. The flag controls layer ORDER only — the
    probe's ``back_reflectivity`` is never touched, so refl1d's default
    interpretation (``sample[0]`` = substrate / beam exit, ``sample[-1]`` =
    surface / beam entry) applies uniformly.
    """
    lines: List[str] = ["def create_sample(back_reflection=False):"]
    lines.append(
        '    """Build a fresh sample stack for one state.'
    )
    lines.append("")
    lines.append(
        "    Every probe in a given state is constructed with this stack,"
    )
    lines.append(
        "    so all structural parameters (thickness / SLD / interface) are"
    )
    lines.append(
        "    automatically tied across that state's segments via Python"
    )
    lines.append(
        "    object identity. Each state gets its OWN call, so structural"
    )
    lines.append(
        "    parameters are independent across states unless explicitly"
    )
    lines.append('    tied below with ``sample_B[\'X\'].attr = sample_A[\'X\'].attr``.')
    lines.append("")
    lines.append(
        "    ``back_reflection`` selects stack orientation so the default"
    )
    lines.append(
        "    ``probe.back_reflectivity=False`` gives correct physics in both"
    )
    lines.append("    buried-interface and standard front-reflection geometries.")
    lines.append('    """')
    lines.extend(_materials_lines(spec, "    "))
    lines.append("")
    amb_iface = _ambient_interface_line(spec, "        ")
    sub_iface = _substrate_interface_line(spec, "        ")
    lines.append("    if back_reflection:")
    lines.append(_stack_line(spec, "        ", back_reflection=True))
    if amb_iface is not None:
        lines.append(amb_iface)
    lines.append("    else:")
    lines.append(_stack_line(spec, "        ", back_reflection=False))
    if sub_iface is not None:
        lines.append(sub_iface)
    lines.append("")
    lines.append("    # Parameter ranges")
    lines.extend(_range_lines(
        spec,
        "    ",
        include_ambient_interface=False,
        include_substrate_interface=False,
    ))
    lines.append("")
    lines.append("    return sample")
    return lines


def _states_probe_helpers(spec: ModelSpec, need_make_probe: bool) -> List[str]:
    """Emit probe constructor helpers (QProbe / make_probe wrappers)."""
    lines: List[str] = []
    # Combined: QProbe helper.
    lines.append("def create_q_probe(data_file):")
    lines.append('    """Angle-independent probe for a combined REF_L file."""')
    lines.append("    q, data, errors, dq = np.loadtxt(data_file).T")
    lines.append("    dq = dq / 2.355  # FWHM → 1-sigma")
    lines.append("    probe = QProbe(q, dq, data=(data, errors))")
    lines.append(
        f"    probe.intensity = Parameter(value={_format_float(spec.intensity['value'])}, "
        f'name="intensity")'
    )
    lines.append(
        f"    probe.intensity.range({_format_float(spec.intensity['min'])}, "
        f"{_format_float(spec.intensity['max'])})"
    )
    lines.append("    return probe")
    lines.append("")
    if need_make_probe:
        lines.append("def create_angle_probe(data_file, theta):")
        lines.append('    """Angle-based probe from one REF_L partial file."""')
        lines.append("    q, data, errors, dq = np.loadtxt(data_file).T")
        lines.append("    wl = 4 * np.pi * np.sin(np.pi / 180 * theta) / q")
        lines.append("    dT = dq / q * np.tan(np.pi / 180 * theta) * 180 / np.pi")
        lines.append("    dL = 0 * q  # wavelength resolution placeholder")
        lines.append("    probe = make_probe(")
        lines.append("        T=theta, dT=dT, L=wl, dL=dL,")
        lines.append("        data=(data, errors),")
        lines.append('        radiation="neutron",')
        lines.append('        resolution="uniform",')
        lines.append("    )")
        lines.append(
            f"    probe.intensity = Parameter(value={_format_float(spec.intensity['value'])}, "
            f'name="intensity")'
        )
        lines.append(
            f"    probe.intensity.range({_format_float(spec.intensity['min'])}, "
            f"{_format_float(spec.intensity['max'])})"
        )
        lines.append("    return probe")
        lines.append("")
    return lines


def _shared_assignment(first_sample: str, other_sample: str, path: str) -> Optional[str]:
    m = _SHARED_PATH_RE.match(path)
    if not m:
        return None
    layer = m.group("layer")
    attr = m.group("attr")
    return (
        f'{other_sample}[{layer!r}].{attr} = {first_sample}[{layer!r}].{attr}'
    )


def render_states_script(
    spec: ModelSpec,
    states: Sequence[StateSpec],
    *,
    model_name: str = "model",
    shared_parameters: Optional[Sequence[str]] = None,
    data_dir: Optional[Path | str] = None,
    data_dir_abs: Optional[Path | str] = None,
) -> str:
    """Render a multi-state co-refinement script.

    Each state yields one Experiment per data file (all experiments in a
    partial-kind state share the state's sample, theta_offset and
    sample_broadening). Structural parameters listed in
    ``shared_parameters`` are tied across states.
    """
    if not states:
        raise ValueError("At least one state is required.")

    need_make_probe = any(s.kind == STATE_PARTIALS for s in states)

    lines: List[str] = [_HEADER.format(model_name=model_name)]
    if need_make_probe:
        lines.append("from refl1d.probe import make_probe")
    lines.extend(_data_dir_lines(data_dir))
    lines.append("")
    lines.extend(_states_sample_fn(spec))
    lines.append("")
    lines.append("")
    lines.extend(_states_probe_helpers(spec, need_make_probe))
    lines.append("")

    experiment_names: List[str] = []
    state_samples: List[str] = []  # name of the sample variable per state

    for s in states:
        svar = _state_var(s.name)
        sample_var = f"sample_{svar}"
        state_samples.append(sample_var)
        lines.append(f"# ── State: {s.name} ({s.kind}) ─────────────────────────")
        lines.append(
            f"# All probes in this state share {sample_var}, so every structural"
        )
        lines.append(
            "# parameter (thickness, SLD, roughness) is tied across this state's"
        )
        lines.append("# segments by Python object identity — no explicit ties needed.")
        back_refl = (
            s.back_reflection
            if s.back_reflection is not None
            else bool(spec.back_reflection)
        )
        lines.append(f"{sample_var} = create_sample(back_reflection={back_refl!r})")

        # Nuisance parameters shared across segments within this state.
        theta_var: Optional[str] = None
        sb_var: Optional[str] = None
        if s.theta_offset is not None:
            theta_var = f"theta_offset_{svar}"
            lines.append(
                f'{theta_var} = Parameter(value={_format_float(s.theta_offset["init"])}, '
                f'name="theta_offset_{svar}")'
            )
            lines.append(
                f"{theta_var}.range({_format_float(s.theta_offset['min'])}, "
                f"{_format_float(s.theta_offset['max'])})"
            )
        if s.sample_broadening is not None:
            sb_var = f"sample_broadening_{svar}"
            lines.append(
                f'{sb_var} = Parameter(value={_format_float(s.sample_broadening["init"])}, '
                f'name="sample_broadening_{svar}")'
            )
            lines.append(
                f"{sb_var}.range({_format_float(s.sample_broadening['min'])}, "
                f"{_format_float(s.sample_broadening['max'])})"
            )

        for i, data_file in enumerate(s.data_files, start=1):
            pvar = f"probe_{svar}_{i}" if s.kind == STATE_PARTIALS else f"probe_{svar}"
            evar = f"experiment_{svar}_{i}" if s.kind == STATE_PARTIALS else f"experiment_{svar}"
            file_ref = _data_file_ref(data_file, data_dir, data_dir_abs)
            if s.kind == STATE_COMBINED:
                lines.append(f"{pvar} = create_q_probe({file_ref})")
            else:
                theta = s.thetas[i - 1]
                lines.append(
                    f"{pvar} = create_angle_probe({file_ref}, "
                    f"theta={_format_float(theta)})"
                )
            if theta_var is not None:
                lines.append(f"{pvar}.theta_offset = {theta_var}")
            if sb_var is not None:
                lines.append(f"{pvar}.sample_broadening = {sb_var}")
            lines.append(f"{evar} = Experiment(probe={pvar}, sample={sample_var})")
            experiment_names.append(evar)
        lines.append("")

    # Shared-parameter constraints across states.
    effective_shared = list(shared_parameters) if shared_parameters else []
    if len(states) > 1 and effective_shared:
        lines.append("# ── Shared structural parameters across states ──")
        first = state_samples[0]
        for other in state_samples[1:]:
            for path in effective_shared:
                assignment = _shared_assignment(first, other, path)
                if assignment is not None:
                    lines.append(assignment)
        lines.append("")

    lines.append("problem = FitProblem([" + ", ".join(experiment_names) + "])")
    lines.append("")
    return "\n".join(lines)


def generate_model_script_from_states(
    description: str,
    states: Sequence[StateSpec],
    *,
    model_name: str = "model",
    shared_parameters: Optional[List[str]] = None,
    unshared_parameters: Optional[List[str]] = None,
    data_dir: Optional[Path | str] = None,
    data_dir_abs: Optional[Path | str] = None,
    llm: Any = None,
) -> str:
    """High-level entry point for the multi-state (YAML ``states:``) path."""
    if not states:
        raise ValueError("At least one state is required.")
    # Collect any layer names referenced in the user's shared/unshared
    # parameter lists so the LLM is forced to use them (and the renderer's
    # sample[<name>] lookups resolve correctly).
    required_names: List[str] = []
    if shared_parameters:
        required_names.extend(_layer_names_in_paths(shared_parameters))
    if unshared_parameters:
        for name in _layer_names_in_paths(unshared_parameters):
            if name not in required_names:
                required_names.append(name)
    messages = build_states_llm_prompt(
        description, states,
        required_layer_names=required_names or None,
    )
    spec = call_llm_for_model_spec(messages, llm=llm)
    effective = resolve_shared_parameters(
        spec, shared=shared_parameters, unshared=unshared_parameters
    )
    return render_states_script(
        spec,
        states,
        model_name=model_name,
        shared_parameters=effective,
        data_dir=data_dir,
        data_dir_abs=data_dir_abs,
    )


# ---------------------------------------------------------------------------
# Top-level orchestration (used by CLI)
# ---------------------------------------------------------------------------


def generate_model_script(
    description: str,
    data_files: Sequence[Path | str],
    *,
    model_name: str = "model",
    data_dir: Optional[Path | str] = None,
    data_dir_abs: Optional[Path | str] = None,
    llm: Any = None,
) -> str:
    """High-level entry point: files → header parsing → LLM → script."""
    paths = [Path(f) for f in data_files]
    case = detect_case(paths)
    headers = [parse_refl_header(p) for p in paths]
    messages = build_llm_prompt(case, description, paths, headers)
    spec = call_llm_for_model_spec(messages, llm=llm)

    thetas: Optional[List[float]] = None
    if case == CASE_2:
        # Pull theta from each partial file's single header row; fall back to
        # filename order if any row is missing.
        thetas = []
        for h in headers:
            runs = h.get("runs") or []
            if not runs:
                raise ValueError(
                    "Case 2 requires a 2θ entry in every partial file header; "
                    "one of the files has an empty header table."
                )
            thetas.append(float(runs[0]["theta"]))
    return render_script(
        case,
        spec,
        paths,
        thetas=thetas,
        model_name=model_name,
        data_dir=data_dir,
        data_dir_abs=data_dir_abs,
    )
