# CLAUDE.md — agent instructions for the analyzer repo

This repository is a **toolbox of small, well-named CLI commands** for neutron reflectometry analysis. Per-workflow knowledge lives in `SKILL.md` files under [analyzer_tools/skills/](analyzer_tools/skills/). The expected interaction model is:

> An LLM agent (or human) chains the existing CLIs together. The agent does **not** invent fresh analysis scripts; it picks the right tool, configures it, runs it, and reports.

[README.md](README.md) is the user-facing entry point and the source of truth for what the project does. Read it before suggesting any new workflow.

## When the user asks you to *analyze data*

Pick the highest-level CLI that already covers the task — don't write a one-off Python script unless the user explicitly asks.

- **One sample, end to end** → `analyze-sample sample.yaml`. The YAML uses the `create-model --config` shape (see [analyzer_tools/skills/create-model/SKILL.md](analyzer_tools/skills/create-model/SKILL.md)). The pipeline runs partial-data checks → reduction-issue gate → `create-model` → `run-fit` → `assess-result`.
- **Many samples** → write one YAML per sample, dispatch with `analyzer-batch manifest.yaml`. Pattern in [README.md](README.md#L153-L213).
- **A new partial file just arrived** → `plan-data DATA_FILE CONTEXT_FILE` produces a config YAML you can feed straight into `analyze-sample` or `create-model --config`.
- **Lower-level control** (regenerate a model without re-fitting; refit an existing script with different settings) → call `create-model` / `run-fit` / `assess-result` directly, optionally from an `analyzer-batch` manifest.
- **Reduction-related work** → `simple-reduction` (Mantid-based; needs Docker, see [docs/docker.md](docs/docker.md)) and `theta-offset` (Mantid-free incident-angle offset).

For any tool, run it with `--help` for the canonical signature. The installed commands are the `[project.scripts]` entries in [pyproject.toml](pyproject.toml). Workflow-level guidance is in the skill files — pull the relevant one into context with `@analyzer_tools/skills/<name>/SKILL.md` rather than guessing.

### Reflectometry data conventions
- Combined reflectivity files: 4 columns `Q, R, dR, dQ`, plotted as `R vs Q` with `dR` as error bars.
- Partial files: named `REFL_<set_ID>_<part_ID>_<run_ID>_partial.txt`. A complete curve is usually 3 parts (`part_ID` 1–3) sharing a `set_ID` (the first run_ID of the set). Live under `data/partial/` by convention.
- "Combined data" with no qualifier means the final assembled file, not partials.
- Domain primer if you need it: [analyzer_tools/skills/reflectometry-basics/SKILL.md](analyzer_tools/skills/reflectometry-basics/SKILL.md).

## When the user asks you to *change the codebase*

This is the more common case in this repo. Conventions:

- **Tool layout**: every CLI is registered in three places that must stay in sync:
  1. The implementation module under `analyzer_tools/` (or `analyzer_tools/analysis/`, `analyzer_tools/reduction/`), exposing a Click `main()`.
  2. A thin wrapper in [analyzer_tools/cli.py](analyzer_tools/cli.py) (e.g. `plan_data_cli`).
  3. A `[project.scripts]` entry in [pyproject.toml](pyproject.toml). (Batch-dispatchable tools also need a `TOOL_COMMANDS` entry in [analyzer_tools/batch.py](analyzer_tools/batch.py).)
- **CLIs use Click.** New options follow the existing pattern: long-form `--option`, sensible defaults shown, mutually-exclusive options validated explicitly with `click.UsageError`.
- **Skills are package data, not docs.** They live in [analyzer_tools/skills/](analyzer_tools/skills/) and ship inside the wheel via `[tool.setuptools.package-data]`. The runtime loader is [analyzer_tools/analysis/plan_data.py](analyzer_tools/analysis/plan_data.py) `load_skills()`. To iterate on skill text without reinstalling, set `ANALYZER_SKILLS_DIR=/path/to/overrides` (per-skill `<name>/SKILL.md`); the loader prefers it over the packaged copy.
- **Tests live in [tests/](tests/)** and run with `pytest`. The default invocation has a coverage floor that's noisy mid-edit; for fast iteration use:
  ```bash
  .venv/bin/python -m pytest -x --no-cov -q
  ```
- **No new abstractions for hypothetical needs.** This repo follows YAGNI hard — three similar lines beats a premature helper. Match the surrounding style.

### When you change a CLI signature

Skills are the agent-facing reference; stale skills produce silent wrong behavior in `plan-data` and downstream callers. After any CLI change:

1. Update the matching skill in [analyzer_tools/skills/](analyzer_tools/skills/).
2. Update the single-file summary [analyzer_tools/skills/distributable/SKILL.md](analyzer_tools/skills/distributable/SKILL.md) — external users and downstream repos rely on it.
3. If a tool was added or removed entirely, also update `[project.scripts]` in [pyproject.toml](pyproject.toml) and, if it's batch-dispatchable, `TOOL_COMMANDS` in [analyzer_tools/batch.py](analyzer_tools/batch.py).
4. If quality thresholds, column formats, or file-naming conventions changed, also touch [analyzer_tools/skills/data-organization/](analyzer_tools/skills/data-organization/) and [analyzer_tools/skills/fitting/](analyzer_tools/skills/fitting/).

## Skills index

| Skill | Topic |
|---|---|
| [pipeline](analyzer_tools/skills/pipeline/) | End-to-end `analyze-sample` workflow |
| [create-model](analyzer_tools/skills/create-model/) | `create-model` modes A (JSON) & B (LLM/AuRE) |
| [fitting](analyzer_tools/skills/fitting/) | `create-model` → `run-fit` → `assess-result` |
| [partial-assessment](analyzer_tools/skills/partial-assessment/) | Overlap-χ² check on partial files |
| [theta-offset](analyzer_tools/skills/theta-offset/) | Single & batch theta-offset calculation |
| [plan-data](analyzer_tools/skills/plan-data/) | New-data-file planner |
| [data-organization](analyzer_tools/skills/data-organization/) | Layout, naming, column formats |
| [models](analyzer_tools/skills/models/) | Available refl1d model files |
| [reflectometry-basics](analyzer_tools/skills/reflectometry-basics/) | Domain primer (Q, R, SLD, χ²) |
| [tool-output](analyzer_tools/skills/tool-output/) | `--json` + `ndip-tool-result` manifest contracts |
| [distributable](analyzer_tools/skills/distributable/) | Single-file summary for external repos |

## Practical Claude Code tips for this repo

- **Cross-cutting searches** ("where is X used?", "how does pipeline call run-fit?") → `Agent` with `subagent_type=Explore` instead of many separate greps; keeps the main context clean.
- **Multi-step CLI runs** (especially `analyzer-batch` over many samples, or a debugging chain through `plan-data` → `create-model` → `run-fit`) → use `TodoWrite` to track which sample/job is at which stage. Some fits take minutes.
- **Configuration**: cd into a sample folder and the analyzer resolves five role-based dirs under `$PWD` (`rawdata/`, `models/`, `results/`, `reports/`). Full cascade rules in [docs/configuration.md](docs/configuration.md). LLM features need `~/.config/analyzer/.env`; verify with `check-llm`.
- **Don't run reductions automatically.** The pipeline gates on a reduction-issue check and emits a `reduction_batch.yaml` manifest for the user to review and dispatch. Surface it; don't dispatch it for them.
