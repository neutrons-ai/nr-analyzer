---
name: create-model
description: >
  Generate a refl1d analyzer-convention model script. Two modes:
  (A) convert an existing AuRE problem JSON, or
  (B) generate directly via LLM from a YAML/JSON config that lists
  one or more measurement *states* (each grouping data files that share
  one physical sample). Mode B auto-detects per state whether its data
  is a single combined file or N partial files sharing a set_id, and
  ties structural parameters across states.
  USE FOR: creating a new model file; adapting a hand-written model for
  co-refinement of multiple measurements / conditions.
  DO NOT USE FOR: running fits (see fitting skill) or adjusting an existing
  model's parameter ranges (edit the script directly). For batch use
  across many samples, drive create-model from `analyzer-batch`.
---

# create-model

## When to use

- You have a **natural-language description** of a sample and one or more
  REF_L data files, and want a model script ready for `run-fit`.
- You already have an **AuRE problem JSON** (from `aure prepare` / `aure batch`)
  and want to convert it to an analyzer-convention script.

## The two modes

### Mode A — convert an AuRE problem JSON

```bash
create-model path/to/problem.json --out models/cu_thf.py
```

Accepts either an AuRE `ModelDefinition` JSON (keys `substrate`/`ambient`/
`layers`/`intensity`/`dq_is_fwhm`) or a bumps `problem.json` (schema
`bumps-draft-03`).

### Mode B — generate via LLM (states-driven config)

Mode B is always driven by a YAML or JSON config file passed via `--config`:

```bash
create-model --config model-creation.yaml [--out PATH] [--model-name NAME]
```

The config file's directory is the base for relative paths. CLI flags
`--out` and `--model-name` override the corresponding config keys.

## Per-state file kinds (auto-detected)

Within a single state, all data files must be the same kind:

| Kind | Files | Probe |
|------|-------|-------|
| combined | one `REFL_{set}_combined_data_auto.txt` | `QProbe` (Q, dQ) |
| partials | N `REFL_{set}_{part}_{run}_partial.txt` files sharing one set_id | `make_probe` per segment (θ, dT, λ, dL); one `Sample` shared across segments |

Mixing combined and partial files within a single state is rejected.
Different states can be of different kinds.

## Config schema

### Top-level keys

| Key | Aliases | Meaning |
|---|---|---|
| `describe` | `description`, `sample_description` | Sample description (required) |
| `states` | — | List of state mappings (required, non-empty) |
| `model_name` | `name` | Name in docstring and default filename |
| `out` | — | Output script path (relative to config dir) |
| `data_dir` | — | Emit `DATA_DIR = "<value>"` at the top of the generated script; file paths below become `os.path.join(DATA_DIR, ...)`. Relative values resolve against the config dir. |
| `shared_parameters` | — | Whitelist of dotted attribute paths tied across states |
| `unshared_parameters` | — | Blacklist subtracted from the default tied set (mutually exclusive with `shared_parameters`) |

Top-level `data:`, `data_file:`, `data_files:`, `source:`, `jobs:` and
`defaults:` are **rejected**. For Mode A, pass the JSON file as the
SOURCE positional argument. For batch processing, drive create-model
from `analyzer-batch` (see below).

### Per-state keys

| Key | Type | Meaning |
|---|---|---|
| `name` | string | Unique state label |
| `data` / `data_files` | list of paths | REF_L files (all combined OR all partials of one set_id) |
| `extra_description` | string | Text appended to `describe` when this state is presented to the LLM. Use it to record state-specific conditions (e.g. "in H₂O instead of D₂O", "after 30 min anneal"). |
| `theta_offset` | `false` / `true` / `{init, min, max}` | Per-state nuisance shared across the state's segments. Partials only. |
| `sample_broadening` | `false` / `true` / `{init, min, max}` | Same syntax as `theta_offset`. Partials only. |
| `back_reflection` | bool | Beam enters through the substrate (per state). Defaults to the LLM's answer derived from `describe`. |

Defaults when set to `true`: `theta_offset = {0.0, -0.02, 0.02}`,
`sample_broadening = {0.0, 0.0, 0.01}`.

### Example: same sample in two solvents

