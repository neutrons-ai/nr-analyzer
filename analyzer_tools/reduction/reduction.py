#!/usr/bin/env python3
"""
CLI entry point for neutron event reduction.

Usage::

    simple-reduction --event-file REF_L_12345.nxs.h5 --template template.xml

Requires ``mantid`` and ``lr_reduction``::

    pip install analyzer-tools[reduction]
"""

from __future__ import annotations

import csv
import glob
import json
import logging
import os
import sys

import click

logger = logging.getLogger(__name__)


def _read_offset_from_csv(csv_path: str, run_id: str) -> float:
    """Read the theta offset for *run_id* from a theta-offset CSV file.

    Looks for a row whose ``nexus`` column contains *run_id*.
    """
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if run_id in row["nexus"]:
                return float(row["offset"])
    raise click.ClickException(
        f"Run {run_id} not found in offset CSV: {csv_path}"
    )


def _read_partial_metadata(path: str) -> dict | None:
    """Return the ``# Meta:{...}`` JSON dict from a partial file's header.

    The reduction writes one ``# Meta: {json}`` line in the comment block at
    the top of every ``*_partial.txt`` file. The ``sequence_id`` field there
    is the authoritative first-run-of-set — Mantid's ``workflow.reduce()``
    return value can disagree when a run joins an existing sequence.
    """
    prefix = "# Meta:"
    try:
        with open(path) as f:
            for line in f:
                if not line.startswith("#"):
                    return None
                if line.startswith(prefix):
                    return json.loads(line[len(prefix):].strip())
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return None


