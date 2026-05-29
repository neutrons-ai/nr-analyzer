"""
Check LLM availability for the analyzer.

The analyzer itself does not configure an LLM: all LLM calls are delegated to
AuRE (via `aure evaluate` subprocesses and the optional ``aure.llm`` module
used by :mod:`analyzer_tools.analysis.partial_data_assessor`).

This command verifies the full chain end-to-end:

1. The ``aure`` CLI is installed and on ``PATH`` (for ``assess-result`` and
   ``analyze-sample``).
2. The :mod:`aure.llm` module is importable from Python (for the optional
   partial-data commentary).
3. ``aure check-llm`` reports a working LLM connection.

Use this before starting an interactive session to catch missing credentials
or a broken LLM endpoint early.

Example::

    check-llm
    check-llm --json
    check-llm --no-test
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

import click
from dotenv import dotenv_values


def check_aure_cli() -> Tuple[bool, str]:
    """Return (ok, message) for whether the ``aure`` CLI is available."""
    path = shutil.which("aure")
    if path is None:
        return False, "aure executable not found on PATH"
    return True, path


def check_aure_python() -> Tuple[bool, str]:
    """Return (ok, message) for whether :mod:`aure.llm` is importable."""
    try:
        from aure.llm import llm_available  # type: ignore  # noqa: F401
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"aure.llm not importable: {exc}"
    return True, "aure.llm importable"


def run_aure_check_llm(
    *, test_connection: bool = True, timeout: int = 60
) -> Tuple[bool, Dict[str, Any]]:
    """Invoke ``aure check-llm --json`` and return (ok, parsed_payload).

    Returns ``(False, {"error": ...})`` on any failure including timeouts or
    non-JSON output.
    """
    aure = shutil.which("aure")
    if aure is None:
        return False, {"error": "aure executable not found"}

    cmd: List[str] = [aure, "check-llm", "--json"]
    if not test_connection:
        cmd.append("--no-test")

    # Build the subprocess environment using the analyzer's full .env
    # cascade (project .env walking up from CWD, then ~/.config/analyzer/.env)
    # so that aure picks up LLM_BASE_URL, LLM_MODEL, LLM_API_KEY, etc. even
    # when they are not exported in the shell. Process environment wins
    # (matches override=False in config_utils._load_env).
    from analyzer_tools.config_utils import _candidate_env_paths

    env = dict(os.environ)
    for path in _candidate_env_paths(None):
        for key, value in dotenv_values(str(path)).items():
            if value is not None and key not in env:
                env[key] = value

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, {"error": f"aure check-llm timed out after {timeout}s"}

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return False, {
            "error": "aure check-llm produced no output",
            "stderr": (completed.stderr or "").strip(),
            "returncode": completed.returncode,
        }
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # aure may print extra text before the JSON; try to extract the last
        # JSON object in the stream.
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(stdout[start : end + 1])
            except json.JSONDecodeError:
                return False, {
                    "error": "aure check-llm output was not valid JSON",
                    "stdout": stdout,
                }
        else:
            return False, {
                "error": "aure check-llm output was not valid JSON",
                "stdout": stdout,
            }

    ok = completed.returncode == 0 and bool(payload.get("ok", payload.get("available", False)))
    return ok, payload


def collect_status(*, test_connection: bool = True) -> Dict[str, Any]:
    """Gather a structured report of analyzer ↔ AuRE ↔ LLM health."""
    cli_ok, cli_msg = check_aure_cli()
    py_ok, py_msg = check_aure_python()

    aure_payload: Optional[Dict[str, Any]] = None
    aure_ok = False
    if cli_ok:
        aure_ok, aure_payload = run_aure_check_llm(test_connection=test_connection)

    overall = cli_ok and py_ok and (aure_ok or not test_connection)
    return {
        "ok": overall,
        "test_connection": test_connection,
        "checks": {
            "aure_cli": {"ok": cli_ok, "detail": cli_msg},
            "aure_python": {"ok": py_ok, "detail": py_msg},
            "aure_check_llm": {
                "ok": aure_ok,
                "payload": aure_payload,
            },
        },
    }


def _render_human(status: Dict[str, Any]) -> None:
    """Pretty-print status to stdout."""
    click.echo()
    click.echo(click.style("  Analyzer LLM Check", fg="blue", bold=True))
    click.echo(click.style("  " + "─" * 40, fg="blue"))
    click.echo()

    checks = status["checks"]

    cli = checks["aure_cli"]
    marker = click.style("✓", fg="green") if cli["ok"] else click.style("✗", fg="red")
    click.echo(f"    {marker} aure CLI           {cli['detail']}")

    py = checks["aure_python"]
    marker = click.style("✓", fg="green") if py["ok"] else click.style("✗", fg="red")
    click.echo(f"    {marker} aure.llm (Python)  {py['detail']}")

    aure = checks["aure_check_llm"]
    if aure["ok"]:
        marker = click.style("✓", fg="green")
        detail = "LLM reachable"
    else:
        marker = click.style("✗", fg="red")
        payload = aure.get("payload") or {}
        detail = payload.get("message") or payload.get("error") or "unavailable"
    click.echo(f"    {marker} aure check-llm     {detail}")

    if aure.get("payload"):
        payload = aure["payload"]
        provider = payload.get("provider")
        model = payload.get("model")
        if provider or model:
            click.echo()
            if provider:
                click.echo(f"      Provider: {provider}")
            if model:
                click.echo(f"      Model:    {model}")

    click.echo()
    if status["ok"]:
        click.echo(click.style("  ✓ Ready", fg="green", bold=True))
    else:
        click.echo(click.style("  ✗ Not ready — fix the errors above", fg="red", bold=True))
        click.echo("    Hint: `pip install -e /path/to/aure` and run `aure check-llm`.")
    click.echo()


@click.command()
@click.option("--json", "output_json", is_flag=True, help="Output as JSON")
@click.option("--no-test", is_flag=True, help="Skip the live LLM connection test")
def main(output_json: bool, no_test: bool) -> None:
    """Check analyzer LLM availability (via AuRE).

    Verifies that the ``aure`` CLI is installed, that ``aure.llm`` is
    importable, and (unless ``--no-test``) that ``aure check-llm`` can
    successfully reach the configured LLM endpoint.

    Exit codes:

    \b
      0  LLM is reachable and all components are ready
      1  A required component is missing or the LLM is unreachable
    """
    status = collect_status(test_connection=not no_test)
    if output_json:
        click.echo(json.dumps(status, indent=2))
    else:
        _render_human(status)
    sys.exit(0 if status["ok"] else 1)


if __name__ == "__main__":
    main()
