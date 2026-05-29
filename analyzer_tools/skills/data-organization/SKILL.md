---
name: data-organization
description: >
  Neutron reflectometry data layout, file naming conventions, column formats,
  and how to discover available data. USE FOR: understanding where data lives,
  interpreting file names, reading data columns, configuring paths. 
  DO NOT USE FOR: fitting or reducing data (see fitting or time-resolved skills).
---

# Data Organization

## Directory Layout

All paths are relative to the repository root and configurable via `.env` (or environment variables).

| Directory | Default Path | Contents |
|-----------|-------------|----------|
| Combined data | `data/combined/` | Final reduced reflectivity curves (one file per measurement set) |
| Partial data | `data/partial/` | Individual partial curves (usually 3 per set, before combining) |
| Models | `models/` | Python model files for refl1d fitting |
| Results | `results/` | Fit outputs organized by run |
| Reports | `reports/` | Markdown analysis reports with plots |

## File Naming Conventions

### Combined data
```
REFL_{set_id}_combined_data_auto.txt
```
- `set_id` is a numeric identifier (e.g., `218281`, `218386`)
- Example: `REFL_218281_combined_data_auto.txt`

### Partial data
```
REFL_{set_id}_{part_id}_{run_id}_partial.txt
```
- `set_id` — identifier for the measurement set (usually the first `run_id` of the set)
- `part_id` — runs from 1 to 3 (three angular settings that together cover the full Q range)
- `run_id` — the individual run number for that part
- All parts with the same `set_id` belong together
- Example: `REFL_218281_1_218281_partial.txt`, `REFL_218281_2_218282_partial.txt`, `REFL_218281_3_218283_partial.txt`

## Column Format

All reflectometry data files (both combined and partial) have **4 columns**:

| Column | Symbol | Description | Units |
|--------|--------|-------------|-------|
| 1 | Q | Momentum transfer | 1/Å |
| 2 | R | Reflectivity | dimensionless |
| 3 | dR | Uncertainty on R | dimensionless |
| 4 | dQ | Q resolution (FWHM) | 1/Å |

Partial data files have a 1-line header (skipped with `skiprows=1`).
Combined data files have no header.

A reflectivity curve is plotted as **R vs Q**, with dR as error bars, typically on a log-log scale.

## Configuration

The analyzer resolves five role-based directories from a project root:

| Role | Default sub-folder | Variables |
|---|---|---|
| Combined data | `rawdata` | `ANALYZER_COMBINED_DATA_DIR` (absolute) or `ANALYZER_DATA_SUBDIR` |
| Partial data | *(falls back to combined)* | `ANALYZER_PARTIAL_DATA_DIR` (absolute) or `ANALYZER_PARTIAL_SUBDIR` |
| Models | `models` | `ANALYZER_MODELS_DIR` (absolute) or `ANALYZER_MODELS_SUBDIR` |
| Results | `results` | `ANALYZER_RESULTS_DIR` (absolute) or `ANALYZER_RESULTS_SUBDIR` |
| Reports | `reports` | `ANALYZER_REPORTS_DIR` (absolute) or `ANALYZER_REPORTS_SUBDIR` |

Project root is `ANALYZER_PROJECT_DIR` if set, else the **current working
directory**. So in the typical case you just `cd Sample7/` and run analyzer
commands — no `.env` needed.

A repo-level `.env` (sitting **above** the sample folders) can rename the
sub-folders without forcing the repo to be the project root. Example:

```
experiments-2025/
├── .env                 ← repo-wide sub-folder names
│     ANALYZER_DATA_SUBDIR=Rawdata
│     ANALYZER_MODELS_SUBDIR=Models
│     ANALYZER_RESULTS_SUBDIR=analyzer_results
├── Sample5/             ← cd here; project_dir = $PWD
└── Sample7/
```

LLM secrets (`LLM_API_KEY`, etc.) belong in the user-global
`~/.config/analyzer/.env` and are loaded via the `.env` cascade.

The combined-data filename template is independent of the path:

```dotenv
ANALYZER_COMBINED_DATA_TEMPLATE=REFL_{set_id}_combined_data_auto.txt
```

`{set_id}` is replaced at runtime.

## Discovering Available Data

```bash
# List all available data files (combined + partial)
analyzer-tools --show-data
```

This scans the configured data directories and lists all recognized data files with their set IDs.

## Key Concepts

- A **set** is a complete reflectivity measurement, identified by `set_id`
- A set is built from **parts** (typically 3) measured at different angular settings to cover different Q ranges
- Parts **overlap** in Q — the overlap quality can be checked with
  the `assess-partial` tool (see the partial-assessment skill)
- After verifying overlap quality, parts are **combined** into a single reflectivity curve