```yaml
# model-creation.yaml
describe: |
  2 nm CuOx / 50 nm Cu / 3 nm Ti on Si.
  Neutrons enter from the silicon side.

states:
  - name: run_226642_D2O
    extra_description: ambient is D₂O (SLD ≈ 6.4)
    data:
      - Rawdata/REFL_226642_1_226642_partial.txt
      - Rawdata/REFL_226642_2_226643_partial.txt
      - Rawdata/REFL_226642_3_226644_partial.txt
    theta_offset:      {init: 0.0, min: -0.02, max: 0.02}
    sample_broadening: true

  - name: run_226660_H2O
    extra_description: ambient is H₂O (SLD ≈ -0.56), measured 24 h later
    data: [Rawdata/REFL_226660_combined_data_auto.txt]
    back_reflection: true

shared_parameters:
  - Cu.thickness
  - Cu.material.rho
  - Cu.interface
  - Ti.thickness
  - Ti.material.rho
  - Ti.interface
# unshared_parameters: [CuOx.thickness]   # mutually exclusive with the above

out: Models/Cu-corefine.py
model_name: cu_corefine
```

```bash
create-model --config model-creation.yaml
```

## Rules

- **Within a state, every structural parameter is tied across the state's
  data files.** The renderer creates one `Sample` per state and reuses it
  for every probe in that state. Per-state `theta_offset` /
  `sample_broadening` are likewise single `Parameter` objects shared by
  every probe of the state.
- A state's data files must all be the same kind; mixing combined and
  partial within one state is rejected.
- `theta_offset` and `sample_broadening` are only allowed on partial-kind
  states.
- `back_reflection` controls **stack orientation only** — the renderer
  emits the layer pipe-expression in the correct order so refl1d's
  default `probe.back_reflectivity = False` always gives correct physics.
- `shared_parameters` and `unshared_parameters` are mutually exclusive.
  When neither is set, a sensible default (every layer.thickness,
  layer.material.rho, layer.interface, plus substrate.interface) is shared.
- Layer prefixes in `shared_parameters` / `unshared_parameters` (e.g. the
  `Cu` in `Cu.thickness`) are forwarded to the LLM as **required layer
  names** so the generated model uses the exact spelling and case you
  wrote. If a path's prefix doesn't match any layer the LLM produced (or
  the substrate name), `create-model` aborts with a clear error rather
  than emitting a script that crashes at fit time.

## Batch use across many samples

`create-model` itself does not iterate over a list of jobs. To create
many models in one shot, drive it from `analyzer-batch`:

```yaml
# manifest.yaml
data_location: ./data
jobs:
  - tool: create-model
    args: [--config, configs/sample_a.yaml]
  - tool: create-model
    args: [--config, configs/sample_b.yaml]
```

```bash
analyzer-batch manifest.yaml
```

Each job is one independent invocation of `create-model --config …`.

## What the LLM must return

The LLM is constrained to reply with a single JSON object of this shape —
`create-model` converts it into the Python script itself, so free-form LLM
Python is never executed:

```json
{
  "ambient":   {"name": "D2O", "sld": 6.19,
                "sld_min": 5.19, "sld_max": 7.19,
                "roughness_min": 1.0, "roughness_max": 25.0},
  "substrate": {"name": "Si",  "sld": 2.07,
                "roughness_min": 0.0, "roughness_max": 15.0},
  "layers": [
    {"name": "CuOx", "sld": 5.0,  "thickness": 30.0,  "roughness": 10.0,
     "thickness_min": 5.0,   "thickness_max": 200.0,
     "sld_min": 3.0,         "sld_max": 7.0,
     "roughness_min": 5.0,   "roughness_max": 30.0},
    {"name": "Cu",   "sld": 6.4,  "thickness": 500.0, "roughness": 5.0, "...": "..."},
    {"name": "Ti",   "sld": -1.95,"thickness": 35.0,  "roughness": 5.0, "...": "..."}
  ],
  "intensity":        {"value": 1.0, "min": 0.95, "max": 1.05},
  "back_reflection":  false,
  "shared_parameters": [
    "Cu.material.rho", "Cu.interface",
    "Ti.thickness", "Ti.material.rho", "Ti.interface"
  ]
}
```

Key rules:

