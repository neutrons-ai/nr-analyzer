"""
Bridge between AuRE-compatible JSON inputs and analyzer-convention refl1d scripts.

The analyzer convention is a Python module that defines::

    def create_fit_experiment(q, dq, data, errors) -> refl1d.Experiment

This module is Mode A of ``create-model``: it converts either

* a raw AuRE ``ModelDefinition`` JSON (keys: ``substrate`` / ``ambient`` /
  ``layers`` / ``intensity`` / ``dq_is_fwhm``), or
* a bumps ``problem.json`` (schema ``bumps-draft-03``) produced by
  ``aure prepare``

into an analyzer-convention model script.  Mode B (LLM-driven script
generation from a natural-language description) lives in
:mod:`analyzer_tools.analysis.model_generator`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# ModelDefinition → analyzer-convention script
# ---------------------------------------------------------------------------


def _safe_identifier(name: str, fallback: str) -> str:
    """Convert *name* into a valid Python identifier suitable for dict keys."""
    ident = "".join(c if c.isalnum() or c == "_" else "_" for c in name.strip())
    if not ident or not (ident[0].isalpha() or ident[0] == "_"):
        ident = fallback
    return ident


def _range_pair(layer: Dict[str, Any], key: str, default_min: float, default_max: float) -> tuple[float, float]:
    """Return (min, max) range for *key* (thickness/sld/roughness) with fallbacks."""
    lo = layer.get(f"{key}_min", default_min)
    hi = layer.get(f"{key}_max", default_max)
    return float(lo), float(hi)


def definition_to_script(
    definition: Dict[str, Any],
    *,
    model_name: str = "model",
    data_files: Optional[List[Any]] = None,
) -> str:
    """Convert an AuRE ModelDefinition to an analyzer-convention refl1d script.

    The returned script has two shapes, depending on ``data_files``:

    * **Single-file / Q-based mode** — emits a classic
      ``create_fit_experiment(q, dq, data, errors)`` function that wraps
      the data in a ``QProbe`` and returns a refl1d ``Experiment``.
    * **Multi-segment angle-based mode** — when ``data_files`` contains
      dicts with ``file`` + ``theta`` (one per segment), emits a
      ``create_sample()`` plus ``create_probe(data_file, theta)`` pair.
      A single shared ``sample`` is built once and reused across probes
      (so every structural parameter is tied automatically), while each
      probe carries its own ``intensity``.  ``sample_broadening`` and
      ``theta_offset`` parameters are exposed on each probe and tied to a
      single shared parameter when enabled in the definition.

    Parameters
    ----------
    definition
        ModelDefinition dict with keys: ``substrate``, ``layers``, ``ambient``,
        optional ``intensity``, ``dq_is_fwhm``, ``sample_broadening``,
        ``theta_offset``.
    model_name
        Used only in the module docstring.
    data_files
        Optional list of reflectivity data files.  Each entry may be a
        string (path) or a dict with ``file``, optional ``theta`` (deg)
        and ``dq_is_fwhm``.
    """
    substrate = definition["substrate"]
    ambient = definition["ambient"]
    layers = definition.get("layers", [])
    intensity = definition.get("intensity", {}) or {}
    dq_is_fwhm = bool(definition.get("dq_is_fwhm", True))
    broadening = definition.get("sample_broadening", {}) or {}
    theta_off = definition.get("theta_offset", {}) or {}

    sub_name = _safe_identifier(substrate["name"], "substrate")
    amb_name = _safe_identifier(ambient["name"], "ambient")

    # Build unique layer identifiers (avoid collisions with substrate/ambient).
    used = {sub_name, amb_name}
    layer_names: list[str] = []
    for i, layer in enumerate(layers):
        base = _safe_identifier(layer["name"], f"layer{i + 1}")
        candidate = base
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        layer_names.append(candidate)

    # Normalise data_files entries into dicts and decide which mode to emit.
    norm_files: list[dict] = []
    for ds in data_files or []:
        if isinstance(ds, dict):
            norm_files.append({
                "file": str(ds.get("file", "")),
                "theta": ds.get("theta"),
                "dq_is_fwhm": ds.get("dq_is_fwhm", dq_is_fwhm),
            })
        else:
            norm_files.append({
                "file": str(ds),
                "theta": None,
                "dq_is_fwhm": dq_is_fwhm,
            })
    angle_mode = (
        len(norm_files) > 1
        and all(isinstance(f.get("theta"), (int, float)) and f["theta"] > 0 for f in norm_files)
    )

    def _sample_ranges_lines(indent: str) -> list[str]:
        out: list[str] = []
        if (
            ambient.get("name", "").lower() != "air"
            and ambient.get("sld", 0) != 0
            and ("sld_min" in ambient or "sld_max" in ambient)
        ):
            amb_min = float(ambient.get("sld_min", ambient["sld"] * 0.8))
            amb_max = float(ambient.get("sld_max", ambient["sld"] * 1.2))
            out.append(f"{indent}sample[{ambient['name']!r}].material.rho.range({amb_min!r}, {amb_max!r})")

        for layer in layers:
            t_min, t_max = _range_pair(layer, "thickness", float(layer["thickness"]) * 0.5, float(layer["thickness"]) * 2.0)
            s_min, s_max = _range_pair(layer, "sld", float(layer["sld"]) - 2.5, float(layer["sld"]) + 2.5)
            r_min, r_max = _range_pair(layer, "roughness", 5.0, 30.0)
            key = layer["name"]
            out.append(f"{indent}sample[{key!r}].thickness.range({t_min!r}, {t_max!r})")
            out.append(f"{indent}sample[{key!r}].material.rho.range({s_min!r}, {s_max!r})")
            out.append(f"{indent}sample[{key!r}].interface.range({r_min!r}, {r_max!r})")

        sub_rough_max = float(substrate.get("roughness_max", 15.0))
        out.append(f"{indent}sample[{substrate['name']!r}].interface.range(0.0, {sub_rough_max!r})")
        return out

    def _sample_stack_line(indent: str) -> str:
        # Stack order: refl1d convention is "ambient | ... | substrate".
        # Layers are stored substrate-adjacent first in the ModelDefinition,
        # so we iterate ``reversed(layers)`` to write the top-most layer
        # first.  Substrate is emitted bare (no params); the bottom-most
        # layer's ``interface`` carries the substrate-boundary roughness.
        parts = [f"{amb_name}(0, 5.0)"]
        for ident, layer in zip(reversed(layer_names), reversed(layers)):
            parts.append(f"{ident}({float(layer['thickness'])!r}, {float(layer.get('roughness', 5.0))!r})")
        parts.append(sub_name)
        return f"{indent}sample = " + " | ".join(parts)

    def _material_lines(indent: str) -> list[str]:
        out = [
            f"{indent}{sub_name} = SLD(name={substrate['name']!r}, rho={float(substrate['sld'])!r})",
            f"{indent}{amb_name} = SLD(name={ambient['name']!r}, rho={float(ambient['sld'])!r})",
        ]
        for ident, layer in zip(layer_names, layers):
            out.append(f"{indent}{ident} = SLD(name={layer['name']!r}, rho={float(layer['sld'])!r})")
        return out

    lines: list[str] = []
    lines.append(f'"""Auto-generated analyzer model ({model_name}) from AuRE ModelDefinition."""')
    lines.append("")
    lines.append("import os")
    lines.append("import numpy as np")
    lines.append("from bumps.fitters import fit")
    lines.append("from refl1d.names import *")
    if angle_mode:
        # make_probe lives in refl1d.probe and is re-exported through
        # refl1d.names in modern refl1d; import it explicitly to be robust.
        lines.append("from refl1d.probe import make_probe")
    lines.append("")
    lines.append("")

    # ------------------------------------------------------------------
    # Mode A: single-file / Q-based
    # ------------------------------------------------------------------
    if not angle_mode:
        lines.append("def create_fit_experiment(q, dq, data, errors):")
        lines.append('    """Build an analyzer-convention refl1d Experiment.')
        lines.append("")
        lines.append("    Parameters")
        lines.append("    ----------")
        lines.append("    q, dq, data, errors : array-like")
        lines.append("        Columns Q, dQ, R, dR from the data file. dq is assumed to be FWHM;")
        lines.append("        it is converted to 1-sigma internally.")
        lines.append('    """')
        if dq_is_fwhm:
            lines.append("    # Go from FWHM to 1-sigma")
            lines.append("    dq = dq / 2.355")
        lines.append("    probe = QProbe(q, dq, R=data, dR=errors)")

        if intensity.get("fixed", False):
            lines.append(
                f"    probe.intensity = Parameter(value={float(intensity.get('value', 1.0))!r}, name=\"intensity\")"
            )
        else:
            int_val = float(intensity.get("value", 1.0))
            int_min = float(intensity.get("min", 0.7))
            int_max = float(intensity.get("max", 1.1))
            lines.append(
                f"    probe.intensity = Parameter(value={int_val!r}, name=\"intensity\")"
            )
            lines.append(f"    probe.intensity.range({int_min!r}, {int_max!r})")

        lines.append("")
        lines.append("    # Materials")
        lines.extend(_material_lines("    "))
        lines.append("")
        lines.append("    # Sample stack")
        lines.append(_sample_stack_line("    "))
        lines.append("")
        lines.append("    experiment = Experiment(probe=probe, sample=sample)")
        lines.append("")
        lines.append("    # Parameter ranges")
        lines.extend(_sample_ranges_lines("    "))
        lines.append("")
        lines.append("    return experiment")
        lines.append("")

        if data_files:
            lines.append("")
            df = norm_files[0]["file"]
            lines.append(f"data_file = {df!r}")
            lines.append("")
            lines.append("_refl = np.loadtxt(data_file).T")
            lines.append("experiment = create_fit_experiment(_refl[0], _refl[3], _refl[1], _refl[2])")
            lines.append("")
            lines.append("problem = FitProblem(experiment)")
            lines.append("")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Mode B: multi-segment angle-based co-refinement
    # ------------------------------------------------------------------
    int_val = float(intensity.get("value", 1.0))
    int_min = float(intensity.get("min", 0.7))
    int_max = float(intensity.get("max", 1.1))
    int_fixed = bool(intensity.get("fixed", False))

    lines.append("def create_probe(data_file, theta):")
    lines.append('    """Build an angle-based NeutronProbe for one REF_L segment.')
    lines.append("")
    lines.append("    Converts the Q/dQ columns to per-point wavelength and angular")
    lines.append("    divergence and builds a uniform-resolution probe via make_probe.")
    lines.append("    Each probe carries its own intensity normalisation and, when")
    lines.append("    enabled in the definition, its own (tied) sample_broadening /")
    lines.append("    theta_offset parameters.")
    lines.append('    """')
    lines.append("    q, data, errors, dq = np.loadtxt(data_file).T")
    lines.append("    wl = 4 * np.pi * np.sin(np.pi / 180 * theta) / q")
    lines.append("    dT = dq / q * np.tan(np.pi / 180 * theta) * 180 / np.pi")
    lines.append("    dL = 0 * q  # wavelength resolution placeholder")
    lines.append("    probe = make_probe(")
    lines.append("        T=theta,")
    lines.append("        dT=dT,")
    lines.append("        L=wl,")
    lines.append("        dL=dL,")
    lines.append("        data=(data, errors),")
    lines.append('        radiation="neutron",')
    lines.append('        resolution="uniform",')
    lines.append("    )")
    if int_fixed:
        lines.append(
            f"    probe.intensity = Parameter(value={int_val!r}, name=\"intensity\")"
        )
    else:
        lines.append(
            f"    probe.intensity = Parameter(value={int_val!r}, name=\"intensity\")"
        )
        lines.append(f"    probe.intensity.range({int_min!r}, {int_max!r})")
    lines.append("    return probe")
    lines.append("")
    lines.append("")

    lines.append("def create_sample():")
    lines.append('    """Build the shared refl1d Sample stack with parameter ranges.')
    lines.append("")
    lines.append("    A single Sample is shared across all probes in the co-refinement,")
    lines.append("    so every structural parameter (thickness, SLD, roughness) is")
    lines.append("    automatically tied between experiments. Each probe contributes")
    lines.append("    its own intensity normalisation.")
    lines.append('    """')
    lines.append("    # Materials")
    lines.extend(_material_lines("    "))
    lines.append("")
    lines.append("    # Sample stack")
    lines.append(_sample_stack_line("    "))
    lines.append("")
    lines.append("    # Parameter ranges")
    lines.extend(_sample_ranges_lines("    "))
    lines.append("")
    lines.append("    return sample")
    lines.append("")
    lines.append("")

    # Module-level assembly
    for i, ds in enumerate(norm_files, start=1):
        lines.append(f"data_file{i} = {ds['file']!r}")
    lines.append("")
    lines.append("sample = create_sample()")
    lines.append("")

    probe_names: list[str] = []
    for i, ds in enumerate(norm_files, start=1):
        probe_names.append(f"probe{i}")
        lines.append(f"probe{i} = create_probe(data_file{i}, theta={float(ds['theta'])!r})")
    lines.append("")

    experiment_names: list[str] = []
    for i, probe in enumerate(probe_names, start=1):
        expt_name = "experiment" if i == 1 else f"experiment{i}"
        experiment_names.append(expt_name)
        lines.append(f"{expt_name} = Experiment(probe={probe}, sample=sample)")
    lines.append("")

    # sample_broadening / theta_offset — shared across all probes.
    br_enabled = bool(broadening.get("enabled"))
    to_enabled = bool(theta_off.get("enabled"))
    if br_enabled or to_enabled:
        lines.append("# Shared sample_broadening / theta_offset across all probes")
        lines.append("# (set on the first probe; subsequent probes are aliased to the same Parameter).")
    if br_enabled:
        br_min = float(broadening.get("min", 0.0))
        br_max = float(broadening.get("max", 0.5))
        lines.append(f"{probe_names[0]}.sample_broadening.range({br_min!r}, {br_max!r})")
        for other in probe_names[1:]:
            lines.append(f"{other}.sample_broadening = {probe_names[0]}.sample_broadening")
    if to_enabled:
        to_min = float(theta_off.get("min", -0.02))
        to_max = float(theta_off.get("max", 0.02))
        lines.append(f"{probe_names[0]}.theta_offset.range({to_min!r}, {to_max!r})")
        for other in probe_names[1:]:
            lines.append(f"{other}.theta_offset = {probe_names[0]}.theta_offset")
    if not br_enabled:
        lines.append(
            "# To enable sample_broadening for the co-refinement, uncomment:"
        )
        lines.append(
            f"# {probe_names[0]}.sample_broadening.range(0.0, 0.5)"
        )
        for other in probe_names[1:]:
            lines.append(
                f"# {other}.sample_broadening = {probe_names[0]}.sample_broadening"
            )
    if not to_enabled:
        lines.append(
            "# To enable theta_offset for the co-refinement, uncomment:"
        )
        lines.append(
            f"# {probe_names[0]}.theta_offset.range(-0.02, 0.02)"
        )
        for other in probe_names[1:]:
            lines.append(
                f"# {other}.theta_offset = {probe_names[0]}.theta_offset"
            )
    lines.append("")
    lines.append(
        "problem = FitProblem([" + ", ".join(experiment_names) + "])"
    )
    lines.append("")

    return "\n".join(lines)


