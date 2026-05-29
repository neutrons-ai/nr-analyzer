# Plan: Spring 2026 Analyzer Package Upgrade (AuRE integration)

## TL;DR

Refactor the analyzer from a heuristics-based, script-generating tool chain into
a lean **orchestrator of AuRE** plus a handful of domain-specific tools AuRE
doesn't provide (partial-data overlap QC, theta-offset, EIS/time-resolved,
iceberg packaging). The analyzer becomes the "pipeline glue" that wraps AuRE
while keeping its own niche tools. Remove or wrap legacy create-model /
create-temporary-model / run-fit / result_assessor behind AuRE calls.

**Approach:** incremental, six phases. Each phase is independently shippable
and leaves tests green. No LangChain in the analyzer — AuRE already owns that.

## Reference material examined

- `docs/workflow-vision.md` (upgrade goals)
- `~/git/aure` — AuRE CLI: `aure analyze`, `aure evaluate`, `aure extract-features`,
  `aure lookup-sld`, `aure batch`, `aure plot-results`, `aure resume`, `aure mcp-server`.
  - `aure evaluate REFL1D_DIR` already wraps LLM-based fit assessment
  - AuRE stores **ModelDefinition** (dict: substrate/layers/ambient/intensity/…)
    as `models/NNN_model_<stage>.json` alongside a generated `.py` script
  - `aure.nodes.model_builder.build_experiment/build_problem/build_multi_problem`
    are the canonical converters ModelDefinition → refl1d
  - AuRE CLI has NO standalone "generate model" subcommand yet
    (only inside `aure analyze`). Vision doc confirms it's on their TODO.
- Example refl1d outputs in `~/git/experiments-2025/jen-oct2025/results/`:
  - Sample4/223960/Pt-ionomer-parts_SL1_SL2_223960.json — bumps-draft-03
    FitProblem with multiple models + `references` dict (co-refinement)
  - 223992/…-expt.json — per-model iteration snapshots
- Analyzer skills (`skills/`) — 10 skills; several already reference AuRE
  (fit-evaluation, distributable)

## Architecture decisions

1. **Model representation**: adopt AuRE's ModelDefinition JSON as the canonical
   model format for the upgraded analyzer. Keep existing hand-written `models/*.py`
   working (legacy path) but stop generating new ones.
2. **Fit execution**: delegate to AuRE. `run-fit` becomes a thin wrapper that
   either (a) prints the recommended `aure analyze …` one-liner, or
   (b) invokes AuRE subprocess for convenience. Preference: **both** via a
   `--dry-run`/default flag.