- `layers` goes **ambient-adjacent → substrate-adjacent** (top-to-bottom).
  Do **not** include the ambient or substrate inside `layers`.
- SLD bounds: at least ±2 × 10⁻⁶ Å⁻² around nominal. Adhesion layers (Ti):
  ±3 or wider.
- Roughness ≥ 5 Å and typically ≤ 30 Å; must be less than half the thinnest
  adjacent layer.
- Minimum layer thickness: 5 Å.
- Never vary the substrate SLD.
- Do **not** add a native SiO₂ on Si unless the user description says so.

On parse or validation failure, `create-model` retries the LLM once with the
error message appended, then aborts.

## See also

- [models skill](../models/SKILL.md) — anatomy of a model file, adjusting
  parameter ranges.
- [reflectometry-basics skill](../reflectometry-basics/SKILL.md) — domain
  rules the LLM is instructed to follow.
- [fitting skill](../fitting/SKILL.md) — how to run the generated script.
---
name: create-model
description: >
  Generate a refl1d analyzer-convention model script. Two modes:
  (A) convert an existing AuRE problem JSON, or
  (B) generate directly via LLM from a sample description and one or more
  REF_L data files. Mode B auto-detects which of the three fitting cases
  applies: single combined file (case 1), multiple partial files from one
  measurement (case 2), or multiple combined files co-refined (case 3 — not
  supported by AuRE, only by this tool).
  USE FOR: creating a new model file; adapting a hand-written model for
  co-refinement of multiple measurements.
  DO NOT USE FOR: running fits (see fitting skill) or adjusting an existing
  model's parameter ranges (edit the script directly).
---

# create-model

## When to use

- You have a **natural-language description** of a sample and one or more
  REF_L data files, and want a model script ready for `run-fit`.
- You already have an **AuRE problem JSON** (from `aure prepare` / `aure batch`)
  and want to convert it to an analyzer-convention script.

## The three cases

`create-model` in Mode B auto-detects the case from the data file names:

| Case | Input files | Probe | Output shape |
|------|-------------|-------|--------------|
| 1 | One `REFL_{set}_combined_data_auto.txt` | `QProbe` (Q, dQ) | `create_fit_experiment` + `FitProblem(experiment)` |
| 2 | N `REFL_{set}_{part}_{run}_partial.txt` files sharing one `set_id` | `make_probe` per segment (θ, dT, λ, dL) | `create_sample()` + `create_probe()` + single `FitProblem(experiment)` with shared sample across probes |
| 3 | N `REFL_{set_k}_combined_data_auto.txt` files with different `set_id`s | `QProbe` each | N experiments, constraint lines tying shared parameters, `FitProblem([experiment, experiment2, ...])` |

Case 3 is **not supported by AuRE** — only by `create-model` Mode B.

## Modes

### Mode A — convert an AuRE problem JSON

```bash
create-model path/to/problem.json --out models/cu_thf.py
```

Accepts either an AuRE `ModelDefinition` JSON (keys `substrate`/`ambient`/
`layers`/`intensity`/`dq_is_fwhm`) or a bumps `problem.json` (schema
`bumps-draft-03`). Only produces case-1 or case-2 scripts, depending on what
AuRE emitted.

### Mode B — generate via LLM

```bash
create-model \
  --describe "2 nm CuOx / 50 nm Cu / 3 nm Ti on Si in D2O" \
  --data data/REFL_226642_combined_data_auto.txt \
  --data data/REFL_226652_combined_data_auto.txt \
  --out models/Cu-D2O-corefine.py \
  --model-name corefine_226642_226652
```

`--data` is repeatable. The case is detected from the file names; you do not
need to specify it. The LLM provider and model come from `.env` (`LLM_PROVIDER`,
`LLM_MODEL`, `LLM_BASE_URL`, …) via `aure.llm`.

### Driving options from a file

Either mode accepts `--config FILE` (YAML or JSON). Two shapes are supported.

#### Flat (single job)

Top-level keys. Command-line flags override config values. Relative paths
are resolved against the **config file's directory**.

