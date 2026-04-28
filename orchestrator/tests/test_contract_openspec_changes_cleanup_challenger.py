"""Challenger contract tests for REQ-openspec-changes-cleanup-1777343379.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-openspec-changes-cleanup-1777343379/specs/openspec-orphan-cleanup/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered (12 total):
  OSC-S1  escalate calls exec_in_runner once per repo with rm+commit command
  OSC-S2  exec_in_runner failure does not block escalate
  OSC-S3  no involved_repos → exec_in_runner not called for cleanup
  OSC-S4  k8s_runner.get_controller() failure → escalate still completes (fail-open)
  SUPR-S1 vN redispatch triggers supersede: exec_in_runner called with _superseded + req_id
  SUPR-S2 non-vN req_id → base_slug == req_id, no stale-dir mv can occur
  SUPR-S3 supersede exec failure does not block BKD dispatch
  COP-S1  _classify: done state → action=delete
  COP-S2  _classify: escalated state → action=delete
  COP-S3  _classify: in-flight state → action=keep
  COP-S4  _classify: PG no record → action=delete, state=not_found
  COP-S5  _collect_req_dirs: archive and _superseded excluded from scan
"""
from __future__ import annotations

import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ─── Paths ────────────────────────────────────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"

# ─── Minimal ExecResult stub ─────────────────────────────────────────────────


class _ExecResult:
    """Minimal RunnerController.exec_in_runner return-value stub."""

    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_sec = 0.1


# ─── Shared helpers ───────────────────────────────────────────────────────────


def _make_body(event: str = "verify.escalate", issue_id: str = "rvw-1"):
    return type("B", (), {
        "issueId": issue_id,
        "projectId": "proj-test",
        "event": event,
        "title": "test",
        "tags": [],
        "issueNumber": None,
    })()


class _FakePool:
    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        pass


def _fake_bkd_ctx():
    bkd = AsyncMock()
    bkd.create_issue = AsyncMock(return_value=MagicMock(id="new-1"))
    bkd.update_issue = AsyncMock(return_value=MagicMock(id="new-1"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    bkd.list_issues = AsyncMock(return_value=[])
    bkd.get_issue = AsyncMock(return_value=MagicMock(id="x", tags=["foo"]))
    bkd.merge_tags_and_update = AsyncMock(return_value=MagicMock(id="x"))

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield bkd

    return _ctx


class _FakeReqRow:
    state = type("S", (), {"value": "executing"})()


# ─── OSC: escalate openspec cleanup ──────────────────────────────────────────

_REQ_ID = "REQ-openspec-changes-cleanup-1777343379"


def _patch_escalate_base(monkeypatch, controller, prs_merged: bool = False):
    """Patch external deps so the real escalate path runs cleanly."""
    from orchestrator import k8s_runner as krunner
    from orchestrator.store import req_state as rs

    monkeypatch.setattr("orchestrator.actions.escalate.BKDClient", _fake_bkd_ctx())
    monkeypatch.setattr("orchestrator.actions.escalate.db.get_pool", lambda: _FakePool())
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_FakeReqRow()))
    monkeypatch.setattr(rs, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(krunner, "get_controller", lambda: controller)
    # Prevent PR-merged shortcut from firing — it would short-circuit before cleanup
    monkeypatch.setattr(
        "orchestrator.actions.escalate._all_prs_merged_for_req",
        AsyncMock(return_value=prs_merged),
    )


async def test_OSC_S1_exec_in_runner_called_once_per_repo(monkeypatch):
    """OSC-S1: real escalate with 2 involved_repos calls exec_in_runner once per repo.

    Command MUST contain 'rm -rf openspec/changes/<req_id>/' AND 'git commit'.

    GIVEN a REQ with verifier-decision-escalate event and two involved_repos in ctx
    WHEN the escalate action runs (non-transient, retry_count=0)
    THEN exec_in_runner is called once per repo with rm+commit command
    """
    from orchestrator.actions import escalate as mod

    exec_calls: list[dict] = []

    class FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            pass

        async def exec_in_runner(self, req_id, command, **kwargs):
            exec_calls.append({"req_id": req_id, "command": command})
            return _ExecResult(exit_code=0)

    _patch_escalate_base(monkeypatch, FakeController())

    out = await mod.escalate(
        body=_make_body(event="verify.escalate"),
        req_id=_REQ_ID,
        tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/sisyphus", "phona/other-repo"],
        },
    )

    assert out.get("escalated") is True, (
        f"escalate must return escalated=True on real escalate path; got: {out}"
    )

    cleanup_calls = [
        c for c in exec_calls
        if f"openspec/changes/{_REQ_ID}" in c["command"]
    ]
    assert len(cleanup_calls) == 2, (
        f"exec_in_runner must be called once per involved_repo (2 repos) with cleanup command; "
        f"cleanup calls: {len(cleanup_calls)}; all exec_calls: {exec_calls!r}"
    )
    for call in cleanup_calls:
        cmd = call["command"]
        assert "rm -rf" in cmd, (
            f"cleanup command must contain 'rm -rf'; got: {cmd!r}"
        )
        assert "git commit" in cmd, (
            f"cleanup command must contain 'git commit'; got: {cmd!r}"
        )


