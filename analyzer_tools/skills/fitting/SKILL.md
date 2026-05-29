---
name: fitting
description: >
  Reflectivity fitting workflow — generate a model script, run a fit, and
  evaluate the result.
  USE FOR: fitting reflectivity data, evaluating fit quality, iterating on a model.
  DO NOT USE FOR: partial data quality checks (see partial-assessment skill),
  data reduction (see time-resolved skill), or end-to-end pipelines for a
  single sample (see pipeline skill — `analyze-sample`).
---

# Reflectivity Fitting Workflow

## Overview

The standard fitting workflow is three CLI calls:

1. **`create-model`** — Generate an analyzer-convention refl1d Python script
   either from a sample description (Mode B, LLM/AuRE) or by converting an
   existing AuRE problem JSON (Mode A).
2. **`run-fit`** — Fit the script with bumps DREAM (or another bumps fitter).
   Writes `problem.par`, `problem.json`, `*-refl.dat`, etc., and — unless
   `--no-assess` is passed — automatically runs `assess-result` to produce
   the Markdown report.
3. **`assess-result`** — Render plots and the report from a fit-output
   directory. Optionally augments the report with an `aure evaluate` LLM
   verdict.

For an end-to-end single-sample pipeline (partial checks → reduction-issue
gate → fit → evaluate), use **`analyze-sample`** instead. See the
[pipeline skill](../pipeline/SKILL.md).

## Step 1: Create a Model

`create-model` accepts either an AuRE JSON as a positional argument
(Mode A) **or** a YAML/JSON config via `--config` (Mode B). It does not
take ad-hoc `--describe` / `--data` flags — put those in the config file.

### Mode B — from a config file (most common)

```bash
create-model --config model-creation.yaml --out models/cu_d2o.py
```

Minimal config (Mode B, single combined file):

```yaml
describe: 50 nm Cu / 3 nm Ti on Si in D2O
model_name: cu_d2o
states:
  - name: state_d2o
    data:
      - rawdata/REFL_226642_combined_data_auto.txt
```

For multi-state co-refinement (e.g. same stack measured against two
solvents) and for partial-data fits, see the
[create-model skill](../create-model/SKILL.md).

### Mode A — from an AuRE problem JSON

```bash
create-model path/to/problem.json --out models/cu_thf.py
```

## Step 2: Run a Fit

```bash
run-fit <SCRIPT.py> [options]
```

Common options:

| Option | Default | Meaning |
|---|---|---|
| `--name` | script stem | Output subfolder name and report tag |
| `--results-dir` | `$ANALYZER_RESULTS_DIR` | Parent directory for fit output |
| `--reports-dir` | `$ANALYZER_REPORTS_DIR` | Where the report is written |
| `--fit` | `dream` | Bumps fitter (`dream`, `amoeba`, `lm`, `de`, `newton`, …) |
| `--samples` | `10000` | DREAM samples |
| `--burn` | `5000` | DREAM burn-in steps |
| `--no-assess` | off | Skip the post-fit `assess-result` invocation |
| `--no-aure-export` | off | Skip writing the `aure serve`-compatible JSON |
| `--sample-description TEXT` | empty | Free-text description recorded in the AuRE export |
| `--hypothesis TEXT` | none | Optional hypothesis recorded in the AuRE export |

```bash
run-fit models/cu_d2o.py --name cu_d2o_226642
# → results/cu_d2o_226642/  + reports/report_cu_d2o_226642.md
# Then: aure serve results/cu_d2o_226642
```

### Output files

| File | Contents |
|---|---|
| `problem.par` | Best-fit parameter values |
| `problem-err.json` | Parameter uncertainties |
| `problem.json` | FitProblem definition (consumed by `aure evaluate`) |
| `problem.out` | Overall fit statistics |
| `*-refl.dat` | Reflectivity data with calculated values per experiment |
| `run_info.json` | Run metadata for `aure serve` (skip with `--no-aure-export`) |
| `final_state.json` | Consolidated state (Q/R/dR, fit_results, SLD) for `aure serve` |

### Data column convention

Data files are `Q, R, dR, dQ`. Generated scripts pass them to
`create_fit_experiment(q, dq, data, errors)` correctly — no manual swap.

## Step 3: Assess the Result

`run-fit` calls this for you; run it directly when you want to re-render
a report or when fitting outside the analyzer.

```bash
assess-result <RESULTS_DIR> [options]
```

| Option | Meaning |
|---|---|
| `--output-dir` | Reports directory (default `$ANALYZER_REPORTS_DIR`) |
| `--context` | Sample description passed to `aure evaluate -c` |
| `--sample-description PATH` | Markdown file used as the AuRE context |
| `--hypothesis` | Optional hypothesis passed to `aure evaluate -h` |
| `--skip-aure-eval` | Skip the LLM augmentation entirely |
| `--json` | Print a machine-readable summary to stdout |

The report tag is the basename of the results directory:
`results/cu_d2o_226642` → `report_cu_d2o_226642.md`.

### What it produces

| File | Contents |
|---|---|
| `report_<TAG>.md` | Markdown report: χ², parameter table, plots, optional AuRE verdict |
| `fit_result_<TAG>_reflectivity.svg` | R vs Q (multi-experiment overlay when relevant) |
| `fit_result_<TAG>_profile.svg` | Per-state SLD profile with 90% CL band |
| `sld_uncertainty_<TAG>.txt` | SLD profile numerical data |

### Chi-squared quality thresholds

| χ² range | Assessment | Recommended action |
|---|---|---|
| < 2.0 | Excellent | Review parameter uncertainties |
| 2.0 – 3.0 | Good | Check for systematic residual patterns |
| 3.0 – 5.0 | Acceptable | Consider adjusting model |
| > 5.0 | Poor | Model likely needs revision |

### Optional AuRE evaluation

When AuRE is installed and an LLM endpoint is reachable, `assess-result`
appends an `## AuRE evaluation` section with:

| Field | Meaning |
|---|---|
| `verdict` / `acceptable` | Overall assessment |
| `issues` | Concrete problems (boundary hits, residual structure, …) |
| `suggestions` | Actionable next steps |
| `physical_concerns` | Parameters that look physically implausible |

To run only the LLM step manually:

```bash
aure evaluate results/cu_d2o_226642 \
  --context "50 nm Cu / 3 nm Ti on Si in D2O" --json
```

LLM credentials live in `~/.config/analyzer/.env`; verify with `check-llm`.

## Step 4: Iterate

If the fit is poor or the AuRE verdict is negative:

- Edit the model description or `states:` in the config and re-run
  `create-model` → `run-fit`.
- Or edit `models/<name>.py` directly (layer structure, parameter
  `.range(...)` bounds) and re-run `run-fit`.

Common AuRE suggestions and responses:

| Suggestion | Response |
|---|---|
| "Parameter X at upper bound" | Widen the parameter's `.range(...)` in the script |
| "Consider adding interface roughness" | Add an `interface.range(...)` call |
| "Residual fringes suggest unmodeled layer" | Add a layer to the stack |
| "High-Q residual structure" | Check `dQ` resolution and background level |

## Notes

- The default fitter is bumps DREAM; MCMC samples give parameter
  uncertainties and 90% CL SLD bands.
- Record analysis findings in `docs/analysis_notes.md` per repo convention.
- For end-to-end sample analysis with the reduction-issue gate, use
  **`analyze-sample`** (see the [pipeline skill](../pipeline/SKILL.md)).
