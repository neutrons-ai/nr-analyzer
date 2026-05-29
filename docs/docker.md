# Docker (full stack with Mantid)

The Docker image uses [pixi](https://pixi.sh) to install Mantid and
[lr_reduction](https://github.com/neutrons/LiquidsReflectometer) from
conda channels, then installs `analyzer-tools` via pip on top.

```bash
docker compose build
docker compose run analyzer bash          # interactive shell
docker compose run analyzer analyze-sample sample.yaml
docker compose run test                   # run the test suite
```

Output files appear on the host via volume mounts (`data/`, `models/`,
`reports/`, `results/`).

Use Docker when you need the Mantid-based reduction tools
(`simple-reduction`, `eis-reduce-events`); for analysis-only work, a local
`pip install -e ".[dev]"` is faster.
