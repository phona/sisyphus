"""Challenger contract tests: BKD intent issue statusId sync on terminal transitions.

REQ-bkd-hitl-end-to-end-loop-1777273753

Black-box challenger. Derived exclusively from:
  openspec/changes/REQ-bkd-hitl-end-to-end-loop-1777273753/specs/intent-status-sync/spec.md
  openspec/changes/REQ-bkd-hitl-end-to-end-loop-1777273753/specs/intent-status-sync/contract.spec.yaml

Scenarios: HITL-S1 through HITL-S6 (all spec scenarios, one test per scenario).

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator import engine, k8s_runner
from orchestrator.actions import ACTION_META, REGISTRY
from orchestrator.state import Event, ReqState

# ─── Minimal fake database pool ──────────────────────────────────────────────

@dataclass
class _FakeRow:
    state: str
    context: dict = field(default_factory=dict)


class _FakePool:
    def __init__(self, rows: dict[str, _FakeRow]):
        self.rows = rows
        self._pk = 1

    async def fetchrow(self, sql: str, *args):
        s = sql.strip()
        if s.startswith("SELECT"):
            rid = args[0]
            r = self.rows.get(rid)
            if r is None:
                return None
            return {
                "req_id": rid, "project_id": "proj-x", "state": r.state,
                "history": json.dumps([]),
                "context": json.dumps(r.context),
                "created_at": None, "updated_at": None,
            }
        if s.startswith("UPDATE req_state"):
            rid, expected, target = args[0], args[1], args[2]
            r = self.rows.get(rid)
            if r is None or r.state != expected:
                return None
            r.state = target
            # apply context_patch if present (last args slot)
            if len(args) > 4:
                try:
                    patch_d = json.loads(args[-1])
                    if isinstance(patch_d, dict):
                        r.context.update(patch_d)
                except (json.JSONDecodeError, TypeError):
                    pass
            return {"req_id": rid}
        if "stage_runs" in s:
            pk = self._pk
            self._pk += 1
            return {"id": pk}
        raise NotImplementedError(s[:60])

    async def execute(self, sql: str, *args):
        s = sql.strip()
        if "context" in s and s.startswith("UPDATE req_state"):
            rid, patch_json = args[0], args[1]
            try:
                p = json.loads(patch_json)
            except (json.JSONDecodeError, TypeError):
                return
            r = self.rows.get(rid)
            if r and isinstance(p, dict):
                r.context.update(p)
            return
        if "stage_runs" in s:
            return
        raise NotImplementedError(s[:60])


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _bkd_mock():
    """Return (ctx_manager_inst, update_issue_mock)."""
    ui = AsyncMock(return_value=None)
    inst = MagicMock()
    inst.update_issue = ui
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=None)
    return inst, ui


class _Body:
    def __init__(self, event="session.completed", issue_id="x", project_id="proj-x"):
        self.event = event
        self.issueId = issue_id
        self.projectId = project_id


async def _drain():
    """Drain asyncio.create_task() fire-and-forget tasks."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def clean_registry(monkeypatch):
    """Clear REGISTRY so engine doesn't invoke real stage handlers."""
    saved_r, saved_m = dict(REGISTRY), dict(ACTION_META)
    REGISTRY.clear()
    ACTION_META.clear()
    yield REGISTRY
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(saved_r)
    ACTION_META.update(saved_m)


@pytest.fixture
def runner_ctrl():
    fake = MagicMock()
    fake.cleanup_runner = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