async def test_OSC_S2_cleanup_failure_does_not_block_escalate(monkeypatch):
    """OSC-S2: exec_in_runner raising RuntimeError MUST NOT prevent escalate from completing.

    GIVEN exec_in_runner raises RuntimeError for every call
    WHEN the escalate action runs
    THEN escalate still returns {"escalated": True} without raising
    """
    from orchestrator.actions import escalate as mod

    class FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            pass

        async def exec_in_runner(self, req_id, command, **kwargs):
            raise RuntimeError("simulated exec failure for OSC-S2")

    _patch_escalate_base(monkeypatch, FakeController())

    out = await mod.escalate(
        body=_make_body(event="verify.escalate"),
        req_id=_REQ_ID,
        tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/sisyphus"],
        },
    )

    assert out.get("escalated") is True, (
        f"exec_in_runner failure must not block escalate; must return escalated=True; got: {out}"
    )


async def test_OSC_S3_no_involved_repos_skips_cleanup(monkeypatch):
    """OSC-S3: no involved_repos resolved → exec_in_runner never called for cleanup.

    GIVEN no involved_repos in ctx, tags, or default (all sources empty)
    WHEN the escalate action runs
    THEN exec_in_runner is never called with an openspec/changes cleanup command
    """
    from orchestrator.actions import escalate as mod

    exec_calls: list[str] = []

    class FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            pass

        async def exec_in_runner(self, req_id, command, **kwargs):
            exec_calls.append(command)
            return _ExecResult(exit_code=0)

    _patch_escalate_base(monkeypatch, FakeController())
    # Also ensure settings.default_involved_repos is empty
    monkeypatch.setattr(
        "orchestrator.actions.escalate.settings",
        MagicMock(
            bkd_base_url="http://bkd.test",
            bkd_token="tok",
            default_involved_repos=[],
            gh_incident_repo="",
        ),
    )

    out = await mod.escalate(
        body=_make_body(event="verify.escalate"),
        req_id=_REQ_ID,
        tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            # no involved_repos → no repos resolved
        },
    )

    assert out.get("escalated") is True, (
        f"escalate must complete even with no involved_repos; got: {out}"
    )
    cleanup_exec_calls = [c for c in exec_calls if "openspec/changes" in c]
    assert len(cleanup_exec_calls) == 0, (
        f"exec_in_runner must not be called for openspec cleanup when no involved_repos; "
        f"cleanup commands found: {cleanup_exec_calls!r}"
    )


async def test_OSC_S4_no_runner_controller_is_fail_open(monkeypatch):
    """OSC-S4: k8s_runner.get_controller() raising RuntimeError must not block escalate.

    GIVEN k8s_runner.get_controller() raises RuntimeError
    WHEN the escalate action runs
    THEN escalate still returns {"escalated": True} without raising
    """
    from orchestrator import k8s_runner as krunner
    from orchestrator.store import req_state as rs

    monkeypatch.setattr("orchestrator.actions.escalate.BKDClient", _fake_bkd_ctx())
    monkeypatch.setattr("orchestrator.actions.escalate.db.get_pool", lambda: _FakePool())
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_FakeReqRow()))
    monkeypatch.setattr(rs, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "orchestrator.actions.escalate._all_prs_merged_for_req",
        AsyncMock(return_value=False),
    )

    def _raise_no_controller():
        raise RuntimeError("no runner controller for OSC-S4")

    monkeypatch.setattr(krunner, "get_controller", _raise_no_controller)

    from orchestrator.actions import escalate as mod

    out = await mod.escalate(
        body=_make_body(event="verify.escalate"),
        req_id=_REQ_ID,
        tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/sisyphus"],
        },
    )

    assert out.get("escalated") is True, (
        f"get_controller() failure must not block escalate; got: {out}"
    )


