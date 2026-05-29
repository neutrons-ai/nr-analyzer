---
name: models
description: >
  Refl1d model files for neutron reflectometry fitting — how they work,
  available models, and how to create or modify them. USE FOR: choosing a model,
  understanding model structure, creating new models, adjusting parameter ranges.
  DO NOT USE FOR: running fits (see fitting skill) or data organization.
---

# Reflectometry Models

## What Is a Model File?

A model file is a Python module in the `models/` directory that defines a function:

```python
def create_fit_experiment(q, dq, data, errors):
    """
    Parameters
    ----------
    q : array — Momentum transfer values (1/Å)
    dq : array — Q resolution, FWHM (1/Å)
    data : array — Measured reflectivity R
    errors : array — Uncertainty on R (dR)

    Returns
    -------
    refl1d.experiment.Experiment
    """
```

The function builds a layer model using refl1d's `SLD`, `Slab`, and `Experiment` classes, sets parameter ranges for fitting, and returns an `Experiment` object.

## Layer Model Structure

Models define a sample as a stack of layers separated by `|`, ordered from top (incident medium) to bottom (substrate):

```python
from refl1d.names import *

THF = SLD("THF", rho=5.8)
Si = SLD("Si", rho=2.07)
Ti = SLD("Ti", rho=-1.2)
Cu = SLD("Cu", rho=6.25)
material = SLD(name="material", rho=5, irho=0.0)

sample = THF(0, 11.4) | material(58, 13) | Cu(505, 4.6) | Ti(39.5, 9.1) | Si
#        ^incident       ^layers...                                         ^substrate
```

Each `SLD(thickness, interface)` call creates a slab. Parameters are constrained with `.range(min, max)`:

```python
sample["material"].thickness.range(10.0, 200.0)
sample["material"].material.rho.range(5.0, 12.0)
sample["material"].interface.range(1.0, 33.0)
```

## Generating Models

The `create-model` command is the primary way to produce analyzer-convention
model scripts. Two modes:

- **Mode A** — convert an existing **AuRE problem JSON** (from
  `aure prepare`/`aure batch`) into a script.
- **Mode B** — generate a script **directly via LLM** from a sample
  description and one or more data files. Mode B auto-detects the fitting
  case from the file names:
  - **Case 1**: one combined data file (`QProbe` fit).
  - **Case 2**: multiple partial files sharing a `set_id` (`make_probe`
    per segment).
  - **Case 3**: multiple combined files co-refined with shared structural
    parameters (not supported by AuRE — only by this tool).

```bash
# Mode A — from a problem JSON
create-model path/to/problem.json --out models/cu_thf.py

# Mode B — description + one combined file (case 1)
create-model --describe "50 nm Cu / 3 nm Ti on Si in D2O" \
             --data data/REFL_226642_combined_data_auto.txt \
             --out models/cu_d2o.py

# Mode B — co-refine two combined files (case 3)
create-model --describe "2 nm CuOx / 50 nm Cu / 3 nm Ti on Si in D2O" \
             --data data/REFL_226642_combined_data_auto.txt \
             --data data/REFL_226652_combined_data_auto.txt \
             --out models/Cu-D2O-corefine.py

# Any mode — options driven from a YAML or JSON config
create-model --config model-creation.yaml
```

See the dedicated [create-model skill](../create-model/SKILL.md) for the full
option list, the LLM JSON schema, and the case-3 `shared_parameters` rules.

## Adjusting Parameter Ranges

To widen or tighten a parameter range, **edit the model file directly**:

```python
# models/cu_thf.py
Cu = SLD(name="Cu", rho=6.4)(thickness=Cu_thickness, interface=4.6)
Cu_thickness.range(300, 1000)   # edit these bounds
```

Then re-run `run-fit` and `assess-result`. The previous
`create-temporary-model` CLI has been removed — editing is clearer and keeps
a single source of truth per model.
