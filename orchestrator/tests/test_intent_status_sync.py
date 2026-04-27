"""Contract tests for REQ-bkd-hitl-end-to-end-loop-1777273753.

When the orchestrator transitions a REQ into a terminal state (DONE or
ESCALATED), the BKD intent issue's `statusId` MUST be PATCHed to the
column that mirrors that terminal state ("done" or "review"). Covers
HITL-S1..S6 from openspec/changes/REQ-bkd-hitl-end-to-end-loop-1777273753/
specs/intent-status-sync/spec.md.
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


# ─── Fake req_state pool（沿用 test_engine.FakePool 同模式 minimal version）────
@dataclass
class _FakeReq:
    state: str
    history: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)


class _FakePool:
    def __init__(self, initial: dict[str, _FakeReq]):
        self.rows = initial
        self._next_id = 1

    async def fetchrow(self, sql: str, *args):
        s = sql.strip()
        if s.startswith("SELECT"):
            rid = args[0]
            r = self.rows.get(rid)
            if r is None:
                return None
            return {
                "req_id": rid, "project_id": "proj-x", "state": r.state,
                "history": json.dumps(r.history),
                "context": json.dumps(r.context),
                "created_at": None, "updated_at": None,
            }
        if s.startswith("UPDATE req_state"):
            rid, expected, target, history_json, *rest = args
            r = self.rows.get(rid)
            if r is None or r.state != expected:
                return None
            r.state = target
            r.history.extend(json.loads(history_json))
            if rest:
                try:
                    patch_d = json.loads(rest[0])
                    if isinstance(patch_d, dict):
                        r.context.update(patch_d)
                except (json.JSONDecodeError, TypeError):
                    pass
            return {"req_id": rid}
        if s.startswith("INSERT INTO stage_runs") or s.startswith("UPDATE stage_runs"):
            rid = self._next_id
            self._next_id += 1
            return {"id": rid}
        raise NotImplementedError(s[:60])

    async def execute(self, sql: str, *args):
        s = sql.strip()
        if s.startswith("UPDATE req_state SET context"):
            rid, patch_json = args
            try:
                p = json.loads(patch_json)
            except (json.JSONDecodeError, TypeError):
                return
            r = self.rows.get(rid)
            if r and isinstance(p, dict):
                r.context.update(p)
            return
        if s.startswith("UPDATE stage_runs"):
            return
        raise NotImplementedError(s[:60])


@pytest.fixture
def _isolated_actions(monkeypatch):
    """Empty REGISTRY so engine doesn't hit real handlers; restore on exit."""
    saved_reg = dict(REGISTRY)
    saved_meta = dict(ACTION_META)
    REGISTRY.clear()
    ACTION_META.clear()
    yield REGISTRY
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(saved_reg)
    ACTION_META.update(saved_meta)


@pytest.fixture
def _mock_runner_controller():
    fake = MagicMock()
    fake.cleanup_runner = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


def _make_bkd_mock(update_issue: AsyncMock | None = None):
    """Build a context-manager-compatible BKDClient mock that records update_issue calls."""
    update_issue = update_issue or AsyncMock(return_value=None)
    inst = MagicMock()
    inst.update_issue = update_issue
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=None)
    return inst, update_issue


