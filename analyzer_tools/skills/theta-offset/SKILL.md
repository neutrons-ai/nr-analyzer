---
name: theta-offset
description: >
  Compute theta offsets for Liquids Reflectometer (BL-4B) runs by fitting
  the specular peak and comparing with the motor-log angle. Supports single
  runs and batch processing via manifests.
  USE FOR: calculating theta offsets from NeXus event files, building offset
  CSV logs, batch-processing multiple runs.
  DO NOT USE FOR: fitting reflectivity data (see fitting skill), reducing
  neutron events (see time-resolved skill).
---

# Theta Offset Calculation

## Overview

The theta offset is the difference between the angle calculated from the
fitted specular-peak pixel position and the motor-log angle.  It is needed
to correct for small misalignments before reflectivity reduction.

$$\theta_{\text{offset}} = \theta_{\text{calc}} - \theta_{\text{motor}}$$

where $\theta_{\text{calc}}$ is derived from the pixel displacement between
the reflected beam and the direct-beam reference pixel.

## Prerequisites

Each run requires two files:

| File | Description | How to get it |
|------|-------------|---------------|
| NeXus event file | `REF_L_<run>.nxs.h5` | `/SNS/REF_L/IPTS-<id>/nexus/` |
| Direct-beam reference | `DB_<run>.dat` **or** raw `REF_L_<run>.nxs.h5` | `/SNS/REF_L/shared/autoreduce/` or the nexus archive |

The `--db` option accepts **either** format:
- **`.dat` file** — pre-processed, must have a comment header with `db_pixel = <value>` and `tthd = <value>`.
- **`.h5` / `.nxs.h5` file** — raw NeXus event file; the tool will fit the direct-beam peak automatically and read `tthd` from the motor logs.

## Template XML — per-segment DB mapping

**Important:** The direct-beam (DB) run is NOT the same for every segment.
Each angle setting typically uses a different DB run.  The correct mapping
is stored in the reduction **template XML** file
(`*_auto_template.xml`).

### Template XML structure

The template XML contains one `<RefLData>` block per segment.  Each block
has a `<data_sets>` element (the sample run ID) and a `<norm_dataset>`
element (the DB run ID for that segment):

```xml
<RefLData>
  <data_sets>226642</data_sets>
  ...
  <norm_dataset>226559</norm_dataset>
  ...
</RefLData>
```

### Where to find the template

Template XML files are usually created by the auto-reduction system and
stored alongside the reduced data, e.g.:

```
/SNS/REF_L/IPTS-<id>/shared/autoreduce/REF_L_<first_run>_auto_template.xml
```

or in the experiment's data directory.

### When to ask the user

**If the user does not provide a template XML or explicit `--db` for each
segment, always ask for one.**  The DB run cannot be assumed — using the
wrong DB will produce incorrect theta offsets.

Prompt: *"Which template XML should I use to determine the DB runs for
each segment?  The DB varies per angle setting."*

## Single-Run Usage

```bash
# With explicit DB
theta-offset <nexus_file> --db <db_file>

# With template XML (auto-resolves DB per segment)
theta-offset <nexus_file> --template <template.xml>
```

### Examples

```bash
# Basic usage with a pre-processed DB
theta-offset REF_L_226642.nxs.h5 --db DB_226559.dat

# Use a raw NeXus file as the DB instead
theta-offset REF_L_226642.nxs.h5 --db REF_L_226559.nxs.h5

# Use a template XML to auto-resolve the DB for this segment
theta-offset REF_L_226642.nxs.h5 --template REF_L_226642_auto_template.xml

# Override y-pixel fitting range
theta-offset REF_L_226642.nxs.h5 --db DB_226559.dat --ymin 135 --ymax 170

# Append result to a CSV log
theta-offset REF_L_226642.nxs.h5 --db DB_226559.dat --log offsets.csv
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `NEXUS` | (required) | Path to NeXus event file (.nxs.h5) |
| `--db` | — | Direct-beam reference: `.dat` file or raw `.h5` NeXus |
| `--template` | — | Reduction template XML — auto-resolves DB per segment |
| `--ymin` | auto | Lower y-pixel bound for peak fitting |
| `--ymax` | auto | Upper y-pixel bound for peak fitting |
| `--xmin` | 50 | Low-resolution x-pixel min |
| `--xmax` | 200 | Low-resolution x-pixel max |
| `--peak-type` | supergauss | Peak model: `gauss` or `supergauss` |
| `--log` | — | Append result to this CSV file |

> **Note:** Provide exactly one of `--db` or `--template`.  When using
> `--template`, the tool extracts the run ID from the NeXus filename, looks
> it up in the template XML, and searches for the DB file in the same
> directory as the NeXus file (or the current directory).

## Batch Usage (Manifest)

To process many runs at once, create a YAML manifest and use
`analyzer-batch`.  Each segment should use the correct DB; the easiest way
is to pass `--template`:

```yaml
jobs:
  - name: segment_1
    tool: theta-offset
    args:
      - /data/REF_L_226642.nxs.h5
      - --template
      - /data/REF_L_226642_auto_template.xml
      - --log
      - offsets.csv

  - name: segment_2
    tool: theta-offset
    args:
      - /data/REF_L_226643.nxs.h5
      - --template
      - /data/REF_L_226642_auto_template.xml
      - --log
      - offsets.csv