| Key | Aliases | Meaning |
|---|---|---|
| `describe` | `description`, `sample_description` | Mode B: sample description text |
| `data` | `data_files` | Mode B: list of REF_L data files |
| `data_file` | — | Mode B: single extra data file, prepended to `data` |
| `source` | — | Mode A: path to problem JSON |
| `out` | — | Output script path |
| `model_name` | `name` | Name used in docstring and default filename |
| `data_dir` | — | Emit `DATA_DIR = "<value>"` at the top of the generated script; file paths below are rewritten as `os.path.join(DATA_DIR, …)` so the script is portable. Relative values resolve against the manifest directory. |

```yaml
# model-creation.yaml  (flat)
describe: |
  2 nm copper oxide on 50 nm copper on 3 nm Ti on silicon.
  The ambient medium is D2O (SLD about 6).
  Neutrons enter from the silicon substrate side.
data:
  - Rawdata/REFL_226642_combined_data_auto.txt
  - Rawdata/REFL_226652_combined_data_auto.txt
out: Models/Cu-D2O-corefine.py
model_name: corefine_226642_226652
```

```bash
create-model --config model-creation.yaml
```

#### Jobs list (batch)

To generate several scripts in one call, use a top-level `jobs:` list. Each
entry is one create-model invocation. This shape mirrors the AuRE
`aure batch` manifest so you can reuse an existing file, but **only the keys
listed above are read** — AuRE-specific settings (`fit_method`, `fit_steps`,
`llm_*`, `command`, …) are ignored.

```yaml
# model-creation.yaml  (jobs)
defaults:
  output_root: ./Models    # default directory for per-job output files

jobs:
  - name: copper_oxide     # → Models/copper_oxide.py
    sample_description: >-
      2 nm copper oxide on 50 nm copper on 3 nm Ti on silicon
      in D2O (SLD ~6). Neutrons enter from the silicon side.
    data_file: Rawdata/REFL_226642_1_226642_partial.txt
    data_files:
      - Rawdata/REFL_226642_2_226643_partial.txt
      - Rawdata/REFL_226642_3_226644_partial.txt

  - name: corefine_226642_226652
    description: 2 nm CuOx / 50 nm Cu / 3 nm Ti on Si in D2O
    data:
      - Rawdata/REFL_226642_combined_data_auto.txt
      - Rawdata/REFL_226652_combined_data_auto.txt
    out: Models/Cu-D2O-corefine.py   # overrides defaults.output_root
```

Rules for the jobs form:

- Each job must be **either** Mode A (`source:`) **or** Mode B
  (`describe` + data files); mixing the two in one entry is an error.
- Output path: explicit `out:` wins; otherwise `<defaults.output_root>/<name>.py`
  is used (resolved relative to the config file).
- Do not pass `SOURCE`/`--describe`/`--data`/`--out` on the command line when
  `--config` has a `jobs:` list.
- `defaults.output_root` is the only field read from `defaults:`. Everything
  else there is ignored.

#### States (multi-state co-refinement)

Case 3 (multiple combined files) ties every parameter either fully or via a
single `shared_parameters` list. Use the `states:` form when you need finer
control, e.g. several measurements of the *same* sample at different times
where most structural parameters should be tied but the θ offset and sample
broadening should float **per state**, or when you want to mix partials and
combined data in one co-refinement.

A **state** groups data files that share one physical sample (so one `Sample`
stack). Within a state, all files use the same material / thickness /
roughness parameters; across states, the `shared_parameters` whitelist (or
`unshared_parameters` blacklist) controls which structural attributes are
tied.

