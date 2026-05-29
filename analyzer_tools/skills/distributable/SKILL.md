---
name: neutron-reflectometry-analyzer
description: >
  Neutron reflectometry analysis using the nr-analyzer package (refl1d/bumps).
  USE FOR: fitting reflectivity data, assessing fit quality, evaluating partial data
  overlap, computing theta offsets, and creating or adjusting models.
  DO NOT USE FOR: general Python questions unrelated to reflectometry.
argument-hint: 'Describe what you want to analyze (e.g., "fit set 218281 with cu_thf model")'
---

# Neutron Reflectometry Analyzer

Provides CLI tools for neutron reflectometry data analysis built on
[refl1d](https://refl1d.readthedocs.io) and
[bumps](https://bumps.readthedocs.io). Install with:

```bash
pip install -e /path/to/analyzer
```

Verify installation:

```bash
analyze-sample --help
```

---

## Data Conventions

### Directory layout (configured via `.env` / `ANALYZER_*` env vars)

The five role-based directories are resolved as
`$ANALYZER_PROJECT_DIR/<subdir>` (defaulting to `$PWD/<subdir>`):

| Role | Default sub-folder | Override sub-folder name | Override absolute path |
|------|-------------------|--------------------------|------------------------|
| Combined data | `rawdata` | `ANALYZER_DATA_SUBDIR` | `ANALYZER_COMBINED_DATA_DIR` |
| Partial data  | *(falls back to combined)* | `ANALYZER_PARTIAL_SUBDIR` | `ANALYZER_PARTIAL_DATA_DIR` |
| Models        | `models`  | `ANALYZER_MODELS_SUBDIR` | `ANALYZER_MODELS_DIR` |
| Results       | `results` | `ANALYZER_RESULTS_SUBDIR` | `ANALYZER_RESULTS_DIR` |
| Reports       | `reports` | `ANALYZER_REPORTS_SUBDIR` | `ANALYZER_REPORTS_DIR` |

Typical setup: `cd` into a sample folder; the analyzer uses `$PWD` as the
project root. A repo-level `.env` *above* the sample folders can override
the sub-folder names (e.g. `ANALYZER_DATA_SUBDIR=Rawdata`) without becoming
the project root itself. LLM secrets live in `~/.config/analyzer/.env`.

### File naming

- **Combined**: `REFL_{set_id}_combined_data_auto.txt`
- **Partial**: `REFL_{set_id}_{part_id}_{run_id}_partial.txt` (part_id = 1–3)

### Column format (all data files)

| Column | Symbol | Description |
|--------|--------|-------------|
| 1 | Q | Momentum transfer (1/Å) |
| 2 | R | Reflectivity |
| 3 | dR | Uncertainty on R |
| 4 | dQ | Q resolution (FWHM, 1/Å) |

---

## CLI Tools Reference

### Fitting workflow

#### 1. `run-fit` — Run a refl1d-ready model script

```bash
run-fit SCRIPT [--results-dir DIR] [--reports-dir DIR] [--name NAME] \
                [--fit dream] [--samples 10000] [--burn 5000] [--no-assess] \
                [--no-aure-export] [--sample-description TEXT] [--hypothesis TEXT]
```

`SCRIPT` is a complete refl1d Python file (typically produced by
`create-model`) that defines a module-level `problem = FitProblem(...)` and
loads its own data. Output lands in `<results-dir>/<name>/` (default
`<results-dir>/<script-stem>/`). Unless `--no-assess` is given, `run-fit`
automatically calls `assess-result` afterwards. Defaults for `--results-dir`
and `--reports-dir` come from `$ANALYZER_RESULTS_DIR` and
`$ANALYZER_REPORTS_DIR`.

After the fit, `run-fit` also writes `run_info.json` and `final_state.json`
into the same `<results-dir>/<name>/` directory so that
`aure serve <results-dir>/<name>/` opens the result in the AuRE web viewer.
Disable with `--no-aure-export`. `analyze-sample` forwards the YAML
`describe` and `hypothesis` fields to populate these.

#### 2. `assess-result` — Evaluate fit quality

```bash
assess-result <RESULTS_DIR> [--output-dir DIR] [--skip-aure-eval] \
              [--context TEXT | --sample-description FILE]
```

The basename of `RESULTS_DIR` is used as the report tag (e.g.
`results/cu_thf` → `report_cu_thf.md`). All experiments in the fit are
overlaid on the reflectivity plot; every distinct SLD profile is shown with
90% CL uncertainty bands. Output goes to `--output-dir` (default
`$ANALYZER_REPORTS_DIR`).

**Chi-squared quality thresholds:**

| χ² | Quality | Action |
|----|---------|--------|
| < 2 | Excellent | Review uncertainties |
| 2–3 | Good | Check residual patterns |
| 3–5 | Acceptable | Consider adjusting model |
| > 5 | Poor | Revise model |

#### 3. `create-model` — Generate a refl1d model script

Two modes. **Mode A** converts an existing AuRE problem JSON. **Mode B**
generates a script via LLM from a YAML/JSON config that lists one or more
measurement *states*; each state groups files that share one physical
sample. Per state the file kind is auto-detected:

- one combined file → single `QProbe` segment
- N partial files (`REFL_{set}_{part}_{run}_partial.txt`) sharing one
  `set_id` → N `make_probe` segments per state, with one `Sample` reused
  across them.

Structural parameters are tied across states via `shared_parameters`
(whitelist) or `unshared_parameters` (blacklist). Each state may carry
`extra_description` text (e.g. "in H₂O instead of D₂O") that is appended
to the global `describe` when the LLM is told about it.

```bash
# Mode A — from a problem JSON
create-model path/to/problem.json --out models/cu_thf.py

# Mode B — states-driven config (single or multi-state co-refinement)
create-model --config model-creation.yaml
```

Minimal Mode B config:

```yaml
describe: 50 nm Cu / 3 nm Ti on Si in D2O
states:
  - name: run_218281
    data: [data/combined/REFL_218281_combined_data_auto.txt]
out: models/cu_thf.py
model_name: cu_thf
```

Multi-state example (mix partials and combined files; share Cu and Ti
across states; record per-state conditions):

```yaml
describe: 2 nm CuOx / 50 nm Cu / 3 nm Ti on Si
states:
  - name: D2O
    extra_description: ambient is D₂O (SLD ≈ 6.4)
    data:
      - data/partial/REFL_226642_1_226642_partial.txt
      - data/partial/REFL_226642_2_226643_partial.txt
      - data/partial/REFL_226642_3_226644_partial.txt
    theta_offset: {init: 0.0, min: -0.02, max: 0.02}
  - name: H2O
    extra_description: ambient is H₂O (SLD ≈ -0.56)
    data: [data/combined/REFL_226660_combined_data_auto.txt]
shared_parameters:
  - Cu.thickness
  - Cu.material.rho
  - Ti.thickness
out: models/Cu-corefine.py
```

Add `data_dir: <path>` at the top level of the config to emit a
`DATA_DIR` variable in the generated script — file paths become
`os.path.join(DATA_DIR, ...)` so the script is portable. See
`skills/create-model/SKILL.md` for the full schema.

To create many models in one go, drive `create-model` from
`analyzer-batch` (one job per `--config FILE`).

### Model adjustment

To widen a parameter range or change a layer, edit `models/<name>.py`
directly and re-run the fit. (The old `create-temporary-model` CLI has
been removed.)

### Data arrival planning

#### `plan-data` — Generate a config YAML when a new partial file arrives

```bash
plan-data DATA_FILE CONTEXT_FILE --output-dir DIR [--sequence-total N]
```

Called once per arriving partial file. Reads `sequence_id` and
`sequence_number` from the file's `Meta:` JSON header (falling back to
filename parsing), scans the same directory for sibling parts, and writes
`job_<sequence_id>.yaml` to `OUTPUT_DIR`. The output conforms to the
`create-model --config` / `analyze-sample` schema, with job-control
flags inside a `metadata` block that those tools ignore.

| Field | Set when |
|---|---|
| `metadata.perform_assembly: true` | current file is the last part (`sequence_number == sequence_total`) **and** all other parts are present |
| Top-level `describe` / `states` / `model_name` | `perform_assembly` is `true` **and** an LLM is available **and** the context file is sufficient |
| `metadata.notes` | always — summary of sequence status and LLM verdict |

**Arguments**

| Argument / Option | Description |
|---|---|
| `DATA_FILE` | One `REFL_{seq_id}_{seq_num}_{run_id}_partial.txt` file |
| `CONTEXT_FILE` | Scientist's Markdown context note (semi-structured) |
| `--output-dir DIR` | Directory for the output YAML (**required**) |
| `--sequence-total N` | Expected number of parts in a complete sequence (default `3`) |
| `--skill NAME` | Skill to load (repeatable) |

**Context file** (`context-sample5.md`) is a free-form Markdown file describing
the sample stack, ambient medium, and any fitting approach notes. The richer
the description, the better the drafted create-model fields.

**Output YAML shape** (`job_<sequence_id>.yaml`):

```yaml
# create-model schema fields at the top level — present only when
# metadata.perform_assembly=true and context is sufficient:
describe: |
  2 nm CuOx / 50 nm Cu / 3 nm Ti on Si in D2O (SLD ~6).
  Neutrons enter from the silicon side.
states:
  - name: run_226642
    data:
      - REFL_226642_1_226642_partial.txt
      - REFL_226642_2_226643_partial.txt
      - REFL_226642_3_226644_partial.txt
    theta_offset: {init: 0.0, min: -0.02, max: 0.02}
    sample_broadening: true
model_name: Cu-D2O-226642

metadata:
  perform_assembly: true          # or false
  notes: |
    Sequence 226642 is complete (3 parts present).  <LLM summary…>
```

The YAML can be passed directly to `create-model --config` or
`analyze-sample` (both ignore the `metadata` block). A scheduler
that consumes the file should branch on `metadata.perform_assembly`
and on the presence of `states` before invoking either CLI.

### Partial data

#### `assess-partial` — Check overlap quality before combining

```bash
assess-partial <SET_ID>
```

Calculates overlap χ² between adjacent parts. Thresholds: < 1.5 good,
1.5–3.0 acceptable, > 3.0 investigate.

#### `assemble-partials` — Combine partial segments into a combined R(Q) file

```bash
assemble-partials <SET_ID>            # -> <combined-dir>/REFL_<SET_ID>_combined_data_auto.txt
assemble-partials <SET_ID> --scale    # rescale each segment to its predecessor's overlap first
```

A Mantid-free way to produce the combined file (segments from one reduction are
usually already consistently scaled, so the default just concatenates and sorts
by Q). Reports the adjacent-overlap χ². Supports `--json` and `--result-out`.

### Theta offsets

#### `theta-offset` — Compute angular offsets from NeXus event files

```bash
theta-offset <NEXUS_FILE> --db <DIRECT_BEAM_FILE>
```

Also computes the gravity-induced angular offset at the mean neutron wavelength.

### Batch processing

#### `analyzer-batch` — Run multiple operations from a manifest

```bash
analyzer-batch <MANIFEST_FILE>
```

When many jobs use the same tool and options, use the `files:` shorthand
(or the general `for_each:` mapping) to expand one entry into many:

```yaml
data_location: ~/data/mar26
output_dir: ~/data/reduced/mar26/sample5
jobs:
  - tool: simple-reduction
    args: [--template, template_down.xml]
    files:                   # one job per file, --event-file <file> appended
      - REF_L_226642.nxs.h5
      - REF_L_226643.nxs.h5
```

### LLM health check

#### `check-llm` — Verify the AuRE/LLM chain is ready

```bash
check-llm              # full check with a live test prompt
check-llm --no-test    # static checks only
check-llm --json       # machine-readable
```

Run at the start of a session. Exits non-zero when the `aure` CLI is
missing, `aure.llm` is not importable, or the LLM endpoint is unreachable.

---

## End-to-end Pipeline (recommended)

For a single sample, `analyze-sample` drives everything — partial-overlap
checks → reduction-issue gate → `create-model` → `run-fit` (which auto-runs
`assess-result`) → optional AuRE evaluation — and writes a consolidated
report:

```bash
analyze-sample sample_218281.yaml          # YAML in create-model --config shape
analyze-sample sample_218281.yaml --dry-run
analyze-sample sample_218281.yaml --skip-aure-eval
```

The argument is a YAML file in the **same shape as `create-model --config`**
(top-level `describe`, `model_name`, `states:` list; per-state `name`, `data`,
optional `theta_offset` / `sample_broadening` / `back_reflection`). Two
pipeline-only extras are accepted at the top level:

- `hypothesis:` — passed to `aure evaluate -h`.
- `theta_offset:` — list of pre-computed `{run, offset}` entries used by the
  reduction-issue gate (the `theta-offset` tool itself is **not** invoked).

The pipeline tag is `model_name` (or `name`), defaulting to the YAML stem.

If the reduction-issue gate trips, the pipeline emits
`reports/sample_<id>/reduction_issues.md` and a pre-filled
`reduction_batch.yaml` for the user to review and run with `analyzer-batch`.
Reduction is **never** auto-executed.

## Standard Fitting Workflow (manual)

```
create-model --config <config.yaml>     # or: create-model <problem.json>
    │
    ▼
run-fit <script.py>                    # auto-calls assess-result
    │
    ▼
┌─ acceptable? ─────────────────────────┐
│  Yes → record in analysis notes       │
│  No  → edit the YAML config or the    │
│        generated script and re-fit    │
└───────────────────────────────────────┘
```

### Complete example

```bash
# 1. Generate a model (Mode B — LLM, states-driven config)
cat > cu_thf.yaml <<'YAML'
describe: Cu/Ti on Si in dTHF
states:
  - name: run_218281
    data: [data/combined/REFL_218281_combined_data_auto.txt]
out: models/cu_thf.py
model_name: cu_thf
YAML
create-model --config cu_thf.yaml

# 2. Fit (auto-runs assess-result afterwards)
run-fit models/cu_thf.py
```

---

## Model Files

Model files live in `models/` and define a `create_fit_experiment(q, dq, data, errors)` function
that returns a `refl1d.experiment.Experiment`. Layers are stacked top-to-bottom:

```python
sample = THF(0, 11.4) | material(58, 13) | Cu(505, 4.6) | Ti(39.5, 9.1) | Si
#        ambient         layers...                                          substrate
```

Parameters are constrained with `.range(min, max)`.

---

## LLM-Powered Evaluation (optional)

If [AuRE](https://github.com/neutrons-ai/aure) is installed (`pip install -e /path/to/aure`),
run `aure evaluate` after `assess-result` for intelligent assessment:

```bash
aure evaluate results/<SET_ID>_<MODEL> \
  --context "<sample description>" --json
```

Returns structured verdict with `acceptable`, `issues`, `suggestions`, and
`physical_concerns`. See AuRE documentation for LLM configuration.

---

## Notes

- All analysis results should be recorded in `docs/analysis_notes.md`
- The fitting algorithm is bumps DREAM (Differential Evolution Adaptive Metropolis)
- SLD profile uncertainty bands represent 90% confidence intervals
- Data column order is Q, R, dR, dQ but model function signature is `(q, dq, data, errors)`
