"""Tests for the check-llm CLI."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from analyzer_tools.analysis import check_llm as cl


def test_check_aure_cli_missing(monkeypatch) -> None:
    monkeypatch.setattr(cl.shutil, "which", lambda _: None)
    ok, msg = cl.check_aure_cli()
    assert not ok
    assert "not found" in msg


def test_check_aure_cli_present(monkeypatch) -> None:
    monkeypatch.setattr(cl.shutil, "which", lambda _: "/opt/bin/aure")
    ok, msg = cl.check_aure_cli()
    assert ok
    assert msg == "/opt/bin/aure"


def test_run_aure_check_llm_no_executable(monkeypatch) -> None:
    monkeypatch.setattr(cl.shutil, "which", lambda _: None)
    ok, payload = cl.run_aure_check_llm()
    assert not ok
    assert "error" in payload


def test_run_aure_check_llm_success(monkeypatch) -> None:
    monkeypatch.setattr(cl.shutil, "which", lambda _: "/opt/bin/aure")

    class _Result:
        returncode = 0
        stdout = json.dumps({"ok": True, "provider": "openai", "model": "gpt-4"})
        stderr = ""

    monkeypatch.setattr(cl.subprocess, "run", lambda *a, **k: _Result())
    ok, payload = cl.run_aure_check_llm()
    assert ok
    assert payload["provider"] == "openai"


def test_run_aure_check_llm_non_json(monkeypatch) -> None:
    monkeypatch.setattr(cl.shutil, "which", lambda _: "/opt/bin/aure")

    class _Result:
        returncode = 0
        stdout = "garbage output"
        stderr = ""

    monkeypatch.setattr(cl.subprocess, "run", lambda *a, **k: _Result())
    ok, payload = cl.run_aure_check_llm()
    assert not ok
    assert "error" in payload


def test_run_aure_check_llm_timeout(monkeypatch) -> None:
    monkeypatch.setattr(cl.shutil, "which", lambda _: "/opt/bin/aure")

    def _raise(*a, **k):
        raise cl.subprocess.TimeoutExpired(cmd=["aure"], timeout=1)

    monkeypatch.setattr(cl.subprocess, "run", _raise)
    ok, payload = cl.run_aure_check_llm(timeout=1)
    assert not ok
    assert "timed out" in payload["error"]


def test_collect_status_all_ok(monkeypatch) -> None:
    monkeypatch.setattr(cl, "check_aure_cli", lambda: (True, "/opt/bin/aure"))
    monkeypatch.setattr(cl, "check_aure_python", lambda: (True, "aure.llm importable"))
    monkeypatch.setattr(
        cl, "run_aure_check_llm", lambda **k: (True, {"ok": True, "provider": "openai"})
    )
    status = cl.collect_status()
    assert status["ok"] is True
    assert status["checks"]["aure_cli"]["ok"]
    assert status["checks"]["aure_python"]["ok"]
    assert status["checks"]["aure_check_llm"]["ok"]


def test_collect_status_cli_missing(monkeypatch) -> None:
    monkeypatch.setattr(cl, "check_aure_cli", lambda: (False, "aure executable not found"))
    monkeypatch.setattr(cl, "check_aure_python", lambda: (False, "no module"))
    status = cl.collect_status()
    assert status["ok"] is False
    # Should not have tried aure check-llm when CLI is missing
    assert status["checks"]["aure_check_llm"]["ok"] is False


def test_collect_status_no_test(monkeypatch) -> None:
    """--no-test makes the tool report ok when static checks pass."""
    monkeypatch.setattr(cl, "check_aure_cli", lambda: (True, "/opt/bin/aure"))
    monkeypatch.setattr(cl, "check_aure_python", lambda: (True, "ok"))
    monkeypatch.setattr(cl, "run_aure_check_llm", lambda **k: (False, {}))
    status = cl.collect_status(test_connection=False)
    assert status["ok"] is True


def test_cli_json(monkeypatch) -> None:
    monkeypatch.setattr(
        cl,
        "collect_status",
        lambda test_connection: {
            "ok": True,
            "test_connection": test_connection,
            "checks": {
                "aure_cli": {"ok": True, "detail": "/opt/bin/aure"},
                "aure_python": {"ok": True, "detail": "ok"},
                "aure_check_llm": {"ok": True, "payload": {"provider": "openai"}},
            },
        },
    )
    runner = CliRunner()
    result = runner.invoke(cl.main, ["--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True


def test_cli_human_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        cl,
        "collect_status",
        lambda test_connection: {
            "ok": False,
            "test_connection": test_connection,
            "checks": {
                "aure_cli": {"ok": False, "detail": "aure executable not found"},
                "aure_python": {"ok": False, "detail": "no module"},
                "aure_check_llm": {"ok": False, "payload": None},
            },
        },
    )
    runner = CliRunner()
    result = runner.invoke(cl.main, [])
    assert result.exit_code == 1
    assert "Not ready" in result.output