def _bumps_problem_to_definition(
    data: Dict[str, Any],
) -> Tuple[Dict[str, Any], List[str]]:
    """Convert a bumps ``problem.json`` dict to a ModelDefinition + data file list.

    The bumps schema (``bumps-draft-03``) nests refl1d ``Experiment`` objects
    under ``object.models[]`` with parameters stored as ``Reference`` objects
    that resolve via the top-level ``references`` map. The layer order in
    ``sample.layers`` is top-to-bottom (ambient first, substrate last);
    each ``Slab.interface`` carries the roughness at the slab's boundary
    with the next layer below.

    The returned ModelDefinition follows the analyzer convention where
    ``layers[0]`` is the bottom-most (substrate-adjacent) layer, i.e. the
    middle layers are stored in reverse stack order.
    """
    obj = data.get("object") or {}
    models = obj.get("models") or []
    if not models:
        raise ValueError("bumps problem.json has no models under 'object.models'")

    refs = data.get("references") or {}

    def _resolve(ref: Any) -> Dict[str, Any]:
        if isinstance(ref, dict) and ref.get("__class__") == "Reference":
            return refs.get(ref.get("id"), {}) or {}
        return ref if isinstance(ref, dict) else {}

    def _value(ref: Any, default: float = 0.0) -> float:
        param = _resolve(ref)
        slot = param.get("slot") or {}
        val = slot.get("value")
        if val is None:
            return float(default)
        return float(val)

    def _bounds(ref: Any) -> Optional[Tuple[float, float]]:
        param = _resolve(ref)
        b = param.get("bounds")
        if not b or len(b) != 2:
            return None
        try:
            return float(b[0]), float(b[1])
        except (TypeError, ValueError):
            return None

    def _fixed(ref: Any) -> bool:
        return bool(_resolve(ref).get("fixed", False))

    sample = models[0].get("sample") or {}
    layers_raw = sample.get("layers") or []
    if len(layers_raw) < 2:
        raise ValueError(
            "bumps problem.json must have at least an ambient and a substrate layer"
        )

    ambient_layer = layers_raw[0]
    substrate_layer = layers_raw[-1]
    middle_layers = layers_raw[1:-1]

    ambient: Dict[str, Any] = {
        "name": ambient_layer["material"]["name"],
        "sld": _value(ambient_layer["material"]["rho"]),
    }
    amb_bounds = _bounds(ambient_layer["material"]["rho"])
    if amb_bounds is not None:
        ambient["sld_min"], ambient["sld_max"] = amb_bounds

    substrate: Dict[str, Any] = {
        "name": substrate_layer["material"]["name"],
        "sld": _value(substrate_layer["material"]["rho"]),
    }

    # Reverse to match analyzer ModelDefinition convention (layer0 = substrate-adjacent).
    defn_layers: List[Dict[str, Any]] = []
    for layer in reversed(middle_layers):
        entry: Dict[str, Any] = {
            "name": layer["material"]["name"],
            "sld": _value(layer["material"]["rho"]),
            "thickness": _value(layer["thickness"]),
            "roughness": _value(layer["interface"]),
        }
        tb = _bounds(layer["thickness"])
        if tb is not None:
            entry["thickness_min"], entry["thickness_max"] = tb
        sb = _bounds(layer["material"]["rho"])
        if sb is not None:
            entry["sld_min"], entry["sld_max"] = sb
        rb = _bounds(layer["interface"])
        if rb is not None:
            entry["roughness_min"], entry["roughness_max"] = rb
        defn_layers.append(entry)

    # Intensity is attached to the probe; pull it from the first model.
    probe = models[0].get("probe") or {}
    intensity: Dict[str, Any] = {}
    if "intensity" in probe:
        int_val = _value(probe["intensity"], default=1.0)
        int_bounds = _bounds(probe["intensity"])
        intensity["value"] = int_val
        intensity["fixed"] = _fixed(probe["intensity"])
        if int_bounds is not None:
            intensity["min"], intensity["max"] = int_bounds

    definition: Dict[str, Any] = {
        "substrate": substrate,
        "ambient": ambient,
        "layers": defn_layers,
        "intensity": intensity,
        # Data columns from REF_L combined files carry dQ as FWHM.
        "dq_is_fwhm": True,
    }

    # Collect data file paths, one per model (co-refinement).
    data_files: List[str] = []
    for m in models:
        probe_m = m.get("probe") or {}
        filename = probe_m.get("filename")
        if filename:
            data_files.append(str(filename))

    return definition, data_files


