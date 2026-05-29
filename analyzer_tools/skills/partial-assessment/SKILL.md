---
name: partial-assessment
description: >
  Assess partial reflectometry data overlap quality before combining.
  USE FOR: checking whether partial curves (parts) from different angular settings
  are consistent, diagnosing data quality issues, generating quality reports.
  DO NOT USE FOR: fitting combined data (see fitting skill) or understanding data
  layout (see data-organization skill).
---

# Partial Data Assessment

## When to Use

Before analyzing combined reflectivity data, verify that the partial curves
(typically 3 parts measured at different angles) are consistent in their
overlap regions. Poor overlap indicates potential issues with normalization,
alignment, or sample changes during measurement.

## Usage

```bash
assess-partial <SET_ID>
```

**Examples:**
```bash
assess-partial 218281
assess-partial 218386 --data-dir data/partial --output-dir reports
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `SET_ID` | (required) | Numeric identifier for the measurement set |
| `--data-dir` | from `ANALYZER_PARTIAL_DATA_DIR` | Directory containing partial data files |
| `--output-dir` | from `ANALYZER_REPORTS_DIR` | Directory for report output |

## What It Does

1. **Finds partial files** matching `REFL_{SET_ID}_*_partial.txt` in the data directory
2. **Reads each part** (4 columns: Q, R, dR, dQ, with 1-line header)
3. **Identifies overlap regions** where adjacent parts share Q ranges
4. **Calculates overlap chi-squared** for each pair of overlapping parts:

$$\chi^2 = \frac{1}{N}\sum_i \frac{(R_1(Q_i) - R_2^{\text{interp}}(Q_i))^2}{\sigma_1(Q_i)^2 + \sigma_2^{\text{interp}}(Q_i)^2}$$

where $R_2^{\text{interp}}$ is interpolated onto the Q points of part 1.

5. **Generates a plot** of all partial curves on log-log scale
6. **Writes a markdown report** with overlap metrics

## Quality Thresholds

| χ² overlap | Assessment | Meaning |
|-----------|------------|---------|
| < 1.5 | Good | Parts are consistent — safe to combine |
| 1.5 – 3.0 | Acceptable | Minor discrepancies — review plot for systematic trends |
| > 3.0 | Poor | Significant mismatch — investigate before combining |

## Output Files

| File | Location | Contents |
|------|----------|----------|
| `report_{SET_ID}.md` | `{output_dir}/` | Markdown report with overlap chi-squared metrics |
| `reflectivity_curve_{SET_ID}.svg` | `{output_dir}/` | Plot of all partial curves |

## Example Output

A typical report section:

```markdown
## Partial Data Assessment: 218281

- Number of parts: 3
- Overlap 1-2: χ² = 0.87 (good)
- Overlap 2-3: χ² = 1.23 (good)
- Overall quality: good
```

## Requirements

- At least 2 partial files must exist for the given `SET_ID`
- Files must follow the naming convention `REFL_{SET_ID}_{PART_ID}_{RUN_ID}_partial.txt`
- Each file must have 4 columns (Q, R, dR, dQ) with a 1-line header
