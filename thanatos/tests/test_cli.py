"""Tests for thanatos.cli.

Black-box (importing main, capturing stdout/stderr); does not boot the MCP
server. Covers:

- argparse rejects missing required args (exit 2).
- `run-scenario` happy path returns one JSON object on stdout (M0 stub →
  pass=False with the canonical scaffold-only failure_hint).
- `run-all` returns a JSON array with one entry per parsed scenario.
- `recall` returns an empty JSON array (M0 stub).
- runner exception → exit 3 + stderr carries the exception class name.
- `python -m thanatos` (zero argv after dispatcher) routes to the MCP server
  entrypoint (verified by patching).
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from thanatos.cli import main as cli_main


def _write_skill(tmp: Path, driver: str = "playwright") -> Path:
    p = tmp / "skill.yaml"
    p.write_text(
        textwrap.dedent(
            f"""
            name: cli-test
            driver: {driver}
            entry: $ENDPOINT
            """
        ).lstrip("\n"),
        encoding="utf-8",
    )
    return p


def _write_spec(tmp: Path) -> Path:
    p = tmp / "spec.md"
    p.write_text(
        textwrap.dedent(
            """\
            ## ADDED Requirements

            ### Requirement: dummy

            Dummy SHALL exist.

            #### Scenario: S1 example

            - **GIVEN** something
            - **WHEN** it happens
            - **THEN** it works

            #### Scenario: S2 second

            - **GIVEN** another
            - **WHEN** it triggers
            - **THEN** it returns
            """
        ),
        encoding="utf-8",
    )
    return p


# ─── argparse rejects missing required args ──────────────────────────────────


def test_run_scenario_missing_args_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_main(["run-scenario", "--skill", "x", "--spec", "y"])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "the following arguments are required" in captured.err


def test_no_subcommand_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_main([])
    # argparse with required=True subparser emits exit 2 on missing subcmd
    assert excinfo.value.code == 2


# ─── run-scenario happy path ────────────────────────────────────────────────


def test_run_scenario_happy_path_emits_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    skill = _write_skill(tmp_path)
    spec = _write_spec(tmp_path)
    rc = cli_main(
        [
            "run-scenario",
            "--skill", str(skill),
            "--spec", str(spec),
            "--scenario-id", "S1",
            "--endpoint", "http://localhost:8080",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["scenario_id"] == "S1"
    # M0: drivers stub → pass=False + canonical failure_hint
    assert payload["pass"] is False
    assert payload["failure_hint"] == "M0: thanatos scaffold only, drivers not implemented"
    assert payload["kb_updates"] == []


def test_run_scenario_unknown_id_returns_failure_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    skill = _write_skill(tmp_path)
    spec = _write_spec(tmp_path)
    rc = cli_main(
        [
            "run-scenario",
            "--skill", str(skill),
            "--spec", str(spec),
            "--scenario-id", "NOPE",
            "--endpoint", "http://x",
        ]
    )
    assert rc == 0  # dispatch succeeded even though scenario was not found
    payload = json.loads(capsys.readouterr().out)
    assert payload["scenario_id"] == "NOPE"
    assert payload["pass"] is False
    assert "not found" in (payload["failure_hint"] or "").lower()


# ─── run-all happy path ──────────────────────────────────────────────────────


def test_run_all_emits_json_array(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    skill = _write_skill(tmp_path)
    spec = _write_spec(tmp_path)
    rc = cli_main(
        [
            "run-all",
            "--skill", str(skill),
            "--spec", str(spec),
            "--endpoint", "http://x",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert {p["scenario_id"] for p in payload} == {"S1", "S2"}
    assert all(p["pass"] is False for p in payload)


# ─── recall stub ─────────────────────────────────────────────────────────────


def test_recall_returns_empty_array(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    skill = _write_skill(tmp_path)
    rc = cli_main(["recall", "--skill", str(skill), "--intent", "find login button"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == []


# ─── runner exception → exit 3 ───────────────────────────────────────────────


def test_runner_exception_exits_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # skill.yaml missing entirely → load_skill raises SkillLoadError
    rc = cli_main(
        [
            "run-scenario",
            "--skill", str(tmp_path / "does-not-exist.yaml"),
            "--spec", str(tmp_path / "spec.md"),
            "--scenario-id", "S1",
            "--endpoint", "http://x",
        ]
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "thanatos run-scenario:" in err
    # exception class name should appear (SkillLoadError or similar)
    assert "Error" in err or "Exception" in err


# ─── __main__ dispatcher: zero args → server, N args → cli ──────────────────


def test_main_module_dispatcher_routes_to_cli_with_args(monkeypatch, tmp_path: Path):
    """`python -m thanatos run-scenario ...` must hit cli.main, not server.main."""
    from thanatos import __main__ as dispatcher

    called = {"server": False, "cli_argv": None}

    def _fake_server() -> None:
        called["server"] = True

    def _fake_cli(argv):
        called["cli_argv"] = list(argv)
        return 0

    monkeypatch.setattr("thanatos.server.main", _fake_server)
    monkeypatch.setattr("thanatos.cli.main", _fake_cli)
    monkeypatch.setattr("sys.argv", ["python -m thanatos", "run-scenario", "--skill", "x"])

    rc = dispatcher._route()
    assert rc == 0
    assert called["server"] is False
    assert called["cli_argv"] == ["run-scenario", "--skill", "x"]


def test_main_module_dispatcher_routes_to_server_without_args(monkeypatch):
    """`python -m thanatos` (zero extra argv) must boot the MCP stdio server."""
    from thanatos import __main__ as dispatcher

    called = {"server": False}

    def _fake_server() -> None:
        called["server"] = True

    monkeypatch.setattr("thanatos.server.main", _fake_server)
    monkeypatch.setattr("sys.argv", ["python -m thanatos"])

    rc = dispatcher._route()
    assert rc == 0
    assert called["server"] is True
