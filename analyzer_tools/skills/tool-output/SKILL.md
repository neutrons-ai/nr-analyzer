---
name: tool-output
description: >
  Machine-readable output contracts for the analyzer CLIs: the `--json` stdout
  summaries and the neutral `ndip-tool-result/1` manifest (`--result-out`).
  USE FOR: driving the tools programmatically or from an orchestrator, parsing
  tool results, deciding what flag to pass for machine output.
  DO NOT USE FOR: the per-tool scientific arguments (run each tool with `--help`).
---

# Tool output contracts

The analyzer CLIs print human-readable text by default. For programmatic use
there are two distinct, complementary machine outputs:

- **`--json`** — print a JSON summary to **stdout** (suppresses the human text).
  Use it when you invoke a tool and want to parse what it computed.
- **`--result-out PATH`** — write a neutral **`ndip-tool-result/1`** manifest
  JSON to a file. Use it when an orchestrator drives the tool and later folds
  the result into its own bookkeeping. A tool may support either, both, or
  neither.

## Which tool supports what

| Tool | `--json` (stdout) | `--result-out` (manifest) |
|---|---|---|
| `assess-partial` | ✅ overlap metrics dict | — |
| `assess-result` | ✅ combined assessment dict | — |
| `check-llm` | ✅ provider/model/availability | — |
| `theta-offset` | ✅ offset result dict | — |
| `assemble-partials` | ✅ assembly summary dict | ✅ |
| `simple-reduction` | ⚠️ **writes a FILE** (`--json PATH`), not stdout | ✅ |
| `plan-data` | — | ✅ |
| `analyze-sample` | — | ✅ |
| `create-model` | — | — |
| `run-fit` | — (delegates to `assess-result`) | — |
| `analyzer-batch` | — | — |

**Convention:** `--json` is a stdout boolean flag everywhere **except
`simple-reduction`**, where `--json PATH` writes the summary to a file (a
historical quirk — prefer `--result-out` for the neutral manifest there).

## The `ndip-tool-result/1` manifest

Written by `--result-out` (`analyzer_tools/result_manifest.py`). A tool reports
what it did in **its own vocabulary**; an orchestrator maps that into whatever
state it keeps. The shape:

```json
{
  "tool": "simple-reduction",
  "tool_version": "0.2.0",
  "schema": "ndip-tool-result/1",
  "status": "ok",
  "exit_code": 0,
  "params":    { "resolved inputs the tool used": "..." },
  "artifacts": { "files the tool produced": "/abs/path" },
  "info":      { "scalar diagnostics": 123 },
  "messages":  [ { "level": "warning", "text": "..." } ]
}
```

- `status` is one of: **`ok`**, **`failed`**, **`skipped`**, **`dry-run`**,
  **`needs-reprocessing`**. (An orchestrator typically maps `dry-run`→ok and
  `needs-reprocessing`→failed-with-retry.)
- `None` values are dropped from `params` / `artifacts` / `info`.
- `messages` is omitted when empty.
- The manifest is schema-agnostic and dependency-free — the same module ships
  byte-identically in data-assembler and nr-isaac-format so the contract is
  shared across the pipeline.

## `--json` payloads (stdout)

- **`assess-partial`** — the overlap-metrics dict (per-pair χ², classification,
  worst χ², Q ranges).
- **`assess-result`** — the combined assessment (fit parameters, χ², and, when
  available, the appended AuRE verdict).
- **`check-llm`** — `{provider, model, available, ok, ...}`.
- **`theta-offset`** — `{run_name, db_pixel, rb_pixel, delta_pixel, theta_motor,
  theta_calc, offset, mean_wl, gravity_dtheta}` (internal `_`-prefixed keys are
  stripped).
- **`assemble-partials`** — `{set_id, n_segments, segments, n_points, scaled,
  scale_factors, overlap_chi2, output}`.