def load_definition(path: str | os.PathLike) -> Dict[str, Any]:
    """Load a ModelDefinition JSON from *path*.

    Accepts either:
    * A raw analyzer ModelDefinition JSON (keys: ``substrate``, ``layers``,
      ``ambient``), or
    * A bumps ``problem.json`` (schema ``bumps-draft-03``) produced by
      ``aure prepare`` / ``aure batch``. In that case the companion
      ``<stem>_definition.json`` sidecar (written alongside by AuRE) is
      preferred, since it contains the raw ``ModelDefinition`` plus the
      full ``data_files`` list with labels. When the sidecar is missing,
      fall back to parsing the bumps document directly.

    When multi-file co-refinement data is available (sidecar ``data_files``
    or bumps ``probe.filename`` entries), the resolved file paths are
    attached under the reserved key ``_data_files`` so
    :func:`write_model_script` can bake them into the script footer.
    """
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "substrate" in data:
        # Raw ModelDefinition. If it carries a list of DatasetInfo dicts under
        # ``data_files``, surface them as the reserved ``_data_files`` list
        # so the script-writer can include the loader footer (and, when per-
        # file ``theta`` is present, switch to the angle-based create_probe
        # pattern).
        if "_data_files" not in data:
            ds_list = data.get("data_files")
            if isinstance(ds_list, list) and ds_list:
                rich: List[Dict[str, Any]] = []
                for ds in ds_list:
                    if isinstance(ds, dict) and ds.get("file"):
                        rich.append({
                            "file": str(ds["file"]),
                            "theta": ds.get("theta"),
                            "dq_is_fwhm": ds.get("dq_is_fwhm"),
                        })
                    elif isinstance(ds, str):
                        rich.append({"file": ds})
                if rich:
                    data["_data_files"] = rich
        return data

    is_bumps_problem = data.get("$schema") == "bumps-draft-03" or (
        isinstance(data.get("object"), dict) and "models" in data["object"]
    )
    if is_bumps_problem:
        # Prefer the AuRE sidecar if present — it preserves the raw
        # ModelDefinition and the resolved data_files list (including
        # labels and theta), which the bumps document can drop (NeutronProbe's
        # serialised form does not retain ``filename``).
        sidecar = path.parent / (path.stem + "_definition.json")
        if sidecar.exists():
            with open(sidecar, "r", encoding="utf-8") as f:
                sidecar_data = json.load(f)
            ds_list = sidecar_data.get("data_files")
            if isinstance(ds_list, list) and ds_list:
                rich = []
                for ds in ds_list:
                    if isinstance(ds, dict) and ds.get("file"):
                        rich.append({
                            "file": str(ds["file"]),
                            "theta": ds.get("theta"),
                            "dq_is_fwhm": ds.get("dq_is_fwhm"),
                        })
                    elif isinstance(ds, str):
                        rich.append({"file": ds})
                if rich:
                    sidecar_data["_data_files"] = rich
            return sidecar_data

        definition, data_files = _bumps_problem_to_definition(data)
        if data_files:
            definition["_data_files"] = [{"file": df} for df in data_files]
        return definition

    raise KeyError(
        f"Could not parse {path} as an analyzer ModelDefinition or a bumps "
        f"problem.json. Expected either a 'substrate' key or an 'object.models' "
        f"section."
    )


def write_model_script(
    definition: Dict[str, Any],
    out_path: str | os.PathLike,
    *,
    model_name: Optional[str] = None,
    data_files: Optional[List[Any]] = None,
) -> str:
    """Write *definition* as an analyzer-convention script to *out_path*.

    Returns the absolute output path.
    """
    out = os.path.abspath(str(out_path))
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    name = model_name or Path(out).stem
    script = definition_to_script(definition, model_name=name, data_files=data_files)
    with open(out, "w", encoding="utf-8") as f:
        f.write(script)
    return out