async def _drain_tasks() -> None:
    """Let fire-and-forget asyncio.create_task() jobs complete."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ═══════════════════════════════════════════════════════════════════════
# HITL-S1: transition into DONE patches intent statusId="done"
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_hitl_s1_done_transition_patches_intent_statusid_done(
    _isolated_actions, _mock_runner_controller,
):
    inst, update_issue = _make_bkd_mock()
    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.ARCHIVING.value,
            context={"intent_issue_id": "abc123"},
        ),
    })
    body = type("B", (), {
        "issueId": "archive-1", "projectId": "proj-x",
        "event": "session.completed",
    })()

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine.step(
            pool, body=body, req_id="REQ-1", project_id="proj-x", tags=[],
            cur_state=ReqState.ARCHIVING,
            ctx={"intent_issue_id": "abc123"},
            event=Event.ARCHIVE_DONE,
        )
        await _drain_tasks()

    assert pool.rows["REQ-1"].state == ReqState.DONE.value
    update_issue.assert_awaited_once_with(
        project_id="proj-x", issue_id="abc123", status_id="done",
    )


# ═══════════════════════════════════════════════════════════════════════
# HITL-S2: transition into ESCALATED patches intent statusId="review"
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_hitl_s2_escalated_transition_patches_intent_statusid_review(
    _isolated_actions, _mock_runner_controller,
):
    """state.py 表声明 next_state=ESCALATED 的路径（非 SESSION_FAILED self-loop）。

    比如 REVIEW_RUNNING + VERIFY_ESCALATE → ESCALATED + escalate action。
    本测试不让 escalate action 真跑（_isolated_actions 清了 REGISTRY），
    只验证 engine 在 CAS 成功后 schedule 了 sync helper.
    """
    inst, update_issue = _make_bkd_mock()
    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.REVIEW_RUNNING.value,
            context={"intent_issue_id": "abc123"},
        ),
    })

    # stub escalate action to no-op so we don't need its full deps
    async def _noop_escalate(*, body, req_id, tags, ctx):
        return {"escalated": True}

    REGISTRY["escalate"] = _noop_escalate

    body = type("B", (), {
        "issueId": "verifier-1", "projectId": "proj-x",
        "event": "session.completed",
    })()

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine.step(
            pool, body=body, req_id="REQ-1", project_id="proj-x", tags=[],
            cur_state=ReqState.REVIEW_RUNNING,
            ctx={"intent_issue_id": "abc123"},
            event=Event.VERIFY_ESCALATE,
        )
        await _drain_tasks()

    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    update_issue.assert_awaited_once_with(
        project_id="proj-x", issue_id="abc123", status_id="review",
    )


# ═══════════════════════════════════════════════════════════════════════
# HITL-S3: escalate self-loop CAS to ESCALATED triggers intent statusId="review"
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_hitl_s3_escalate_session_failed_self_loop_patches_intent_review(
    monkeypatch, _isolated_actions, _mock_runner_controller,
):
    """SESSION_FAILED in *_RUNNING：transition 是 self-loop（state 表面没动）；
    escalate action 内部手动 CAS 推到 ESCALATED + cleanup + intent statusId sync.
    """
    from orchestrator.actions import escalate as escalate_mod

    inst, update_issue = _make_bkd_mock()

    # 路径 2（真 escalate 分支）：retry_count=2 already → 直接走 真 escalate。
    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.STAGING_TEST_RUNNING.value,
            context={
                "intent_issue_id": "abc123",
                "auto_retry_count": 2,
            },
        ),
    })

    # patch BKDClient inside escalate (used by merge_tags_and_update + new sync)
    monkeypatch.setattr(escalate_mod, "BKDClient", lambda *a, **kw: inst)
    # add merge_tags_and_update to the BKD instance（escalate.py 流程用）
    inst.merge_tags_and_update = AsyncMock(return_value=None)

    # mock db module so db.get_pool() inside escalate doesn't raise
    monkeypatch.setattr(escalate_mod, "db", MagicMock())

    # disable PR-merged shortcut (no involved repos resolved → returns False)
    monkeypatch.setattr(
        escalate_mod, "_all_prs_merged_for_req",
        AsyncMock(return_value=False),
    )
    # disable GH incident creation
    monkeypatch.setattr(
        escalate_mod.gh_incident, "open_incident",
        AsyncMock(return_value=None),
    )

    # patch req_state inside escalate to use FakePool semantics
    import orchestrator.store.req_state as rs_mod
    saved = (rs_mod.cas_transition, rs_mod.update_context, rs_mod.get)

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

    @dataclass
    class _Row:
        state: ReqState
        context: dict

    async def _fake_get(_p, rid):
        r = pool.rows.get(rid)
        if r is None:
            return None
        return _Row(state=ReqState(r.state), context=dict(r.context))

    rs_mod.cas_transition = _fake_cas
    rs_mod.update_context = _fake_update_ctx
    rs_mod.get = _fake_get

    body = type("B", (), {
        "issueId": "abc123", "projectId": "proj-x",
        "event": "watchdog.stuck",
    })()

    try:
        await escalate_mod.escalate(
            body=body, req_id="REQ-1",
            tags=[], ctx=dict(pool.rows["REQ-1"].context),
        )
    finally:
        rs_mod.cas_transition, rs_mod.update_context, rs_mod.get = saved

    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    # intent statusId PATCH must have happened in the self-loop branch
    update_issue.assert_awaited_once_with(
        project_id="proj-x", issue_id="abc123", status_id="review",
    )


# ═══════════════════════════════════════════════════════════════════════
# HITL-S4: BKD PATCH failure → log warning, state machine continues
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_hitl_s4_bkd_patch_failure_logs_warning_no_rollback(
    _isolated_actions, _mock_runner_controller, caplog,
):
    update_issue = AsyncMock(side_effect=RuntimeError("bkd 503"))
    inst, _ = _make_bkd_mock(update_issue=update_issue)

    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.ARCHIVING.value,
            context={"intent_issue_id": "abc123"},
        ),
    })
    body = type("B", (), {
        "issueId": "archive-1", "projectId": "proj-x",
        "event": "session.completed",
    })()

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        result = await engine.step(
            pool, body=body, req_id="REQ-1", project_id="proj-x", tags=[],
            cur_state=ReqState.ARCHIVING,
            ctx={"intent_issue_id": "abc123"},
            event=Event.ARCHIVE_DONE,
        )
        await _drain_tasks()

    # State transition still succeeded
    assert pool.rows["REQ-1"].state == ReqState.DONE.value
    assert result["next_state"] == ReqState.DONE.value
    # Cleanup task still ran
    _mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=False,
    )
    # PATCH was attempted exactly once
    update_issue.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# HITL-S5: PR-merged shortcut MUST NOT call the new sync helper
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_hitl_s5_pr_merged_shortcut_does_not_call_new_sync_helper(
    monkeypatch, _isolated_actions, _mock_runner_controller,
):
    """PR-merged shortcut 直接 cas_transition + merge_tags_and_update，从不
    re-enter engine.step；本 REQ 新增的 sync helper 必然不被触发。
    """
    from orchestrator.actions import escalate as escalate_mod

    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.ACCEPT_RUNNING.value,
            context={
                "intent_issue_id": "abc123",
                "involved_repos": ["phona/sisyphus"],
            },
        ),
    })

    # PR-merged probe → True
    monkeypatch.setattr(
        escalate_mod, "_all_prs_merged_for_req",
        AsyncMock(return_value=True),
    )

    inst, _update_issue = _make_bkd_mock()
    inst.merge_tags_and_update = AsyncMock(return_value=None)
    monkeypatch.setattr(escalate_mod, "BKDClient", lambda *a, **kw: inst)

    # mock db module so db.get_pool() inside escalate doesn't raise
    monkeypatch.setattr(escalate_mod, "db", MagicMock())

    # spy on the new helper to assert it's NOT called from this path
    sync_spy = AsyncMock(return_value=None)
    monkeypatch.setattr(engine, "_sync_intent_status_on_terminal", sync_spy)

    # patch req_state for FakePool
    import orchestrator.store.req_state as rs_mod
    saved = (rs_mod.cas_transition, rs_mod.update_context, rs_mod.get)

    @dataclass
    class _Row:
        state: ReqState
        context: dict

    async def _fake_cas(_p, rid, expected, target, evt, action, context_patch=None):
        r = pool.rows.get(rid)
        if r is None or r.state != expected.value:
            return False
        r.state = target.value
        if context_patch:
            r.context.update(context_patch)
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

    rs_mod.cas_transition = _fake_cas
    rs_mod.update_context = _fake_update_ctx
    rs_mod.get = _fake_get

    body = type("B", (), {
        "issueId": "abc123", "projectId": "proj-x",
        "event": "session.completed",
    })()

    try:
        result = await escalate_mod.escalate(
            body=body, req_id="REQ-1",
            tags=[], ctx=dict(pool.rows["REQ-1"].context),
        )
    finally:
        rs_mod.cas_transition, rs_mod.update_context, rs_mod.get = saved

    # PR-merged path took over
    assert result["completed_via"] == "pr-merge"
    # The new sync helper from engine MUST NOT be called via this path
    sync_spy.assert_not_awaited()
    # The existing merge_tags_and_update with status_id="done" still fires
    inst.merge_tags_and_update.assert_awaited_once()
    call = inst.merge_tags_and_update.await_args
    assert call.kwargs.get("status_id") == "done"


# ═══════════════════════════════════════════════════════════════════════
# HITL-S6: self-loop transition does NOT invoke the sync helper
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_hitl_s6_self_loop_transition_does_not_trigger_sync(
    monkeypatch, _isolated_actions, _mock_runner_controller,
):
    """STAGING_TEST_RUNNING + STAGING_TEST_PASS → PR_CI_RUNNING：non-terminal,
    sync helper 不应被调用。
    """
    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.STAGING_TEST_RUNNING.value,
            context={"intent_issue_id": "abc123"},
        ),
    })

    async def _stub_create_pr_ci_watch(*, body, req_id, tags, ctx):
        return {"ok": True}

    REGISTRY["create_pr_ci_watch"] = _stub_create_pr_ci_watch

    sync_spy = AsyncMock(return_value=None)
    monkeypatch.setattr(engine, "_sync_intent_status_on_terminal", sync_spy)

    body = type("B", (), {
        "issueId": "x", "projectId": "proj-x", "event": "check.passed",
    })()
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="proj-x", tags=[],
        cur_state=ReqState.STAGING_TEST_RUNNING,
        ctx={"intent_issue_id": "abc123"},
        event=Event.STAGING_TEST_PASS,
    )
    await _drain_tasks()

    assert pool.rows["REQ-1"].state == ReqState.PR_CI_RUNNING.value
    sync_spy.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════
# Defensive: missing intent_issue_id → helper short-circuits no-op
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_helper_no_op_when_intent_issue_id_missing():
    """ctx 没 intent_issue_id（测试 / 重放路径）→ helper 静默 skip，不打 BKD。"""
    update_issue = AsyncMock(return_value=None)
    inst, _ = _make_bkd_mock(update_issue=update_issue)

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine._sync_intent_status_on_terminal(
            project_id="proj-x",
            intent_issue_id=None,
            terminal_state=ReqState.DONE,
            req_id="REQ-1",
        )

    update_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_helper_no_op_for_non_terminal_state():
    """non-terminal state 传进来（防御）→ helper 静默 skip。"""
    update_issue = AsyncMock(return_value=None)
    inst, _ = _make_bkd_mock(update_issue=update_issue)

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine._sync_intent_status_on_terminal(
            project_id="proj-x",
            intent_issue_id="abc123",
            terminal_state=ReqState.STAGING_TEST_RUNNING,  # not terminal
            req_id="REQ-1",
        )

    update_issue.assert_not_awaited()
