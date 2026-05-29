# Docker (full stack with Mantid)

The Docker image uses [pixi](https://pixi.sh) to install Mantid and
[lr_reduction](https://github.com/neutrons/LiquidsReflectometer) from
conda channels, then installs `nr-analyzer` via pip on top.

```bash
docker compose build
docker compose run analyzer bash          # interactive shell
docker compose run analyzer analyze-sample sample.yaml   # create-model --config shape (describe + states)
docker compose run test                   # run the test suite
```

Output files appear on the host through the volume mounts in
`docker-compose.yml` (`models/`, `results/`, `reports/`, and a data dir; the
data dir defaults to `rawdata/` — see [configuration.md](configuration.md)).

Use Docker when you need the Mantid-based reduction tool
(`simple-reduction`); for analysis-only work, a local
`pip install -e ".[dev]"` is faster.
