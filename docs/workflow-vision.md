# Reflectometry Data Pipeline

## During the Experiment
- Every experiment has a uniquer identifier, which follows the format `IPTS-<N>`, where N is a number.
- Users will align their sample and measure it. At the facility, the raw data goes into a folder named `/SNS/REF_L/IPTS-<N>/nexus`.
- A full reflectometry measurement is often make of several segments, each acquired in a separate configuration of the instrument. This is done by changing the angle of reflection and the wavelength band.
- The raw data is "reduced" from neutron events to R(Q). These are stored in `/SNS/REF_L/IPTS-<N>/shared/autoreduce`. When several runs/segments belong together, a file that combines them is also produced.

## Assessing Data Reduction
- Before moving to analysis, a SME will look at the data reduction and assess its correctness.
- This may be done by looking to artefacts in the data, or by looking at the overlap region between segments. This can point out issues like misalignment.
- Problem with the data reduction can also show up during analysis, so a coarse analysis is usually performed be moving to the full analysis phase.
- When issues are found, the reduction parameters/options may be changed, and the data for a given sample may be re-processed in batch.

## Starting the Analysis process
- Since the reflectivity data is small, it is often copied on the user's system. All the data (partial segments and combined data) are usually in the same folder.
- We will assume that the user will have a markdown file for each sample, describing the sample and how it was measured.
- From the description, we will use AuRE to generate an appropriate refl1d model file.
- The user may load that file in refl1d, or use AuRE for automated fitting.
- We then use AuRE to assess the results and produce a final human-readable output, and a markdown file with fit parameters and plots.

## Analyzer Package Upgrade Project

The original analyzer package dates from last summer, when the coding agents
were not as good and agent skills didn't exist. We upgraded this package in
Spring 2026 to use a modern approach. Status of the planned updates:

1- **Model creation** ✅ *(Spring 2026)* — `create_model_script.py` and
`create_temporary_model.py` have been removed and replaced by
`analyzer_tools/analysis/model_from_aure.py`. The `create-model` CLI accepts
either a plain-English sample description (shells out to `aure analyze -m 0`)
or an existing `ModelDefinition` JSON, and converts it to an
analyzer-convention refl1d script.

2- **Data assessor** ✅ *(Spring 2026)* — `partial_data_assessor.py` now emits
structured metrics (`compute_metrics`, `write_metrics_json`), classifies each
overlap against a configurable χ² threshold, and optionally augments the
report with LLM commentary via `aure.llm` (`--llm-commentary`).

3- **Executing fits** ✅ *(Spring 2026)* — `run_fit.py` defaults to an AuRE
wrapper (`aure analyze` with a sample description). The legacy in-process
fitter is still available behind `--legacy` with a deprecation warning.

4- **Assessing fits** ✅ *(Spring 2026)* — `result_assessor.py` invokes
`aure evaluate --json` after the existing plotting/reporting and appends an
`## LLM Evaluation (AuRE)` section to the markdown report. `--skip-aure-eval`
disables it.

5- **Overall workflow** ✅ *(Spring 2026)* — `analyzer_tools/pipeline.py`
provides an `analyze-sample` CLI that drives the full workflow for one
sample: partial assessment → reduction-issue gate → AuRE model creation →
AuRE fit → AuRE evaluation. A `.pipeline_state.json` cache enables resume,
and a reduction-issue gate emits `reduction_issues.md` plus a pre-filled
`reduction_batch.yaml` manifest (analyzer-batch format) for user review —
reduction is never executed automatically. No LangChain.

6- **Agent skills refactor** ✅ *(Spring 2026)* — The `fit-evaluation` skill
was merged into `fitting`. The new `pipeline` skill documents
`analyze-sample`. The `models` and `distributable` skills were updated to
describe the AuRE-backed `create-model` workflow and removed all references
to the retired `create-temporary-model` tool.