# ─── SUPR: start_analyze supersede stale dirs ────────────────────────────────

_VN_REQ_ID = "REQ-foo-v2"       # has -vN suffix; base_slug = "REQ-foo"
_PLAIN_REQ_ID = "REQ-foo-1234"  # no -vN suffix; base_slug = "REQ-foo-1234"


class _FakeAdmissionDecision:
    admit = True
    reason = ""


def _patch_start_analyze_base(monkeypatch, controller):
    """Patch external deps of start_analyze for SUPR tests."""
    from orchestrator import k8s_runner as krunner

    monkeypatch.setattr("orchestrator.actions.start_analyze.BKDClient", _fake_bkd_ctx())
    monkeypatch.setattr("orchestrator.actions.start_analyze.db.get_pool", lambda: _FakePool())
    monkeypatch.setattr(krunner, "get_controller", lambda: controller)
    monkeypatch.setattr(
        "orchestrator.actions.start_analyze.check_admission",
        AsyncMock(return_value=_FakeAdmissionDecision()),
    )
    monkeypatch.setattr(
        "orchestrator.actions.start_analyze.req_state.update_context",
        AsyncMock(),
    )


def _make_analyze_body(issue_id: str = "intent-1"):
    return type("B", (), {
        "issueId": issue_id,
        "projectId": "proj-test",
        "event": "session.completed",
        "title": "supersede test",
        "tags": [],
        "issueNumber": None,
    })()


async def test_SUPR_S1_vN_redispatch_triggers_supersede(monkeypatch):
    """SUPR-S1: vN req_id dispatch → exec_in_runner called with command containing
    '_superseded' AND the current req_id (vN version).

    GIVEN REQ-foo-v2 is being dispatched with involved_repos in ctx
    WHEN start_analyze runs
    THEN exec_in_runner is called with a command containing '_superseded' and 'REQ-foo-v2'
    """
    from orchestrator.actions import start_analyze as mod

    exec_calls: list[dict] = []

    class FakeController:
        async def ensure_runner(self, req_id, *, wait_ready=False):
            return f"runner-{req_id}"

        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            pass

        async def exec_in_runner(self, req_id, command, **kwargs):
            exec_calls.append({"req_id": req_id, "command": command})
            return _ExecResult(exit_code=0)

    _patch_start_analyze_base(monkeypatch, FakeController())

    await mod.start_analyze(
        body=_make_analyze_body(),
        req_id=_VN_REQ_ID,
        tags=["intent:analyze"],
        ctx={"involved_repos": ["phona/sisyphus"]},
    )

    supersede_calls = [
        c for c in exec_calls
        if "_superseded" in c["command"]
    ]
    assert len(supersede_calls) >= 1, (
        f"SUPR-S1: vN redispatch must trigger exec_in_runner with '_superseded' in command; "
        f"all exec_calls: {exec_calls!r}"
    )
    for call in supersede_calls:
        assert _VN_REQ_ID in call["command"], (
            f"SUPR-S1: supersede command must reference current req_id '{_VN_REQ_ID}'; "
            f"command: {call['command']!r}"
        )