3. **Fit assessment**: `assess-result` continues to produce the analyzer's
   SLD uncertainty bands & markdown report (AuRE doesn't do SLD bands), then
   automatically runs `aure evaluate` and appends the LLM verdict/suggestions
   to the same report.
4. **Workflow orchestration**: a new `analyzer-pipeline` (or
   `analyze-sample`) CLI runs `partial → (theta-offset) → aure analyze →
   assess-result` for one sample, driven by a small per-sample markdown
   description file as the vision doc specifies. No LangChain, no graph —
   just sequential steps with a simple resume/skip mechanism.
5. **AuRE as a required dependency** (in a `[aure]` extra). The analyzer
   imports AuRE Python APIs directly where useful (for ModelDefinition →
   refl1d script conversion) rather than shelling out everywhere.

## Phases

### Phase 1 — Model generation via AuRE (replaces create-model / create-temporary-model)

*Parallel with Phase 2.*

1. Add a new module `analyzer_tools/analysis/model_from_aure.py` with:
   - `definition_to_script(defn: dict, data_file: str) -> str` — converts
     an AuRE ModelDefinition JSON into a self-contained refl1d Python script
     compatible with the existing analyzer convention
     (`create_fit_experiment(q, dq, data, errors)` entry point).
     Re-uses AuRE's `build_experiment` logic but emits source code, so users
     can still open the `.py` in refl1d/bumps and tweak manually.
   - `invoke_aure_modeling(sample_description: str, data_file: str) -> dict` —
     calls `aure analyze … --max-refinements 0` (i.e. generate-only), reads
     the last `models/*_model_initial.json`, returns the ModelDefinition.
     (Workaround until AuRE ships a dedicated model-generation CLI.)
2. Replace the CLI `create-model` with a new entry point backed by the two
   functions above. Signature:
   `create-model <sample_description_file.md> <data_file> [--out models/<name>.py]`.
   Keep the old `create-model <model_name> <data_file>` as an alias that
   just wraps the existing `models/<name>.py` into a fit script
   (no generation) — deprecation warning.
3. **Delete** `analyzer_tools/analysis/create_temporary_model.py`. Replace
   with a tiny helper `adjust-model <source.py> --set layer.param=min,max`
   living inside `create_model_script.py` (or drop it entirely — the
   refinement loop is now AuRE's job). **Recommendation: drop it**; the
   analyzer skill `fitting/` changes to tell users to re-run `aure analyze`
   for refinement. Confirm with user (see Further Considerations).
4. Update `skills/models/SKILL.md` and `skills/fitting/SKILL.md`.
5. Add tests: golden-file test that a known ModelDefinition produces a
   script that imports and defines `create_fit_experiment`.

### Phase 2 — Partial-data assessor refresh

*Parallel with Phase 1.*

1. Keep existing numeric overlap χ² logic (works well, it's the analyzer's
   unique value). Refactor `partial_data_assessor.py` into three functions:
   `compute_metrics`, `plot`, `render_report`.
2. Add **optional LLM commentary**: when `AURE_LLM` is configured, call
   `aure.llm.get_llm()` with a prompt summarising overlap χ² values and the
   plot description → short "expert commentary" paragraph appended to the
   markdown report. Graceful fallback when LLM unavailable.
3. Add new CLI options:
   - `--llm-commentary / --no-llm-commentary` (default: auto-detect)
   - `--json` (emit metrics as JSON for pipeline consumption)
4. Emit structured metrics (per-pair χ², q-range, n-overlap points) as a
   sidecar JSON so the orchestrator can decide whether to proceed.
5. Update `skills/partial-assessment/SKILL.md` with the new options + χ²
   thresholds already documented there.
6. Tests: structure of JSON output; LLM path behind a mock.

### Phase 3 — Run-fit → AuRE wrapper

*Depends on Phase 1 (for ModelDefinition support).*

1. Rewrite `analyzer_tools/analysis/run_fit.py`:
   - Default mode: print an `aure analyze <data> "<sample_description>"
     -o results/<set_id>_<model> -m 0` one-liner to stdout (plus `subprocess`
     invocation if `--run` given).
   - For **legacy `models/<name>.py`** users: detect a raw refl1d script
     (current behavior) and run it directly with bumps dream — preserving
     backward compatibility. Deprecation warning points users to AuRE.
   - New flag `--use-aure/--legacy` to force path.
2. Output directory layout stays `results/<set_id>_<model>/` so
   `assess-result` keeps working.
3. Update `skills/fitting/SKILL.md`.
4. Tests: dry-run prints the expected command; legacy path still fits a
   tiny model (existing tests should still pass).

### Phase 4 — Result-assessor augmentation

*Depends on Phase 3.*

1. Keep current SLD-uncertainty contour plot (unique, AuRE doesn't do it).
2. After producing the analyzer's markdown, automatically:
   - invoke `aure evaluate <results_dir> --context "<desc>" --json`
   - parse verdict/issues/suggestions/physical-plausibility
   - append a new **"LLM Evaluation"** section to the markdown report
3. New flags: `--skip-aure-eval`, `--sample-description PATH` or inline
   `--context TEXT`.
4. Return a machine-readable JSON combining both assessments
   (for the pipeline orchestrator).
5. Update `skills/fit-evaluation/SKILL.md` — merge it into `skills/fitting/`
   since the two are now combined? (See Further Considerations.)
6. Tests: mock `aure evaluate`, assert the merged report contains both sections.

### Phase 5 — Pipeline orchestrator

*Depends on Phases 1–4.*

1. New module `analyzer_tools/pipeline.py` + CLI `analyze-sample`.
2. Inputs: either
   - a **sample markdown file** (YAML frontmatter + markdown body) with
     `set_id`, `data_file`, optional `partial_dir`, `model`, `theta_offset`,
     `hypothesis`; body → AuRE `sample_description`; or
   - positional `<set_id>` with auto-discovery in configured dirs.
3. Steps (each individually skippable with flags / resumable from cache):
   1. `assess-partial` (if partial data exists)
   2. `theta-offset` (optional, if flag or description requests)
   3. **Reduction-issue gate** (see Phase 5b below) — if partial QC or
      theta-offset indicate the raw data needs to be re-reduced, **stop**
      the pipeline and emit guidance + a reduction batch YAML.
      Reduction is *never* run automatically.
   4. `create-model` via AuRE (skippable if `--model <existing>`)
   5. `run-fit` via AuRE
   6. `assess-result` (with AuRE evaluate)
   7. Write consolidated `reports/sample_<set_id>.md` and `sample_<set_id>.json`
4. Simple state cache under `reports/<set_id>/.pipeline_state.json` so
   rerunning skips completed stages unless `--force`.
5. **Not** LangGraph-based — plain Python sequential with structured
   logging and a `--dry-run` preview.
6. Add `skills/pipeline/SKILL.md` describing the full workflow.
7. Tests: end-to-end on `tests/sample_data/` with mocked AuRE.

### Phase 5b — Reduction-issue detection & handoff (NEW)

*Part of Phase 5; shipped together.*

The analyzer must detect when the input reduction is flawed and tell the
user to **reprocess the raw data**, without ever kicking off reduction
itself (reduction needs Mantid + facility data access, runs outside this
pipeline).

1. **Detection heuristics** — triggered automatically during Phase 5:
   - **Partial overlap χ² > 3.0** (threshold in `skills/partial-assessment/`)
     for any adjacent pair → suggests bad normalization / DB run / misalignment.
   - **|theta_offset| exceeds a configurable threshold** (default 0.01°)
     for any segment → suggests the reduction was done with the wrong
     offset or no offset at all.
   - **Systematic trend in overlap residuals** (if LLM commentary enabled)
     flagged by the partial-assessor's LLM output.
   - Collect all findings into a structured `reduction_issues` list, each
     with `type`, `segment`/`set_id`, `severity` (warn|block), `detail`.
2. **Decision rule**: if any `severity == block` is present, the pipeline
   **halts after Phase 5 step 2** (no model generation, no fit) and emits
   the handoff artifacts below. `warn`-only issues are noted but the
   pipeline continues.
3. **Emitted artifacts** under `reports/sample_<set_id>/`:
   - `reduction_issues.md` — human-readable summary: which segments,
     what the metrics say, why it matters, step-by-step guidance
     ("rerun `simple-reduction` with the correct DB from the template
     XML and apply the computed theta-offset CSV"). Includes links to
     `skills/theta-offset/SKILL.md` and the existing reduction docs.
   - `reduction_batch.yaml` — an **analyzer-batch-compatible manifest**
     pre-filled with one `simple-reduction` job per segment, using:
       - the discovered event files,
       - the template XML found for the set,
       - the newly computed offset CSV from the theta-offset step (if run).
     User only needs to review → run `analyzer-batch reduction_batch.yaml`.
   - `sample_<set_id>.json` — includes `status: "needs-reprocessing"` plus
     the structured `reduction_issues` list so downstream tools (LLM
     agents, dashboards) can react.
   - `sample_<set_id>.md` — top-level report with a prominent
     "⚠ Reprocessing required" banner and a short narrative.
4. **CLI additions** on `analyze-sample`:
   - `--reduction-gate/--no-reduction-gate` (default: on)
   - `--offset-threshold-deg FLOAT` (default: 0.01)
   - `--chi2-threshold FLOAT` (default: 3.0, matches skill doc)
   - `--write-reduction-yaml PATH` (defaults into the report dir)
5. **Tests**:
   - Fixture partial data with forced bad overlap → pipeline halts,
     `reduction_batch.yaml` valid YAML, matches `TOOL_COMMANDS` dispatch.
   - Fixture with good overlap & small offset → pipeline continues to
     fit stage.
   - Golden-file test for the emitted `reduction_issues.md`.
6. **Skill update**: `skills/pipeline/SKILL.md` gets a "Reprocessing loop"
   subsection; `skills/partial-assessment/` and `skills/theta-offset/`
   cross-reference it.

### Phase 6 — Skills consolidation & tooling cleanup

*Depends on Phases 1–5.*

1. Audit the 10 existing skills:
   - **Merge** `fit-evaluation` into `fitting` (single AuRE-aware workflow).
   - **Keep** `partial-assessment`, `theta-offset`, `time-resolved`,
     `data-packaging`, `data-organization`, `reflectometry-basics`, `models`.
   - **New** `pipeline` skill for the orchestrator (Phase 5).
   - **Update** `distributable/SKILL.md` — single-file summary with new CLI
     surface (`analyze-sample`, deprecated create-temporary-model removed,
     run-fit & assess-result AuRE-aware).
2. Update `pyproject.toml` `[project.scripts]` — drop `create-temporary-model`
   (if user confirms), add `analyze-sample`. Add `[project.optional-dependencies]
   aure = ["aure @ git+https://github.com/neutrons-ai/aure.git"]`.
3. Update `analyzer_tools/registry.py` TOOLS dict + `batch.py TOOL_COMMANDS`.
4. **Update `analyzer_tools/mcp_server.py` if present (not inspected yet) so
   Copilot / Claude can drive the orchestrator via MCP.** → **REMOVED**:
   delete `analyzer_tools/mcp_server.py` and any MCP entry points / deps
   (FastMCP). Analyzer no longer ships an MCP server.
5. Update `.github/copilot-instructions.md` mermaid workflow graph.
6. Update `docs/workflow-vision.md` — tick off the six items and describe
   the new pipeline.

## Relevant files

### To modify
- `analyzer_tools/analysis/create_model_script.py` — new AuRE-backed
  generation path; keep wrapping helper.
- `analyzer_tools/analysis/run_fit.py` — rewrite as AuRE wrapper +
  legacy fallback.
- `analyzer_tools/analysis/result_assessor.py` — append `aure evaluate`.
- `analyzer_tools/analysis/partial_data_assessor.py` — refactor + optional LLM.
- `analyzer_tools/registry.py` — update TOOLS entries.
- `analyzer_tools/batch.py` — `TOOL_COMMANDS` mapping.
- `analyzer_tools/cli.py` — register `analyze-sample`, drop deprecated.
- `pyproject.toml` — scripts, optional deps, version bump.
- `skills/{models,fitting,partial-assessment,distributable}/SKILL.md`.
- `docs/workflow-vision.md`.
- `.github/copilot-instructions.md`.

### To create
- `analyzer_tools/analysis/model_from_aure.py` — ModelDefinition↔script bridge.
- `analyzer_tools/pipeline.py` — sequential orchestrator.
- `skills/pipeline/SKILL.md` — new skill.
- `tests/test_model_from_aure.py`, `tests/test_pipeline.py`,
  updates to `tests/test_partial_data_assessor.py` and
  `tests/test_result_assessor.py`.

### To delete (pending user confirmation)
- `analyzer_tools/analysis/create_temporary_model.py`
- `tests/test_create_temporary_model.py`
- `skills/fit-evaluation/SKILL.md` (merged into `skills/fitting/`)

## Verification

1. `pytest -q` green after every phase.
2. `analyzer-tools --list-tools` shows updated catalog.
3. Manual end-to-end on `tests/sample_data/` set 218281: partial QC → AuRE
   model generation → fit → assessment, produces `sample_218281.md` with
   all four sections.
4. `aure evaluate` output is present in the markdown (mocked in tests,
   real in smoke test).
5. `batch.py`: run the example manifest after renaming a job to
   `analyze-sample`; confirm dispatch works.
6. `skills/distributable/SKILL.md` + `/skills/pipeline/SKILL.md` both mention
   the new CLI and match `pyproject.toml` `[project.scripts]`.
7. Review `.github/copilot-instructions.md` — the mermaid graph still matches
   behavior.

## Decisions

- **No LangChain / LangGraph** in analyzer (vision doc).
- Analyzer keeps its unique differentiators: partial-data overlap QC,
  theta-offset, EIS/time-resolved, iceberg packaging, SLD uncertainty bands.
- AuRE owns: model generation, iterative model refinement, LLM-driven fit
  evaluation.
- ModelDefinition JSON is the new source of truth for models; legacy
  `models/*.py` remain supported (read-only).
- Delete `create_temporary_model` — superseded by AuRE refinement
  *(subject to user confirmation)*.

## Confirmed by user

1. **Delete `create_temporary_model.py`** — AuRE refinement replaces it.
2. **Retire analyzer's MCP server** — no longer needed. Remove
   `analyzer_tools/mcp_server.py`, drop FastMCP references, drop any
   `mcp-server` script entry. Users drive via CLI or AuRE's MCP server.
3. **Sample description file**: YAML frontmatter + markdown body
   (AuRE-manifest-compatible). YAML fields: `set_id`, `data_file` (or
   auto-discover), optional `partial_dir`, `model` (to skip generation),
   `theta_offset`, `hypothesis`. Markdown body → AuRE `sample_description`.