# ═══════════════════════════════════════════════════════════════════════════════
# HITL-S1: transition into DONE patches intent statusId="done"
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hitl_s1_done_transition_patches_statusid_done(clean_registry, runner_ctrl):
    """GIVEN REQ in ARCHIVING with intent_issue_id="abc123" and project_id="proj-x"
    WHEN engine.step processes ARCHIVE_DONE → CAS to DONE succeeds
    THEN BKDClient.update_issue(project_id="proj-x", issue_id="abc123", status_id="done")
    AND the call is fire-and-forget (engine.step returns without blocking on PATCH).
    """
    pool = _FakePool({"REQ-1": _FakeRow(
        state=ReqState.ARCHIVING.value,
        context={"intent_issue_id": "abc123"},
    )})
    inst, ui = _bkd_mock()

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        result = await engine.step(
            pool,
            body=_Body(event="session.completed", issue_id="archive-1"),
            req_id="REQ-1",
            project_id="proj-x",
            tags=[],
            cur_state=ReqState.ARCHIVING,
            ctx={"intent_issue_id": "abc123"},
            event=Event.ARCHIVE_DONE,
        )
        # drain fire-and-forget tasks
        await _drain()

    # State must be DONE
    assert pool.rows["REQ-1"].state == ReqState.DONE.value
    assert result["next_state"] == ReqState.DONE.value
    # BKD intent issue MUST have been PATCHed with statusId="done"
    ui.assert_awaited_once_with(
        project_id="proj-x", issue_id="abc123", status_id="done",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HITL-S2: transition into ESCALATED patches intent statusId="review"
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hitl_s2_escalated_transition_patches_statusid_review(clean_registry, runner_ctrl):
    """GIVEN REQ in REVIEW_RUNNING with intent_issue_id="abc123"
    WHEN engine.step processes VERIFY_ESCALATE → CAS to ESCALATED succeeds
    THEN BKDClient.update_issue(project_id="proj-x", issue_id="abc123", status_id="review").
    """
    pool = _FakePool({"REQ-1": _FakeRow(
        state=ReqState.REVIEW_RUNNING.value,
        context={"intent_issue_id": "abc123"},
    )})
    inst, ui = _bkd_mock()

    # REGISTRY needs an "escalate" handler for the engine to dispatch
    async def _noop_escalate(*, body, req_id, tags, ctx):
        return {"escalated": True}
    REGISTRY["escalate"] = _noop_escalate

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine.step(
            pool,
            body=_Body(event="session.completed", issue_id="verifier-1"),
            req_id="REQ-1",
            project_id="proj-x",
            tags=[],
            cur_state=ReqState.REVIEW_RUNNING,
            ctx={"intent_issue_id": "abc123"},
            event=Event.VERIFY_ESCALATE,
        )
        await _drain()

    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    ui.assert_awaited_once_with(
        project_id="proj-x", issue_id="abc123", status_id="review",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HITL-S3: escalate SESSION_FAILED self-loop triggers intent statusId="review"
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hitl_s3_escalate_session_failed_self_loop_patches_intent_review(
    monkeypatch, clean_registry, runner_ctrl,
):
    """GIVEN REQ in STAGING_TEST_RUNNING with watchdog.stuck event and intent_issue_id="abc123"
    WHEN escalate's internal SESSION_FAILED CAS to ESCALATED advances
    THEN BKDClient.update_issue(project_id="proj-x", issue_id="abc123", status_id="review")
    AND if PATCH raises, it is caught; escalate still returns {"escalated": True, ...}.
    """
    from orchestrator.actions import escalate as esc_mod

    pool = _FakePool({"REQ-1": _FakeRow(
        state=ReqState.STAGING_TEST_RUNNING.value,
        context={"intent_issue_id": "abc123", "auto_retry_count": 2},
    )})

    inst, ui = _bkd_mock()
    inst.merge_tags_and_update = AsyncMock(return_value=None)
    monkeypatch.setattr(esc_mod, "BKDClient", lambda *a, **kw: inst)
    monkeypatch.setattr(esc_mod, "db", MagicMock())
    monkeypatch.setattr(esc_mod, "_all_prs_merged_for_req", AsyncMock(return_value=False))
    monkeypatch.setattr(esc_mod.gh_incident, "open_incident", AsyncMock(return_value=None))

    import orchestrator.store.req_state as rs_mod

    @dataclass
    class _Row:
        state: ReqState
        context: dict

    async def _fake_cas(_p, rid, expected, target, evt, action, context_patch=None):
        r = pool.rows.get(rid)
        if r is None or r.state != expected.value:
            return False
        r.state = target.value
        return True

    async def _fake_update_ctx(_p, rid, patch_d):
        r = pool.rows.get(rid)
        if r:
            r.context.update(patch_d)

    async def _fake_get(_p, rid):
        r = pool.rows.get(rid)
        if r is None:
            return None
        return _Row(state=ReqState(r.state), context=dict(r.context))

    saved = (rs_mod.cas_transition, rs_mod.update_context, rs_mod.get)
    rs_mod.cas_transition = _fake_cas
    rs_mod.update_context = _fake_update_ctx
    rs_mod.get = _fake_get

    body = _Body(event="watchdog.stuck", issue_id="abc123")
    try:
        result = await esc_mod.escalate(
            body=body, req_id="REQ-1",
            tags=[], ctx=dict(pool.rows["REQ-1"].context),
        )
    finally:
        rs_mod.cas_transition, rs_mod.update_context, rs_mod.get = saved

    # Must have transitioned to ESCALATED
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    # BKD update_issue must have been called with status_id="review"
    ui.assert_awaited_once_with(
        project_id="proj-x", issue_id="abc123", status_id="review",
    )
    # escalate must still return its normal result
    assert result.get("escalated") is True


# ═══════════════════════════════════════════════════════════════════════════════
# HITL-S4: BKD PATCH failure → warning logged, state machine continues
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hitl_s4_bkd_patch_failure_warns_and_does_not_rollback(
    clean_registry, runner_ctrl, capsys,
):
    """GIVEN REQ transitioning into DONE, BKD responds with 503
    WHEN sync helper's update_issue raises
    THEN warning logged with engine.intent_status_sync_failed and req_id, intent_issue_id,
         target_status_id, error fields
    AND the helper MUST NOT re-raise
    AND req_state.state MUST remain DONE (no rollback)
    AND cleanup_runner MUST still be invoked.
    """
    pool = _FakePool({"REQ-1": _FakeRow(
        state=ReqState.ARCHIVING.value,
        context={"intent_issue_id": "abc123"},
    )})
    inst, ui = _bkd_mock()
    ui.side_effect = RuntimeError("HTTP 503 Service Unavailable")

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        result = await engine.step(
            pool,
            body=_Body(event="session.completed", issue_id="archive-1"),
            req_id="REQ-1",
            project_id="proj-x",
            tags=[],
            cur_state=ReqState.ARCHIVING,
            ctx={"intent_issue_id": "abc123"},
            event=Event.ARCHIVE_DONE,
        )
        await _drain()

    # State must still be DONE (no rollback)
    assert pool.rows["REQ-1"].state == ReqState.DONE.value
    assert result["next_state"] == ReqState.DONE.value

    # Cleanup must still run
    runner_ctrl.cleanup_runner.assert_awaited_once()

    # Warning log with "intent_status_sync_failed" must appear in output
    # structlog writes to stdout; caplog does not capture it
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "intent_status_sync_failed" in output, (
        f"Expected 'intent_status_sync_failed' warning in log output; got:\n{output}"
    )
    assert "abc123" in output, "Expected intent_issue_id in warning log"
    assert "done" in output, "Expected target_status_id in warning log"


# ═══════════════════════════════════════════════════════════════════════════════
# HITL-S5: PR-merged shortcut MUST NOT invoke the new sync helper
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hitl_s5_pr_merged_shortcut_skips_new_sync_helper(
    monkeypatch, clean_registry, runner_ctrl,
):
    """GIVEN REQ in ACCEPT_RUNNING whose feat/REQ-X PR is merged
    WHEN _apply_pr_merged_done_override returns successfully
    THEN _sync_intent_status_on_terminal MUST NOT be called
    AND existing merge_tags_and_update(...status_id="done") MUST still fire.
    """
    from orchestrator.actions import escalate as esc_mod

    pool = _FakePool({"REQ-1": _FakeRow(
        state=ReqState.ACCEPT_RUNNING.value,
        context={"intent_issue_id": "abc123", "involved_repos": ["phona/sisyphus"]},
    )})

    monkeypatch.setattr(esc_mod, "_all_prs_merged_for_req", AsyncMock(return_value=True))

    inst, _ui = _bkd_mock()
    inst.merge_tags_and_update = AsyncMock(return_value=None)
    monkeypatch.setattr(esc_mod, "BKDClient", lambda *a, **kw: inst)
    monkeypatch.setattr(esc_mod, "db", MagicMock())

    # Spy on the new engine helper — must NOT be called via PR-merged path
    sync_spy = AsyncMock(return_value=None)
    monkeypatch.setattr(engine, "_sync_intent_status_on_terminal", sync_spy)

    import orchestrator.store.req_state as rs_mod

    @dataclass
    class _Row:
        state: ReqState
        context: dict

    async def _fake_cas(_p, rid, expected, target, evt, action, context_patch=None):
        r = pool.rows.get(rid)
        if r is None or r.state != expected.value:
            return False
        r.state = target.value
        return True

    async def _fake_update_ctx(_p, rid, patch_d):
        r = pool.rows.get(rid)
        if r:
            r.context.update(patch_d)

    async def _fake_get(_p, rid):
        r = pool.rows.get(rid)
        if r is None:
            return None
        return _Row(state=ReqState(r.state), context=dict(r.context))

    saved = (rs_mod.cas_transition, rs_mod.update_context, rs_mod.get)
    rs_mod.cas_transition = _fake_cas
    rs_mod.update_context = _fake_update_ctx
    rs_mod.get = _fake_get

    body = _Body(event="session.completed", issue_id="abc123")
    try:
        result = await esc_mod.escalate(
            body=body, req_id="REQ-1",
            tags=[], ctx=dict(pool.rows["REQ-1"].context),
        )
    finally:
        rs_mod.cas_transition, rs_mod.update_context, rs_mod.get = saved

    # PR-merged shortcut should be the path taken
    assert result.get("completed_via") == "pr-merge"
    # New sync helper must NOT have been invoked
    sync_spy.assert_not_awaited()
    # Existing merge_tags_and_update with status_id="done" must still fire
    inst.merge_tags_and_update.assert_awaited_once()
    call_kwargs = inst.merge_tags_and_update.await_args.kwargs
    assert call_kwargs.get("status_id") == "done"


# ═══════════════════════════════════════════════════════════════════════════════
# HITL-S6: self-loop or non-terminal transition MUST NOT trigger sync
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_hitl_s6_non_terminal_transition_does_not_trigger_sync(
    monkeypatch, clean_registry, runner_ctrl,
):
    """GIVEN transition (STAGING_TEST_RUNNING, STAGING_TEST_PASS) → PR_CI_RUNNING
    WHEN engine.step processes the event (non-terminal target)
    THEN _sync_intent_status_on_terminal MUST NOT be called
    AND no BKDClient.update_issue call for statusId is made by engine.
    """
    pool = _FakePool({"REQ-1": _FakeRow(
        state=ReqState.STAGING_TEST_RUNNING.value,
        context={"intent_issue_id": "abc123"},
    )})

    # Stub handler for the downstream stage
    async def _noop_pr_ci_watch(*, body, req_id, tags, ctx):
        return {"ok": True}
    REGISTRY["create_pr_ci_watch"] = _noop_pr_ci_watch

    sync_spy = AsyncMock(return_value=None)
    monkeypatch.setattr(engine, "_sync_intent_status_on_terminal", sync_spy)

    body = _Body(event="check.passed", issue_id="x")
    await engine.step(
        pool,
        body=body,
        req_id="REQ-1",
        project_id="proj-x",
        tags=[],
        cur_state=ReqState.STAGING_TEST_RUNNING,
        ctx={"intent_issue_id": "abc123"},
        event=Event.STAGING_TEST_PASS,
    )
    await _drain()

    # Must have moved to a non-terminal next state
    assert pool.rows["REQ-1"].state == ReqState.PR_CI_RUNNING.value
    # Sync helper must NOT have been invoked
    sync_spy.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════════
# Defensive: missing intent_issue_id → sync is a no-op
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_sync_noop_when_intent_issue_id_absent():
    """GIVEN ctx.intent_issue_id is absent (None or empty)
    WHEN _sync_intent_status_on_terminal is called
    THEN no BKDClient.update_issue call is made.
    """
    inst, ui = _bkd_mock()

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine._sync_intent_status_on_terminal(
            project_id="proj-x",
            intent_issue_id=None,
            terminal_state=ReqState.DONE,
            req_id="REQ-1",
        )

    ui.assert_not_awaited()
