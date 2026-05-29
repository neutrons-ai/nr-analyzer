"""
Manifest-based batch runner for analyzer tools.

Reads a YAML manifest file and runs each job by dispatching to the
corresponding CLI entry point.  Tools themselves are never modified —
this module is pure orchestration.

Usage::

    analyzer-batch manifest.yaml
    analyzer-batch manifest.yaml --dry-run
    analyzer-batch manifest.yaml --jobs copper_on_silicon,second_sample
"""

import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

import click
import yaml


def _resolve_path(path: str, base_dir: str) -> str:
    """Make *path* absolute, resolving relative paths against *base_dir*."""
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(base_dir, path))


def _merge_defaults(job: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *job* with missing keys filled from *defaults*."""
    merged = dict(defaults)
    merged.update(job)
    return merged


# ── Tool → CLI command mapping ────────────────────────────────────────────
# Keys are the canonical tool names used in manifest files.
# Values are the Python module paths (each module has a click main()).
TOOL_COMMANDS = {
    "run-fit":                "analyzer_tools.analysis.run_fit",
    "assess-partial":         "analyzer_tools.analysis.partial_data_assessor",
    "assess-result":          "analyzer_tools.analysis.result_assessor",
    "create-model":           "analyzer_tools.analysis.create_model",
    "theta-offset":           "analyzer_tools.analysis.theta_offset",
    "simple-reduction":       "analyzer_tools.reduction.reduction",
    "assemble-partials":      "analyzer_tools.analysis.assemble",
    "analyze-sample":         "analyzer_tools.pipeline",
    "check-llm":              "analyzer_tools.analysis.check_llm",
}


def _apply_data_location(args: List[str], data_location: str) -> List[str]:
    """Prepend *data_location* to args that look like bare data filenames.

    A bare data filename is any arg that is not a flag (``--…``), not
    already an absolute path, and has a data-file extension
    (``.h5``, ``.nxs.h5``, ``.dat``, ``.xml``).
    """
    data_exts = (".h5", ".nxs.h5", ".hdf5", ".nxs", ".dat", ".xml")
    resolved = []
    for arg in args:
        if (
            not arg.startswith("--")
            and not os.path.isabs(arg)
            and any(arg.endswith(ext) for ext in data_exts)
        ):
            resolved.append(os.path.join(data_location, arg))
        else:
            resolved.append(arg)
    return resolved


def _build_command(tool: str, args: List[str]) -> List[str]:
    """Return the full argv list for a job.

    Uses ``sys.executable -m <module>`` so child processes always use
    the same Python interpreter as the batch runner (important when
    running inside pixi or conda environments).
    """
    module = TOOL_COMMANDS.get(tool)
    if module is None:
        raise click.ClickException(
            f"Unknown tool '{tool}'. "
            f"Available: {', '.join(sorted(TOOL_COMMANDS))}"
        )
    return [sys.executable, "-m", module, *args]


def _expand_for_each(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Expand a job containing a ``for_each`` key into a list of jobs.

    ``for_each`` is a mapping ``{flag: [value1, value2, ...]}``.  The job
    is replicated once per value, with ``[flag, value]`` appended to its
    ``args``.  If the job has no explicit ``name``, one is auto-generated
    from the tool name and the value's basename.

    A shorthand is also accepted: ``files: [a.h5, b.h5]`` is treated as
    ``for_each: {--event-file: [a.h5, b.h5]}``.

    Jobs without ``for_each``/``files`` are returned unchanged in a
    single-element list.
    """
    for_each = job.get("for_each")
    if for_each is None and "files" in job:
        for_each = {"--event-file": job["files"]}

    if for_each is None:
        return [job]

    if not isinstance(for_each, dict) or len(for_each) != 1:
        raise click.ClickException(
            "'for_each' must be a mapping with exactly one flag, "
            "e.g. {--event-file: [a.h5, b.h5]}"
        )
    [(flag, values)] = for_each.items()
    if not isinstance(values, list) or not values:
        raise click.ClickException(
            f"'for_each.{flag}' must be a non-empty list of values."
        )

    base = {k: v for k, v in job.items() if k not in ("for_each", "files")}
    base_args = list(base.get("args", []))
    base_name = base.get("name")
    tool = base.get("tool", "job")

    expanded: List[Dict[str, Any]] = []
    for value in values:
        new_job = dict(base)
        new_job["args"] = base_args + [flag, str(value)]
        if base_name:
            stem = os.path.splitext(os.path.basename(str(value)))[0]
            stem = stem.replace(".nxs", "")
            new_job["name"] = f"{base_name}_{stem}"
        else:
            stem = os.path.splitext(os.path.basename(str(value)))[0]
            stem = stem.replace(".nxs", "")
            new_job["name"] = f"{tool}_{stem}"
        expanded.append(new_job)
    return expanded


def load_manifest(path: str) -> Dict[str, Any]:
    """Parse a YAML manifest and return its contents.

    Any jobs containing ``for_each`` (or the ``files`` shorthand) are
    expanded in place into one job per value.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise click.ClickException("Manifest must be a YAML mapping at the top level.")
    if "jobs" not in data or not isinstance(data["jobs"], list):
        raise click.ClickException("Manifest must contain a 'jobs' list.")

    expanded_jobs: List[Dict[str, Any]] = []
    for job in data["jobs"]:
        if not isinstance(job, dict):
            raise click.ClickException("Each job must be a YAML mapping.")
        expanded_jobs.extend(_expand_for_each(job))
    data["jobs"] = expanded_jobs
    return data


def run_manifest(
    manifest_path: str,
    dry_run: bool = False,
    job_filter: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Execute all jobs in a manifest file.

    Parameters
    ----------
    manifest_path : str
        Path to the YAML manifest.
    dry_run : bool
        If True, print commands without executing them.
    job_filter : list of str, optional
        If provided, only run jobs whose ``name`` is in this list.

    Returns
    -------
    list of dict
        One entry per job with keys ``name``, ``tool``, ``command``,
        ``returncode`` (None when dry-run).
    """
    manifest = load_manifest(manifest_path)
    base_dir = os.path.dirname(os.path.abspath(manifest_path))
    defaults = manifest.get("defaults", {})
    results = []

    output_root = defaults.pop("output_root", None)

    # data_location: directory prepended to bare-filename args
    data_location = manifest.get("data_location")
    if data_location is not None:
        data_location = os.path.expanduser(data_location)

    # theta_offset: global offset injected as --theta-offset if not already set
    global_theta_offset = manifest.get("theta_offset")

    # offset_csv: global offset CSV injected as --offset-csv/--offset-run
    global_offset_csv = manifest.get("offset_csv")
    if global_offset_csv is not None:
        global_offset_csv = os.path.expanduser(global_offset_csv)
        if not os.path.isabs(global_offset_csv) and data_location is not None:
            global_offset_csv = os.path.join(data_location, global_offset_csv)

    # output_dir: global output directory injected as --output-dir
    global_output_dir = manifest.get("output_dir")
    if global_output_dir is not None:
        global_output_dir = os.path.expanduser(global_output_dir)

    for job_spec in manifest["jobs"]:
        job = _merge_defaults(job_spec, defaults)
        name = job.get("name", "unnamed")

        if job_filter and name not in job_filter:
            continue

        tool = job.get("tool")
        if tool is None:
            click.echo(f"  SKIP  {name}: no 'tool' specified", err=True)
            continue

        # Build the raw args list from the manifest
        raw_args: List[str] = [str(a) for a in job.get("args", [])]

        # Inject global theta_offset if no offset option is already present
        if global_theta_offset is not None and not any(
            a in ("--theta-offset", "--offset-csv") for a in raw_args
        ):
            raw_args.extend(["--theta-offset", str(global_theta_offset)])

        # Inject global offset_csv if no offset option is already present
        if global_offset_csv is not None and not any(
            a in ("--theta-offset", "--offset-csv") for a in raw_args
        ):
            raw_args.extend(["--offset-csv", global_offset_csv])
            # Auto-derive --offset-run from the --event-file argument
            ef_idx = next(
                (i for i, a in enumerate(raw_args) if a == "--event-file"),
                None,
            )
            if ef_idx is not None and ef_idx + 1 < len(raw_args):
                event_basename = os.path.basename(raw_args[ef_idx + 1])
                for ext in (".nxs.h5", ".h5", ".hdf5", ".nxs"):
                    if event_basename.endswith(ext):
                        event_basename = event_basename[: -len(ext)]
                        break
                run_id = event_basename.split("_")[-1]
                raw_args.extend(["--offset-run", run_id])

        # Inject global output_dir if --output-dir is not already present
        if global_output_dir is not None and "--output-dir" not in raw_args:
            raw_args.extend(["--output-dir", global_output_dir])

        # Prepend data_location to bare filenames (not flags or paths)
        if data_location is not None:
            raw_args = _apply_data_location(raw_args, data_location)

        # Resolve output_root per-job if set
        job_output = job.get("output_root", output_root)
        if job_output is not None:
            job_output = _resolve_path(job_output, base_dir)
            job_dir = os.path.join(job_output, name)
            os.makedirs(job_dir, exist_ok=True)

        cmd = _build_command(tool, raw_args)
        cmd_str = " ".join(cmd)

        if dry_run:
            click.echo(f"  [DRY] {name}: {cmd_str}")
            results.append({"name": name, "tool": tool,
                            "command": cmd_str, "returncode": None})
            continue

        click.echo(f"  RUN   {name}: {cmd_str}", err=True)
        proc = subprocess.run(cmd, cwd=base_dir)
        status = "OK" if proc.returncode == 0 else f"FAIL({proc.returncode})"
        click.echo(f"  {status}  {name}", err=True)
        results.append({"name": name, "tool": tool,
                        "command": cmd_str, "returncode": proc.returncode})

    return results


# ── CLI ───────────────────────────────────────────────────────────────────

@click.command()
@click.argument("manifest", type=click.Path(exists=True))
@click.option("--dry-run", is_flag=True,
              help="Print commands without executing them.")
@click.option("--jobs", "job_names", type=str, default=None,
              help="Comma-separated list of job names to run (default: all).")
def main(manifest: str, dry_run: bool, job_names: Optional[str]):
    """Run analysis jobs defined in a YAML manifest file.

    \b
    Examples:
      analyzer-batch manifest.yaml
      analyzer-batch manifest.yaml --dry-run
      analyzer-batch manifest.yaml --jobs copper_on_silicon,second_sample
    """
    job_filter = [j.strip() for j in job_names.split(",")] if job_names else None

    click.echo(f"Loading manifest: {manifest}")
    results = run_manifest(manifest, dry_run=dry_run, job_filter=job_filter)

    # Summary
    click.echo()
    total = len(results)
    passed = sum(1 for r in results if r["returncode"] == 0)
    failed = sum(1 for r in results if r["returncode"] is not None and r["returncode"] != 0)
    skipped = sum(1 for r in results if r["returncode"] is None and not dry_run)

    if dry_run:
        click.echo(f"Dry run: {total} job(s) would be executed.")
    else:
        click.echo(f"Batch complete: {passed} passed, {failed} failed"
                    f"{f', {skipped} skipped' if skipped else ''}"
                    f" ({total} total)")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