```yaml
# model-creation.yaml  (states)
describe: |
  2 nm CuOx / 50 nm Cu / 3 nm Ti on Si in D2O (SLD ~6).
  Neutrons enter from the silicon side.

states:
  - name: run_226642           # partials: all three segments share one sample
    data:
      - Rawdata/REFL_226642_1_226642_partial.txt
      - Rawdata/REFL_226642_2_226643_partial.txt
      - Rawdata/REFL_226642_3_226644_partial.txt
    theta_offset:              # shared across the three segments of this state
      init: 0.0
      min:  -0.02
      max:   0.02
    sample_broadening:         # same {init, min, max} syntax as theta_offset
      init: 0.0
      min:  0.0
      max:  0.05
    # Either key also accepts `true` for sensible defaults, or `false`/omitted
    # to disable. Defaults: theta_offset = {0, -0.02, 0.02};
    # sample_broadening = {0, 0, 0.05}.

  - name: run_226652           # single combined file → no theta_offset allowed
    data:
      - Rawdata/REFL_226652_combined_data_auto.txt
    back_reflection: true      # neutrons enter through the substrate for
                               # this state only; others keep their default

# Whitelist (exact set) OR blacklist (subtracted from the default).
shared_parameters:
  - Cu.thickness
  - Cu.material.rho
  - Cu.interface
  - Ti.thickness
  - Ti.material.rho
  - Ti.interface
# unshared_parameters: [CuOx.thickness]   # mutually exclusive with the above

out: Models/Cu-D2O-corefine.py
model_name: corefine
```

Rules for the states form:

- **Within a state, every structural parameter is tied across the state's
  data files.** The renderer creates one Sample object per state and
  reuses it for every probe in that state; there is no per-segment layer
  variation. The same holds for the per-state nuisance parameters
  (`theta_offset`, `sample_broadening`) — they are single `Parameter`
  objects shared by every probe of that state.
- A state's data files must all be the same kind: **all partials of one
  set_id**, or **one combined file**. Mixing within a state is rejected.
- `theta_offset` and `sample_broadening` are only allowed on partial-kind
  states (they are meaningful across multiple probe segments). Accepted
  forms: `false`/omitted (fixed), `true` (defaults), or a `{init, min, max}`
  dict.
- `back_reflection: true` tells the renderer that this state's beam enters
  through the substrate (buried-interface geometry, e.g. a silicon block
  illuminated from the bulk side). `back_reflection: false` is standard
  front-reflection geometry (beam enters through the ambient). The flag
  controls **stack orientation only** — the renderer emits the layer
  pipe-expression in the correct order so refl1d's default
  `probe.back_reflectivity = False` always gives correct physics. We never
  set `probe.back_reflectivity` in generated scripts. If the key is
  omitted on a state, the spec-level default (set by the LLM from the
  sample description) is used.
- `shared_parameters` and `unshared_parameters` are mutually exclusive.
  When neither is set, a sensible default (every layer.thickness,
  layer.material.rho, layer.interface, plus substrate.interface) is shared.
- Layer prefixes in `shared_parameters` / `unshared_parameters` are
  forwarded to the LLM as required layer names — the generated stack must
  use exactly those names. A mismatch aborts `create-model` cleanly.
- `states:` may not be combined with top-level `data:` / `source:`. In a
  `jobs:` list, each job picks exactly one shape.

## What the LLM must return

The LLM is constrained to reply with a single JSON object of this shape —
`create-model` converts it into the Python script itself, so free-form LLM
Python is never executed:

```json
{
  "ambient":   {"name": "D2O", "sld": 6.19,
                "sld_min": 5.19, "sld_max": 7.19,
                "roughness_min": 1.0, "roughness_max": 25.0},
  "substrate": {"name": "Si",  "sld": 2.07,
                "roughness_min": 0.0, "roughness_max": 15.0},
  "layers": [
    {"name": "CuOx", "sld": 5.0,  "thickness": 30.0,  "roughness": 10.0,
     "thickness_min": 5.0,   "thickness_max": 200.0,
     "sld_min": 3.0,         "sld_max": 7.0,
     "roughness_min": 5.0,   "roughness_max": 30.0},
    {"name": "Cu",   "sld": 6.4,  "thickness": 500.0, "roughness": 5.0, "...": "..."},
    {"name": "Ti",   "sld": -1.95,"thickness": 35.0,  "roughness": 5.0, "...": "..."}
  ],
  "intensity":        {"value": 1.0, "min": 0.95, "max": 1.05},
  "back_reflection":  false,
  "shared_parameters": [
    "Cu.material.rho", "Cu.interface",
    "Ti.thickness", "Ti.material.rho", "Ti.interface"
  ]
}
```

Key rules:

- `layers` goes **ambient-adjacent → substrate-adjacent** (top-to-bottom).
  Do **not** include the ambient or substrate inside `layers`.
- SLD bounds: at least ±2 × 10⁻⁶ Å⁻² around nominal. Adhesion layers (Ti):
  ±3 or wider.
