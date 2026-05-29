<!-- Copilot agent instructions. The authoritative agent guide is [CLAUDE.md](../CLAUDE.md); this file mirrors it for Copilot-driven workflows. Keep the two in sync when scope changes. -->

# Copilot instructions for the analyzer repo

This repository is a **toolbox of small, well-named CLI commands** for neutron
reflectometry analysis. Per-workflow knowledge lives in `SKILL.md` files under
[skills/](../skills/). The expected interaction model is:

> An agent (or human) chains the existing CLIs together. The agent does **not**
> invent fresh analysis scripts; it picks the right tool, configures it,
> runs it, and reports.

[README.md](../README.md) is the user-facing entry point and the source of truth
for what the project does. Read it before suggesting any new workflow.

## When the user asks you to *analyze data*

Pick the highest-level CLI that already covers the task — don't write a one-off
Python script unless the user explicitly asks.

- **One sample, end to end** → `analyze-sample sample.yaml`. The YAML uses the
  `create-model --config` shape (see [skills/create-model/SKILL.md](../skills/create-model/SKILL.md)).
  The pipeline runs partial-data checks → reduction-issue gate → `create-model`
  → `run-fit` → `assess-result`.
- **Many samples** → write one YAML per sample, dispatch with
  `analyzer-batch manifest.yaml`. Pattern in [README.md](../README.md).
- **A new partial file just arrived** → `plan-data DATA_FILE CONTEXT_FILE`
  produces a config YAML you can feed straight into `analyze-sample` or
  `create-model --config`. Job-control flags (`perform_assembly`, `notes`)
  live in a `metadata` block that those tools ignore.
- **Lower-level control** (regenerate a model without re-fitting; refit an
  existing script with different settings) → call `create-model` / `run-fit`
  / `assess-result` directly, optionally from an `analyzer-batch` manifest.
- **Reduction-related work** → `simple-reduction`, `eis-reduce-events`,
  `eis-intervals`, `theta-offset`, `iceberg-packager`. Mantid-based ones need
  the full Docker image (`ghcr.io/mdoucet/analyzer:latest`); the slim image
  (`…:latest-slim`) carries only the analysis CLIs. See [docs/docker.md](../docs/docker.md).

For any tool, `analyzer-tools --help-tool <name>` is the canonical signature.
The full registry is `analyzer-tools --list-tools` (source:
[analyzer_tools/registry.py](../analyzer_tools/registry.py)). Workflow-level
guidance is in the skill files — pull the relevant one into context with
`@skills/<name>/SKILL.md` rather than guessing.

### Reflectometry data conventions
- Combined reflectivity files: 4 columns `Q, R, dR, dQ`, plotted as `R vs Q`
  with `dR` as error bars.
- Partial files: named `REFL_<set_ID>_<part_ID>_<run_ID>_partial.txt`. A
  complete curve is usually 3 parts (`part_ID` 1–3) sharing a `set_ID` (the
  first `run_ID` of the set). Live under `data/partial/` by convention.
- "Combined data" with no qualifier means the final assembled file, not
  partials.
- Domain primer if needed: [skills/reflectometry-basics/SKILL.md](../skills/reflectometry-basics/SKILL.md).

## When the user asks you to *change the codebase*

This is the more common case in this repo. Conventions:

- **Tool layout**: every CLI is registered three places that must stay in sync:
  1. The implementation module under `analyzer_tools/` (or
     `analyzer_tools/analysis/`, `analyzer_tools/reduction/`).
  2. A wrapper in [analyzer_tools/cli.py](../analyzer_tools/cli.py)
     (e.g. `plan_data_cli`).
  3. A `[project.scripts]` entry in [pyproject.toml](../pyproject.toml) and a
     `ToolInfo` row in [analyzer_tools/registry.py](../analyzer_tools/registry.py).
- **CLIs use Click.** New options follow the existing pattern: long-form
  `--option`, sensible defaults shown, mutually-exclusive options validated
  explicitly with `click.UsageError`.
- **Skills are documentation, not packaged data.** They live at the top level
  in [skills/](../skills/). When you change a CLI signature you must update the
  matching skill **and** the single-file summary in
  [skills/distributable/SKILL.md](../skills/distributable/SKILL.md).
- **Tests live in [tests/](../tests/)** and run with `pytest`. The default
  invocation has a coverage floor that's noisy mid-edit; for fast iteration:
  ```bash
  .venv/bin/python -m pytest -x --no-cov -q
  ```
- **No new abstractions for hypothetical needs.** This repo follows YAGNI hard
  — three similar lines beats a premature helper. Match the surrounding style.

### When you change a CLI signature

Skills are the agent-facing reference; stale skills produce silent wrong
behavior in `plan-data` and downstream callers. After any CLI change:

1. Update the matching skill in [skills/](../skills/).
2. Update the single-file summary
   [skills/distributable/SKILL.md](../skills/distributable/SKILL.md) — external
   users and downstream repos rely on it.
3. If a tool was added or removed entirely, also update
   [analyzer_tools/registry.py](../analyzer_tools/registry.py) and
   `[project.scripts]` in [pyproject.toml](../pyproject.toml).
4. If quality thresholds, column formats, or file-naming conventions changed,
   also touch [skills/data-organization/SKILL.md](../skills/data-organization/SKILL.md)
   and [skills/fitting/SKILL.md](../skills/fitting/SKILL.md).

## Skills index

| Skill | Topic |
|---|---|
| [pipeline](../skills/pipeline/) | End-to-end `analyze-sample` workflow |
| [create-model](../skills/create-model/) | `create-model` modes A (JSON) & B (LLM/AuRE) |
| [fitting](../skills/fitting/) | `create-model` → `run-fit` → `assess-result` |
| [partial-assessment](../skills/partial-assessment/) | Overlap-χ² check on partial files |
| [theta-offset](../skills/theta-offset/) | Single & batch theta-offset calculation |
| [time-resolved](../skills/time-resolved/) | EIS interval extraction & event reduction |
| [data-packaging](../skills/data-packaging/) | Iceberg/Parquet packaging |
| [plan-data](../skills/plan-data/) | New-data-file planner |
| [data-organization](../skills/data-organization/) | Layout, naming, column formats |
| [models](../skills/models/) | Available refl1d model files |
| [reflectometry-basics](../skills/reflectometry-basics/) | Domain primer (Q, R, SLD, χ²) |
| [distributable](../skills/distributable/) | Single-file summary for external repos |

## Practical tips for this repo

- **Cross-cutting searches** ("where is X used?", "how does pipeline call
  run-fit?") → use the Explore subagent rather than chaining many separate
  greps; keeps the main context clean.
- **Multi-step CLI runs** (especially `analyzer-batch` over many samples, or
  a debugging chain through `plan-data` → `create-model` → `run-fit`) → track
  progress with the todo tool. Some fits take minutes.
- **Configuration**: cd into a sample folder and the analyzer resolves five
  role-based dirs under `$PWD` (`rawdata/`, `models/`, `results/`,
  `reports/`). Full cascade rules in [docs/configuration.md](../docs/configuration.md).
  LLM features need `~/.config/analyzer/.env`; verify with `check-llm`.
- **Don't run reductions automatically.** The pipeline gates on a
  reduction-issue check and emits a `reduction_batch.yaml` manifest for the
  user to review and dispatch. Surface it; don't dispatch it for them.

---

Note: [CLAUDE.md](../CLAUDE.md) is the authoritative agent guide. This file
mirrors it for Copilot. Keep the two in sync when project scope changes.