async def test_SUPR_S2_non_vN_slug_no_mv_possible(monkeypatch):
    """SUPR-S2: non-vN req_id → base_slug equals current req_id → no stale-dir mv.

    When base_slug == req_id, the supersede loop skips the only matching dir
    (the current one) and cannot move anything to _superseded.

    GIVEN REQ-foo-1234 (no -vN suffix) is dispatched
    WHEN start_analyze runs _supersede_stale_openspec_changes
    THEN if a supersede command is sent, its base_slug equals the current req_id
         (meaning the for-loop will find no other dirs to move)
    """
    from orchestrator.actions import start_analyze as mod

    exec_calls: list[dict] = []

    class FakeController:
        async def ensure_runner(self, req_id, *, wait_ready=False):
            return f"runner-{req_id}"

        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            pass

        async def exec_in_runner(self, req_id, command, **kwargs):
            exec_calls.append({"command": command})
            return _ExecResult(exit_code=0)

    _patch_start_analyze_base(monkeypatch, FakeController())

    await mod.start_analyze(
        body=_make_analyze_body(),
        req_id=_PLAIN_REQ_ID,
        tags=["intent:analyze"],
        ctx={"involved_repos": ["phona/sisyphus"]},
    )

    # If a supersede script is sent at all, base_slug must equal req_id
    # (so the for-loop inside the script finds no other dirs to move)
    supersede_calls = [c for c in exec_calls if "_superseded" in c["command"]]
    for call in supersede_calls:
        cmd = call["command"]
        assert f"base_slug='{_PLAIN_REQ_ID}'" in cmd, (
            f"SUPR-S2: for non-vN req_id, base_slug must equal req_id '{_PLAIN_REQ_ID}' "
            f"(so no stale dirs can match); command: {cmd!r}"
        )
        assert f"current='{_PLAIN_REQ_ID}'" in cmd, (
            f"SUPR-S2: current must be '{_PLAIN_REQ_ID}'; command: {cmd!r}"
        )
        # Both base_slug and current are the same → loop finds nothing to move
        assert f"base_slug='{_PLAIN_REQ_ID}'" in cmd and f"current='{_PLAIN_REQ_ID}'" in cmd, (
            "SUPR-S2: base_slug and current must be equal for non-vN req_id"
        )


async def test_SUPR_S3_supersede_exec_failure_does_not_block_dispatch(monkeypatch):
    """SUPR-S3: exec_in_runner raising during supersede must not block BKD dispatch.

    GIVEN exec_in_runner raises RuntimeError during supersede
    WHEN start_analyze runs
    THEN BKD dispatch still proceeds (result does not signal escalation)
    """
    from orchestrator.actions import start_analyze as mod

    clone_done = {"count": 0}

    class FakeController:
        async def ensure_runner(self, req_id, *, wait_ready=False):
            return f"runner-{req_id}"

        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            pass

        async def exec_in_runner(self, req_id, command, **kwargs):
            if "_superseded" not in command:
                # clone script succeeds
                clone_done["count"] += 1
                return _ExecResult(exit_code=0)
            # supersede script fails
            raise RuntimeError("simulated supersede exec failure for SUPR-S3")

    _patch_start_analyze_base(monkeypatch, FakeController())

    try:
        out = await mod.start_analyze(
            body=_make_analyze_body(),
            req_id=_VN_REQ_ID,
            tags=["intent:analyze"],
            ctx={"involved_repos": ["phona/sisyphus"]},
        )
    except Exception as exc:
        pytest.fail(
            f"SUPR-S3: supersede exec failure must not raise from start_analyze; got: {exc!r}"
        )

    # Result must not signal escalation
    result_signals_escalation = (
        (out or {}).get("emit") in ("VERIFY_ESCALATE", "verify.escalate") or
        (out or {}).get("escalated") is True
    )
    assert not result_signals_escalation, (
        f"SUPR-S3: supersede exec failure must not block dispatch; got result: {out!r}"
    )


# ─── COP: cleanup script classify + collect ──────────────────────────────────