- Roughness ≥ 5 Å and typically ≤ 30 Å; must be less than half the thinnest
  adjacent layer.
- Minimum layer thickness: 5 Å.
- Never vary the substrate SLD.
- Do **not** add a native SiO₂ on Si unless the user description says so.

On parse or validation failure, `create-model` retries the LLM once with the
error message appended, then aborts.

## Case-3 `shared_parameters`

Case 3 is the reason `create-model` exists: AuRE cannot co-refine multiple
combined files with flexible inter-experiment constraints. Provide the list
of dotted attribute paths to tie across all experiments. Sensible defaults:

- Share **structural** parameters of buried layers (Ti and Cu thickness / SLD
  / interface; adhesion layer roughness).
- Do **not** share `intensity` (each experiment has its own probe) or the
  ambient SLD (solvent can differ between runs).
- If a layer's properties genuinely differ between measurements (e.g. an
  oxide that grows), leave it **off** the shared list.

Each entry must match ``LayerName.{material.rho|thickness|interface}``.
The renderer emits one line per constraint per non-first experiment:

```python
experiment2.sample["Cu"].material.rho = experiment.sample["Cu"].material.rho
```

## Generated script templates (excerpt)

### Case 1

```python
def create_fit_experiment(q, dq, data, errors):
    dq = dq / 2.355
    probe = QProbe(q, dq, data=(data, errors))
    probe.intensity = Parameter(value=1.0, name="intensity")
    probe.intensity.range(0.95, 1.05)
    ...
    sample = D2O(0, 10) | CuOx(30, 10) | Cu(500, 5) | Ti(35, 5) | Si
    experiment = Experiment(probe=probe, sample=sample)
    sample["Cu"].thickness.range(250, 1000)
    ...
    return experiment

data_file = "…/REFL_226642_combined_data_auto.txt"
_refl = np.loadtxt(data_file).T
experiment = create_fit_experiment(_refl[0], _refl[3], _refl[1], _refl[2])
problem = FitProblem(experiment)
```

### Case 2

```python
def create_probe(data_file, theta):
    q, data, errors, dq = np.loadtxt(data_file).T
    wl = 4*np.pi*np.sin(np.pi/180*theta)/q
    dT = dq/q * np.tan(np.pi/180*theta) * 180/np.pi
    probe = make_probe(T=theta, dT=dT, L=wl, dL=0*q,
                       data=(data, errors),
                       radiation="neutron", resolution="uniform")
    ...

def create_sample():
    ...

sample = create_sample()
probe1 = create_probe(data_file1, theta=0.45)
probe2 = create_probe(data_file2, theta=1.2)
probe3 = create_probe(data_file3, theta=3.5)

experiment = Experiment(probe=probe1, sample=sample)
experiment2 = Experiment(probe=probe2, sample=sample)
experiment3 = Experiment(probe=probe3, sample=sample)

problem = FitProblem(experiment)
```

### Case 3

```python
def create_fit_experiment(q, dq, data, errors):
    ...  # builds an INDEPENDENT sample for each call

_refl = np.loadtxt(data_file1).T
experiment  = create_fit_experiment(_refl[0], _refl[3], _refl[1], _refl[2])
_refl = np.loadtxt(data_file2).T
experiment2 = create_fit_experiment(_refl[0], _refl[3], _refl[1], _refl[2])

# Shared structural parameters
experiment2.sample["Cu"].material.rho = experiment.sample["Cu"].material.rho
experiment2.sample["Cu"].interface    = experiment.sample["Cu"].interface
experiment2.sample["Ti"].thickness    = experiment.sample["Ti"].thickness
experiment2.sample["Ti"].material.rho = experiment.sample["Ti"].material.rho
experiment2.sample["Ti"].interface    = experiment.sample["Ti"].interface

problem = FitProblem([experiment, experiment2])
```

## See also

- [models skill](../models/SKILL.md) — anatomy of a model file, adjusting
  parameter ranges.
- [reflectometry-basics skill](../reflectometry-basics/SKILL.md) — domain
  rules the LLM is instructed to follow.
- [fitting skill](../fitting/SKILL.md) — how to run the generated script.