@click.command()
@click.option(
    "--event-file", default=None, type=click.Path(exists=True),
    help="Path to neutron event data file (HDF5/NeXus). Required.",
)
@click.option(
    "--template", default=None, type=click.Path(exists=True),
    help="Path to reduction template file (.xml). Required.",
)
@click.option(
    "--output-dir", default=None, type=click.Path(file_okay=False),
    help="Directory for output files. Defaults to './reduced_data'.",
)
@click.option(
    "--theta-offset", default=None, type=float,
    help="Theta offset to apply during reduction (mutually exclusive with --offset-csv).",
)
@click.option(
    "--offset-csv", default=None, type=click.Path(exists=True),
    help="CSV file produced by theta-offset batch (requires --offset-run).",
)
@click.option(
    "--offset-run", default=None, type=str,
    help="Run ID to look up in the offset CSV (e.g. '226642').",
)
@click.option(
    "--json", "json_file", default=None, type=click.Path(dir_okay=False),
    help="Write a JSON summary (e.g. results.json) with paths to the partial "
         "and combined output files.",
)
@click.option(
    "--result-out", "result_out", default=None, type=click.Path(dir_okay=False),
    help="Write a neutral ndip-tool-result/1 manifest (params/artifacts/info) "
         "describing what was reduced. Schema-agnostic.",
)
@click.option(
    "-v", "--verbose", is_flag=True,
    help="Enable debug-level logging.",
)
def main(
    event_file: str | None,
    template: str | None,
    output_dir: str | None,
    theta_offset: float | None,
    offset_csv: str | None,
    offset_run: str | None,
    json_file: str | None,
    result_out: str | None,
    verbose: bool,
) -> None:
    """Reduce neutron events using a reduction template.

    Provide theta offset as either a literal value (--theta-offset) or
    looked up from a CSV file (--offset-csv + --offset-run).
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if output_dir is None:
        output_dir = "./reduced_data"

    if event_file is None:
        raise click.UsageError("--event-file is required.")
    if template is None:
        raise click.UsageError("--template is required.")
    if not os.path.isfile(event_file):
        raise click.UsageError(f"--event-file does not exist: {event_file}")
    if not os.path.isfile(template):
        raise click.UsageError(f"--template does not exist: {template}")

    # Resolve theta offset
    if offset_csv is not None:
        if theta_offset is not None:
            raise click.UsageError(
                "--theta-offset and --offset-csv are mutually exclusive."
            )
        if offset_run is None:
            raise click.UsageError(
                "--offset-csv requires --offset-run."
            )
        theta_offset = _read_offset_from_csv(offset_csv, offset_run)
        logger.info("Offset from CSV: run %s → %+.4f°", offset_run, theta_offset)
    elif theta_offset is None:
        theta_offset = 0.0

    from . import MantidNotAvailableError
    try:
        from . import require_mantid
    except MantidNotAvailableError as exc:
        raise click.ClickException(str(exc)) from exc

    require_mantid()

    import mantid
    import mantid.simpleapi as api

    # lr_reduction imports plot_publisher at module level (output.py and
    # web_report.py), but we don't need it.  Provide a stub that accepts
    # any attribute access so all ``from plot_publisher import X`` succeed.
    import types
    if "plot_publisher" not in sys.modules:
        _stub = types.ModuleType("plot_publisher")
        _stub.__getattr__ = lambda name: lambda *a, **kw: None  # type: ignore[attr-defined]
        sys.modules["plot_publisher"] = _stub

    from lr_reduction import workflow

    mantid.kernel.config.setLogLevel(3)

    # Tell Mantid where to find data files by run number.  In production
    # the facility's data catalog handles this; here we point at the
    # directory containing the event files.
    data_dir = os.path.dirname(os.path.abspath(event_file))
    mantid.config.appendDataSearchDir(data_dir)
    mantid.config["default.facility"] = "SNS"
    mantid.config["default.instrument"] = "REF_L"

    logger.info("Loading event data: %s", event_file)
    ws = api.LoadEventNexus(event_file)
    logger.info("Workspace: %d events", ws.getNumberEvents())

    os.makedirs(output_dir, exist_ok=True)

    logger.info("Reducing with template: %s", template)
    first_run_of_set = workflow.reduce(
        ws, template, output_dir,
        average_overlap=False,
        theta_offset=theta_offset,
        q_summing=False,
        bck_in_q=False,
    )

    # Locate the partial file for the run we just reduced. Files are named
    # REFL_{first_run}_{id}_{run_number}_partial.txt; we match by the run
    # number alone because ``workflow.reduce()``'s return value isn't a
    # reliable predictor of the {first_run} prefix Mantid actually writes
    # (it disagrees when a run joins an existing sequence).
    run_number = "".join(c for c in os.path.basename(event_file).split(".")[0] if c.isdigit())
    partial_matches = glob.glob(
        os.path.join(output_dir, f"REFL_*_*_{run_number}_partial.txt")
    )
    if partial_matches:
        partial_file = os.path.abspath(max(partial_matches, key=os.path.getmtime))
    else:
        partial_file = None
        logger.warning(
            "Partial file not found in %s for run %s (searched REFL_*_*_%s_partial.txt)",
            output_dir, run_number, run_number,
        )

    # Take the authoritative ``first_run_of_set`` from the partial file's
    # ``# Meta:`` block (the ``sequence_id`` field). Never glob for the
    # combined file — a stray REFL_*_combined_data_auto.txt from a previous
    # unrelated reduction would silently get picked up.
    sequence_id = None
    if partial_file:
        meta = _read_partial_metadata(partial_file)
        if meta and meta.get("sequence_id") is not None:
            sequence_id = meta["sequence_id"]
    if sequence_id is None:
        # Fall back to what Mantid said — better than nothing, but warn.
        sequence_id = first_run_of_set
        if partial_file:
            logger.warning(
                "Could not read sequence_id from %s; falling back to "
                "workflow.reduce() return value %s",
                partial_file, first_run_of_set,
            )

    combined_file = os.path.join(
        output_dir, f"REFL_{sequence_id}_combined_data_auto.txt"
    )
    combined_file_abs = (
        os.path.abspath(combined_file) if os.path.exists(combined_file) else None
    )
    if combined_file_abs is None:
        logger.warning("Combined file not found: %s", combined_file)

    if json_file is not None:
        result = {
            "partial_file": partial_file,
            "combined_file": combined_file_abs,
        }
        with open(json_file, "w") as f:
            json.dump(result, f, indent=2)
        logger.info("JSON summary written: %s", os.path.abspath(json_file))

    if result_out is not None:
        from ..result_manifest import write_manifest

        write_manifest(
            result_out,
            "simple-reduction",
            "ok",
            params={
                "template_file": os.path.abspath(template),
                "theta_offset": theta_offset,
            },
            artifacts={
                "partial_file": partial_file,
                "combined_file": combined_file_abs,
            },
            info={"first_run_of_set": sequence_id},
        )
        logger.info("Result manifest written: %s", os.path.abspath(result_out))

    logger.info("Reduction complete - output dir: %s", os.path.abspath(output_dir))


if __name__ == "__main__":
    main()
