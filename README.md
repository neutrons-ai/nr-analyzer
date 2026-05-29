# Neutron Reflectometry Analysis Tools

[![Python Tests](https://github.com/neutrons-ai/nr-analyzer/actions/workflows/python-test.yml/badge.svg)](https://github.com/neutrons-ai/nr-analyzer/actions/workflows/python-test.yml)
[![codecov](https://codecov.io/gh/neutrons-ai/nr-analyzer/branch/main/graph/badge.svg)](https://codecov.io/gh/neutrons-ai/nr-analyzer)
[![DOI](https://zenodo.org/badge/1013265177.svg)](https://doi.org/10.5281/zenodo.15870378)

A toolbox of small, well-named CLI tools that an LLM agent (or a human) can
chain together to analyze neutron reflectometry data end-to-end: partial
data quality checks → model generation → refl1d fit → report. Built around
[refl1d](https://github.com/reflectometry/refl1d)/[bumps](https://github.com/bumps/bumps)
for the math, with optional [AuRE](https://github.com/neutrons-ai/aure) for
LLM-driven model creation and fit evaluation.

## Quick Start

1. Install with `pip install -e ".[dev]"` (see [Installation](#installation)).
2. *(Optional)* Set up a one-time user-global LLM config:

   ```bash
   mkdir -p ~/.config/analyzer
   cat > ~/.config/analyzer/.env <<'EOF'
   LLM_PROVIDER=openai
   LLM_MODEL=gpt-4o
   LLM_API_KEY=sk-...
   EOF
   check-llm
   ```

3. `cd` into a sample folder containing reduced data and run the pipeline:

   ```bash
   analyze-sample sample.yaml
   ```

   The YAML uses the same shape as `create-model --config` (a
   `describe:` + `states:` list). A minimal example:

   ```yaml
   describe: 50 nm Cu / 3 nm Ti on Si in D2O
   model_name: cu_d2o_218281
   states:
     - name: state_218281
       data:
         - rawdata/REFL_218281_1_218281_partial.txt
         - rawdata/REFL_218281_2_218282_partial.txt
         - rawdata/REFL_218281_3_218283_partial.txt
   ```

The pipeline runs partial-data checks, halts on bad reduction, then calls
`create-model` → `run-fit` → `assess-result`, writing a Markdown report
under `reports/`.

## What you get

- **`analyze-sample`** — One-shot pipeline for a single sample, with a
  reduction-issue gate that emits a `reduction_batch.yaml` manifest you
  review and dispatch yourself (reduction is never run automatically).
- **`create-model`** — Generate a refl1d-ready Python script from a sample
  description (LLM/AuRE) or convert an AuRE problem JSON. Multi-state
  co-refinement is supported.
- **`run-fit`** — Run a bumps DREAM fit on a refl1d script and produce
  parameter tables, plots, and a Markdown report. Also writes
  `run_info.json` + `final_state.json` so `aure serve <results-dir>/<name>`
  opens the fit in the AuRE web viewer (skip with `--no-aure-export`).
- **`assess-result`** — Re-render the report from a fit-output directory:
  reflectivity overlay (multi-experiment), per-state SLD profiles with 90%
  CL bands. Optionally appends an `aure evaluate` LLM verdict.
- **`assess-partial`** — Overlap-χ² check on partial reflectivity files.
- **`plan-data`** — On arrival of a new partial file, emit a config YAML
  ready for `create-model --config` / `analyze-sample`. Job-control flags
  (`perform_assembly`, `notes`) live in a `metadata` block that those
  tools ignore.
- **`theta-offset`** — Compute or batch-compute incident-angle offsets for
  a Liquids Reflectometer run.
- **`simple-reduction`** — Reduce neutron events to a partial reflectivity
  curve from a Mantid reduction template, applying a theta offset given
  literally or looked up from a `theta-offset` CSV (Mantid-based; Docker
  recommended).
- **`analyzer-batch`** — Dispatch multiple analyzer-tool jobs from a YAML
  manifest.
- **`check-llm`** — Verify that AuRE and the configured LLM endpoint are
  reachable.

Run `analyzer-tools --list-tools` for the full registry, or
`analyzer-tools --help-tool <name>` for any single tool. Per-workflow
documentation lives under [`analyzer_tools/skills/`](analyzer_tools/skills/).

## Installation

```bash
git clone https://github.com/neutrons-ai/nr-analyzer.git
cd nr-analyzer
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

This gives you all analysis, fitting, and pipeline tools. The Mantid-based
reduction command (`simple-reduction`) requires Mantid
and are skipped gracefully when it isn't installed; use Docker for the
full stack — see [docs/docker.md](docs/docker.md).

LLM features (`create-model` Mode B, `aure evaluate` augmentation) require
[AuRE](https://github.com/neutrons-ai/aure) installed in the same
environment and a configured LLM endpoint. They degrade gracefully when
unavailable.

## Configuration

The analyzer needs a project root and five role-based directories
(combined data, partial data, models, results, reports). The simplest
setup is to `cd` into a sample folder — everything resolves under `$PWD`
with lowercase defaults (`rawdata/`, `models/`, `results/`, `reports/`).
A repo-level `.env` *above* the sample folders can rename those
sub-folders without becoming the project root itself.

See [docs/configuration.md](docs/configuration.md) for the full
`.env`-cascade rules and variable reference.

## Batch processing

`analyzer-batch` runs many analyzer-tool invocations from a single YAML
manifest. The manifest is pure orchestration — each `job` dispatches to
one of the CLI tools (`create-model`, `run-fit`, `assess-result`,
`theta-offset`, …) using the same flags you'd type by hand.

### Manifest shape

```yaml
# Optional top-level keys
data_location: ./rawdata     # prepended to bare data filenames in args
output_dir:    ./results     # injected as --output-dir on every job
theta_offset:  -0.005        # injected as --theta-offset (when not already set)

defaults:
  output_root: ./output      # each job's outputs written under <output_root>/<name>

jobs:
  - name: <unique label>     # used for logs and --jobs filter
    tool: <tool name>        # see analyzer-tools --list-tools
    args: [<argv …>]         # exactly as on the command line
```

Run it:

```bash
analyzer-batch manifest.yaml                 # run everything
analyzer-batch manifest.yaml --dry-run       # print commands only
analyzer-batch manifest.yaml --jobs cu_d2o   # run a subset by name
```

A complete reference manifest covering theta-offset, partial checks, fit
+ assess, and `for_each` expansion lives in
[manifest.example.yaml](manifest.example.yaml).

### Example: batch many samples through `analyze-sample`

The recommended way to process many samples is to write one YAML per
sample and dispatch them with `analyzer-batch`. Each per-sample YAML
uses the **same shape as `create-model --config`** (the `describe:` +
`states:` form — see
[analyzer_tools/skills/create-model/SKILL.md](analyzer_tools/skills/create-model/SKILL.md)), and each
`analyze-sample` job runs the full pipeline (partial-data check →
reduction gate → `create-model` → `run-fit` → `assess-result` →
optional AuRE evaluation) for that sample.

```yaml
# samples/cu_thf_218281.yaml
describe: |
  2 nm CuOx / 50 nm Cu / 3 nm Ti on Si in 100 mM LiTFSI/THF.
  Neutrons enter from the silicon side.
model_name: cu_thf_218281
out: models/cu_thf_218281.py
states:
  - name: state_218281
    data:
      - REFL_218281_1_218281_partial.txt
      - REFL_218281_2_218282_partial.txt
      - REFL_218281_3_218283_partial.txt
    theta_offset:      {init: 0.0, min: -0.02, max: 0.02}
    sample_broadening: true
shared_parameters:
  - Cu.thickness
  - Cu.material.rho
  - Ti.thickness
  - Ti.material.rho
```

```yaml
# manifest.yaml
data_location: ./rawdata        # bare REFL_*.txt names resolve here

jobs:
  - name: pipeline_218281
    tool: analyze-sample
    args: [samples/cu_thf_218281.yaml]

  - name: pipeline_218386
    tool: analyze-sample
    args: [samples/cu_thf_218386.yaml]

  - name: pipeline_218430
    tool: analyze-sample
    args: [samples/cu_thf_218430.yaml, --skip-aure-eval]
```

```bash
analyzer-batch manifest.yaml --dry-run                 # verify commands first
analyzer-batch manifest.yaml                           # run all samples
analyzer-batch manifest.yaml --jobs pipeline_218281    # run a single one
```

Each job writes its own `reports/sample_<tag>/` folder. Failures in one
job don't stop the others, and the run summary at the end reports
pass/fail counts. If a sample trips the reduction-issue gate, that
single job halts and emits a `reduction_batch.yaml` for review.

### Going lower-level

`analyze-sample` is one job per sample. When you need finer control —
e.g. regenerating a model without rerunning the fit, or fitting an
existing script multiple times with different settings — call the
underlying tools directly from the manifest:

```yaml
jobs:
  - name: build_cu_thf
    tool: create-model
    args: [--config, samples/cu_thf_218281.yaml]

  - name: fit_cu_thf
    tool: run-fit
    args: [models/cu_thf_218281.py, --name, cu_thf_218281]

  - name: assess_cu_thf
    tool: assess-result
    args: [results/cu_thf_218281]
```

Note that `run-fit` takes a refl1d-ready Python script as its single
positional argument (typically the file `create-model` produced) and
that `assess-result` takes the fit-output directory.

## Documentation

| Topic | Where |
|---|---|
| End-to-end pipeline (`analyze-sample`) | [analyzer_tools/skills/pipeline/SKILL.md](analyzer_tools/skills/pipeline/SKILL.md) |
| `create-model` reference (Mode A & B) | [analyzer_tools/skills/create-model/SKILL.md](analyzer_tools/skills/create-model/SKILL.md) |
| Fitting workflow (`create-model` → `run-fit` → `assess-result`) | [analyzer_tools/skills/fitting/SKILL.md](analyzer_tools/skills/fitting/SKILL.md) |
| Partial-data overlap checks | [analyzer_tools/skills/partial-assessment/SKILL.md](analyzer_tools/skills/partial-assessment/SKILL.md) |
| Theta-offset calculation | [analyzer_tools/skills/theta-offset/SKILL.md](analyzer_tools/skills/theta-offset/SKILL.md) |
| Data arrival planner (`plan-data`) | [analyzer_tools/skills/plan-data/SKILL.md](analyzer_tools/skills/plan-data/SKILL.md) |
| Time-resolved reduction | [analyzer_tools/skills/time-resolved/SKILL.md](analyzer_tools/skills/time-resolved/SKILL.md), [docs/time-resolved-eis.md](docs/time-resolved-eis.md) |
| Data layout & file formats | [analyzer_tools/skills/data-organization/SKILL.md](analyzer_tools/skills/data-organization/SKILL.md) |
| Available refl1d model files | [analyzer_tools/skills/models/SKILL.md](analyzer_tools/skills/models/SKILL.md) |
| Time-resolved / Iceberg packaging | [analyzer_tools/skills/data-packaging/SKILL.md](analyzer_tools/skills/data-packaging/SKILL.md) |
| Reflectometry primer | [analyzer_tools/skills/reflectometry-basics/SKILL.md](analyzer_tools/skills/reflectometry-basics/SKILL.md) |
| Configuration / `.env` cascade | [docs/configuration.md](docs/configuration.md) |
| Batch manifests (`analyzer-batch`) | [Batch processing](#batch-processing), [manifest.example.yaml](manifest.example.yaml) |
| Docker (full stack with Mantid) | [docs/docker.md](docs/docker.md) |
| Single-file skill summary (for downstream repos) | [analyzer_tools/skills/distributable/SKILL.md](analyzer_tools/skills/distributable/SKILL.md) |

## Citation

If this project helps your work, please cite via the
[Zenodo DOI](https://doi.org/10.5281/zenodo.15870378) (badge above) or the
metadata in [CITATION.cff](CITATION.cff).
