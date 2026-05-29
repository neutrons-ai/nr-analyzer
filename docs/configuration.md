# Configuration

The analyzer CLIs read configuration from environment variables and `.env`
files. The goal is for you to be able to `cd` into a sample folder and run
`analyze-sample` without writing any per-sample boilerplate.

## Project root and role directories

Five directories must be resolvable. They default to subdirectories of the
**project root**, which defaults to the current working directory.

| Role | Default location | Subdir env override | Absolute env override |
|---|---|---|---|
| Combined data | `$PROJECT/rawdata` | `ANALYZER_DATA_SUBDIR` | `ANALYZER_COMBINED_DATA_DIR` |
| Partial data | falls back to combined | *(reuses `ANALYZER_DATA_SUBDIR`)* | `ANALYZER_PARTIAL_DATA_DIR` |
| Models | `$PROJECT/models` | `ANALYZER_MODELS_SUBDIR` | `ANALYZER_MODELS_DIR` |
| Results | `$PROJECT/results` | `ANALYZER_RESULTS_SUBDIR` | `ANALYZER_RESULTS_DIR` |
| Reports | `$PROJECT/reports` | `ANALYZER_REPORTS_SUBDIR` | `ANALYZER_REPORTS_DIR` |

`$PROJECT` is `ANALYZER_PROJECT_DIR` if set, otherwise `$PWD`. **It is never
derived from the location of a loaded `.env` file** — a repo-level `.env`
will not turn the repo root into a project root by accident.

If `ANALYZER_PARTIAL_DATA_DIR` is not set, partial files are looked up in
the combined-data directory.

The combined-data filename template is
`ANALYZER_COMBINED_DATA_TEMPLATE` (default
`REFL_{set_id}_combined_data_auto.txt`).

## `.env` cascade

CLIs walk a layered search and the first setter wins:

1. **Process environment** — anything you `export`ed already.
2. **`--env PATH`** (on commands that support it) or `$ANALYZER_ENV_FILE`.
3. **Project `.env`** — the nearest `.env` walking up from `$PWD`.
4. **User-global `.env`** — `~/.config/analyzer/.env`
   (override location with `$ANALYZER_CONFIG_DIR` or
   `$XDG_CONFIG_HOME/analyzer`).

Recommended split:

- **User-global** `~/.config/analyzer/.env` — LLM credentials shared by
  every project (`LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`).
- **Repo-level** `.env` (one above your sample folders) — sub-folder
  renames if you don't like the defaults, e.g.
  `ANALYZER_DATA_SUBDIR=Rawdata`.
- **Sample-folder** `.env` — only when a single sample needs to override
  something.

## LLM variables

Required for `create-model` Mode B and the `aure evaluate` augmentation:

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `openai`, `gemini`, `alcf`, or `local` |
| `LLM_MODEL` | Model name (e.g. `gpt-4o`, `gpt-oss:120b`) |
| `LLM_API_KEY` | API key (or `OPENAI_API_KEY` / `GEMINI_API_KEY`) |
| `LLM_BASE_URL` | Base URL for local / OpenAI-compatible endpoints |
| `LLM_TEMPERATURE` | Default `0.0` |
| `LLM_TIMEOUT` | Request timeout in seconds |

Run `check-llm` to verify the AuRE+LLM chain.