def _load_cleanup_module():
    """Load orchestrator/scripts/cleanup_orphan_openspec_changes.py via importlib."""
    path = _SCRIPTS_DIR / "cleanup_orphan_openspec_changes.py"
    if not path.exists():
        pytest.skip(f"cleanup script not found at {path}")

    src_path = str(Path(__file__).parent.parent / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    spec = importlib.util.spec_from_file_location("_cleanup_script", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


async def test_COP_S1_done_state_maps_to_delete(monkeypatch):
    """COP-S1: _classify with PG state='done' → DirStatus with action='delete'.

    GIVEN a REQ dir name with PG state='done'
    WHEN _classify(["REQ-done-1234"], pg_url) is called
    THEN the returned DirStatus has action='delete'
    """
    mod = _load_cleanup_module()
    monkeypatch.setattr(
        mod, "_query_states",
        AsyncMock(return_value={"REQ-done-1234": "done"}),
    )
    results = await mod._classify(["REQ-done-1234"], "postgresql://fake/test")
    assert len(results) == 1, f"COP-S1: _classify must return one result; got: {results!r}"
    assert results[0].action == "delete", (
        f"COP-S1: state='done' must map to action='delete'; got action={results[0].action!r}"
    )


async def test_COP_S2_escalated_state_maps_to_delete(monkeypatch):
    """COP-S2: _classify with PG state='escalated' → DirStatus with action='delete'."""
    mod = _load_cleanup_module()
    monkeypatch.setattr(
        mod, "_query_states",
        AsyncMock(return_value={"REQ-esc-1234": "escalated"}),
    )
    results = await mod._classify(["REQ-esc-1234"], "postgresql://fake/test")
    assert len(results) == 1, f"COP-S2: _classify must return one result; got: {results!r}"
    assert results[0].action == "delete", (
        f"COP-S2: state='escalated' must map to action='delete'; "
        f"got action={results[0].action!r}"
    )


async def test_COP_S3_in_flight_state_maps_to_keep(monkeypatch):
    """COP-S3: _classify with in-flight state → DirStatus with action='keep'."""
    mod = _load_cleanup_module()
    monkeypatch.setattr(
        mod, "_query_states",
        AsyncMock(return_value={"REQ-inflight-1234": "analyzing"}),
    )
    results = await mod._classify(["REQ-inflight-1234"], "postgresql://fake/test")
    assert len(results) == 1, f"COP-S3: _classify must return one result; got: {results!r}"
    assert results[0].action == "keep", (
        f"COP-S3: in-flight state='analyzing' must map to action='keep'; "
        f"got action={results[0].action!r}"
    )


async def test_COP_S4_pg_no_record_maps_to_delete_not_found(monkeypatch):
    """COP-S4: no PG row → _classify returns DirStatus with action='delete', state='not_found'.

    GIVEN a REQ dir with no matching row in req_state (_query_states returns empty dict)
    WHEN _classify(["REQ-ghost-9999"], pg_url) is called
    THEN DirStatus.action == 'delete' AND DirStatus.state == 'not_found'
    """
    mod = _load_cleanup_module()
    monkeypatch.setattr(
        mod, "_query_states",
        AsyncMock(return_value={}),  # empty → no record
    )
    results = await mod._classify(["REQ-ghost-9999"], "postgresql://fake/test")
    assert len(results) == 1, f"COP-S4: _classify must return one result; got: {results!r}"
    assert results[0].action == "delete", (
        f"COP-S4: PG no record must map to action='delete'; got action={results[0].action!r}"
    )
    assert results[0].state == "not_found", (
        f"COP-S4: PG no record must set state='not_found'; got state={results[0].state!r}"
    )


def test_COP_S5_archive_and_superseded_excluded_from_scan(tmp_path):
    """COP-S5: _collect_req_dirs(repo_root) excludes 'archive' and '_superseded' subdirs.

    GIVEN repo_root/openspec/changes/ contains REQ-real-1234, archive/, and _superseded/
    WHEN _collect_req_dirs(repo_root) is called
    THEN only REQ-real-1234 appears; archive and _superseded are absent
    """
    mod = _load_cleanup_module()

    # _collect_req_dirs(repo_root) internally reads repo_root/openspec/changes/
    (tmp_path / "openspec" / "changes" / "REQ-real-1234").mkdir(parents=True)
    (tmp_path / "openspec" / "changes" / "archive").mkdir()
    (tmp_path / "openspec" / "changes" / "_superseded").mkdir()

    results = mod._collect_req_dirs(tmp_path)  # pass repo_root, not changes_dir

    result_strs = set(results)  # _collect_req_dirs returns list[str] (dir names)

    assert "REQ-real-1234" in result_strs, (
        f"COP-S5: 'REQ-real-1234' must appear in _collect_req_dirs result; got: {result_strs!r}"
    )
    assert "archive" not in result_strs, (
        f"COP-S5: 'archive' must be excluded from _collect_req_dirs; got: {result_strs!r}"
    )
    assert "_superseded" not in result_strs, (
        f"COP-S5: '_superseded' must be excluded from _collect_req_dirs; got: {result_strs!r}"
    )