```

Alternatively, specify `--db` explicitly per segment (useful when DB runs
are already known):

```yaml
jobs:
  - name: segment_1
    tool: theta-offset
    args:
      - /data/REF_L_226642.nxs.h5
      - --db
      - /data/REF_L_226559.nxs.h5
      - --log
      - offsets.csv

  - name: segment_2
    tool: theta-offset
    args:
      - /data/REF_L_226643.nxs.h5
      - --db
      - /data/REF_L_226560.nxs.h5
      - --log
      - offsets.csv
```

```bash
# Run all jobs
analyzer-batch manifest.yaml

# Dry run (show commands without executing)
analyzer-batch manifest.yaml --dry-run

# Run specific jobs only
analyzer-batch manifest.yaml --jobs copper_offset,gold_offset
```

See `manifest.example.yaml` in the repo root for a complete example.

## Output

### Console output

```
Run:            REF_L_226642.nxs.h5
DB pixel:       152.30
Fitted pixel:   153.12  (delta = +0.82 px)
Theta (motor):  0.6200°
Theta (calc):   0.6232°
Offset:         +0.0032°
Mean λ:         5.42 Å
Gravity Δθ:     +0.000312°  (at mean λ)
```

### CSV log columns

When `--log` is used, each run appends a row to the CSV with columns:

| Column | Description |
|--------|-------------|
| `timestamp` | ISO-8601 time of calculation |
| `nexus` | NeXus file basename |
| `db_file` | DB file basename |
| `db_pixel` | Direct-beam reference pixel |
| `rb_pixel` | Fitted reflected-beam pixel |
| `delta_pixel` | Pixel displacement (rb − db) |
| `theta_motor` | Motor-log angle (degrees) |
| `theta_calc` | Calculated angle (degrees) |
| `offset` | Theta offset (degrees) |
| `mean_wl` | Intensity-weighted mean wavelength (Å) |
| `gravity_dtheta` | Gravity-induced angular offset (degrees) |

## Python API

For programmatic use (e.g., from a notebook or another tool):

```python
from analyzer_tools.analysis.theta_offset import (
    compute_theta_offset,
    log_result,
    parse_template_xml,
)

# --- Using explicit DB ---
result = compute_theta_offset(
    "REF_L_226642.nxs.h5",
    "DB_226559.dat",
    peak_type="supergauss",
)
print(f"Offset: {result['offset']:+.4f}°")

# --- Using template XML to resolve DB per segment ---
mapping = parse_template_xml("REF_L_226642_auto_template.xml")
# mapping = {"226642": "226559", "226643": "226560", "226644": "226561"}

for run_id, db_run_id in mapping.items():
    result = compute_theta_offset(
        f"REF_L_{run_id}.nxs.h5",
        f"REF_L_{db_run_id}.nxs.h5",
    )
    print(f"Run {run_id}: offset = {result['offset']:+.4f}°")

# Optionally log to CSV
log_result(result, "offsets.csv", db_path="DB_226559.dat")
```

### Return dict keys

| Key | Type | Description |
|-----|------|-------------|
| `run_name` | str | Basename of the NeXus file |
| `db_pixel` | float | Direct-beam reference pixel |
| `rb_pixel` | float | Fitted reflected-beam pixel |
| `delta_pixel` | float | Pixel displacement |
| `theta_motor` | float | Motor angle (degrees) |
| `theta_calc` | float | Calculated angle (degrees) |
| `offset` | float | Theta offset (degrees) |

## Interpreting Results

| Offset magnitude | Assessment |
|-------------------|-----------|
| < 0.005° | Negligible — alignment is good |
| 0.005° – 0.02° | Typical — apply correction during reduction |
| > 0.02° | Large — verify sample alignment and DB file |

A consistent offset across many runs from the same IPTS suggests a
systematic alignment shift that can be corrected globally.
