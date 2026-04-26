"""Contract tests for REQ-stage-runs-token-tracking-1777220172.

Black-box behavioral contracts derived from
  openspec/changes/REQ-stage-runs-token-tracking-1777220172/specs/
    stage-runs-token-tracking/spec.md

Scenarios covered:
  STR-S1  webhook 收到 agent stage 的 session.completed → fetch issue 拿到
          externalSessionId → stamp_bkd_session_id 在 engine.step 之前被调用，
          stage 取自 cur_state（而非 tag 嗅探）
  STR-S2  webhook 收到 mechanical stage 的 issue.updated（spec_lint 等）→ 不
          stamp（cur_stage 不在 AGENT_STAGES）
  STR-S3  BKD issue 没 externalSessionId（agent session 还没起来）→ 不 stamp
  STR-S4  session.failed 也走 fetch + stamp 路径，让崩掉的 stage_run 也能在
          dashboard 上跳到对应 BKD chat
"""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock

import pytest


class _FakePool:
    def __init__(self):
        self.executed: list = []

    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def _make_request(
    event: str,
    tags: list[str] | None,
    issue_id: str = "issue-str",
) -> object:
    class _Req:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}

        async def json(self_inner):
            return {
                "event": event,
                "issueId": issue_id,
                "issueNumber": 7,
                "projectId": "proj-str",
                "executionId": "exec-str",
                "tags": tags,
            }

    return _Req()


def _mock_bkd_returning(monkeypatch, webhook_mod, *, tags, external_session_id):
    """Stub BKDClient.get_issue → Issue 带指定 tags + externalSessionId."""
    from orchestrator.bkd import Issue

    def make_issue():
        return Issue(
            id="issue-str", project_id="proj-str", issue_number=7,
            title="t", status_id="working", tags=tags,
            session_status=None,
            external_session_id=external_session_id,
        )

    class _BKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_issue(self, *a, **kw):
            return make_issue()

        async def update_issue(self, *a, **kw):
            pass

    monkeypatch.setattr(webhook_mod, "BKDClient", _BKD)


def _setup_passthrough(
    monkeypatch,
    *,
    cur_state,
    derive_event_val,
    req_id: str = "REQ-x",
):
    """Mock dedup / db / state / engine.step / obs.record_event 让 webhook 跑到
    stamp 那段。返回 stamp_calls + step_calls 用于断言。"""
    import orchestrator.observability as obs
    from orchestrator import engine
    from orchestrator import router as router_lib
    from orchestrator.store import db, dedup, stage_runs
    from orchestrator.store import req_state as rs_mod

    stamp_calls: list[tuple] = []
    step_calls: list = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="new"))
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock())
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(obs, "record_event", AsyncMock())
    monkeypatch.setattr(router_lib, "extract_req_id", lambda tags, num=None: req_id)
    monkeypatch.setattr(router_lib, "derive_event", lambda evt, tags: derive_event_val)

    class _Row:
        state = cur_state
        context: ClassVar = {}

    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=_Row()))
    monkeypatch.setattr(rs_mod, "insert_init", AsyncMock())
    monkeypatch.setattr(rs_mod, "update_context", AsyncMock())

    async def _step(*a, **kw):
        step_calls.append(("step", kw))
        return {"action": "noop"}

    monkeypatch.setattr(engine, "step", _step)

    async def _stamp(pool, req, stage, sid):
        stamp_calls.append((req, stage, sid))
        return 1

    monkeypatch.setattr(stage_runs, "stamp_bkd_session_id", _stamp)

    return stamp_calls, step_calls


# ─── STR-S1: agent stage session.completed → stamp before engine.step ────────


@pytest.mark.asyncio
async def test_str_s1_agent_stage_session_completed_stamps_before_step(monkeypatch):
    """STR-S1: cur_state ANALYZING + session.completed → stamp_bkd_session_id
    被调用 (req, "analyze", externalSessionId) 且发生在 engine.step 之前。
    stage 来自 cur_state 映射，不嗅探 tags。
    """
    from orchestrator import webhook
    from orchestrator.state import Event, ReqState

    stamp_calls, step_calls = _setup_passthrough(
        monkeypatch,
        cur_state=ReqState.ANALYZING,
        derive_event_val=Event.ANALYZE_DONE,
    )
    _mock_bkd_returning(
        monkeypatch, webhook,
        tags=["analyze", "REQ-x"],
        external_session_id="sess-analyze-uuid",
    )

    # 用一个共享 list 保留调用顺序
    order: list[str] = []
    from orchestrator.store import stage_runs

    async def _stamp_ordered(pool, req, stage, sid):
        order.append("stamp")
        stamp_calls.append((req, stage, sid))
        return 1

    monkeypatch.setattr(stage_runs, "stamp_bkd_session_id", _stamp_ordered)

    from orchestrator import engine

    async def _step_ordered(*a, **kw):
        order.append("step")
        step_calls.append(("step", kw))
        return {"action": "noop"}

    monkeypatch.setattr(engine, "step", _step_ordered)

    req = _make_request("session.completed", None)
    await webhook.webhook(req)

    assert stamp_calls == [("REQ-x", "analyze", "sess-analyze-uuid")], (
        f"STR-S1: stamp_bkd_session_id must be called once with (REQ-x, "
        f"'analyze', 'sess-analyze-uuid'), got {stamp_calls!r}"
    )
    assert order.index("stamp") < order.index("step"), (
        f"STR-S1: stamp must run before engine.step (otherwise close_latest_stage_run "
        f"would have already moved the row out of ended_at IS NULL), got order={order!r}"
    )


# ─── STR-S2: mechanical stage → no stamp ─────────────────────────────────────


@pytest.mark.asyncio
async def test_str_s2_mechanical_stage_does_not_stamp(monkeypatch):
    """STR-S2: cur_state SPEC_LINT_RUNNING (机械 checker，不在 AGENT_STAGES) →
    即使 webhook fetch 到 issue 也不 stamp。"""
    from orchestrator import webhook
    from orchestrator.state import Event, ReqState

    stamp_calls, _ = _setup_passthrough(
        monkeypatch,
        cur_state=ReqState.SPEC_LINT_RUNNING,
        derive_event_val=Event.SPEC_LINT_PASS,
    )
    # 即使 BKD 给了 sid，也不该 stamp 机械 stage（它没 BKD agent）
    _mock_bkd_returning(
        monkeypatch, webhook,
        tags=["spec_lint", "REQ-x", "result:pass"],
        external_session_id="should-be-ignored",
    )

    req = _make_request("session.completed", None)
    await webhook.webhook(req)

    assert stamp_calls == [], (
        f"STR-S2: stamp must be skipped for non-agent stages (spec_lint/dev_cross_check/"
        f"staging_test/pr_ci/accept_teardown), got {stamp_calls!r}"
    )


# ─── STR-S3: 没 externalSessionId → no stamp ─────────────────────────────────


@pytest.mark.asyncio
async def test_str_s3_no_external_session_id_skips_stamp(monkeypatch):
    """STR-S3: BKD issue 还没分到 externalSessionId（None）→ 不 stamp。
    避免给 stage_run 写空字符串。"""
    from orchestrator import webhook
    from orchestrator.state import Event, ReqState

    stamp_calls, _ = _setup_passthrough(
        monkeypatch,
        cur_state=ReqState.ANALYZING,
        derive_event_val=Event.ANALYZE_DONE,
    )
    _mock_bkd_returning(
        monkeypatch, webhook,
        tags=["analyze", "REQ-x"],
        external_session_id=None,
    )

    req = _make_request("session.completed", None)
    await webhook.webhook(req)

    assert stamp_calls == [], (
        f"STR-S3: stamp must be skipped when BKD issue has no externalSessionId, "
        f"got {stamp_calls!r}"
    )


# ─── STR-S4: session.failed also fetches and stamps ──────────────────────────


@pytest.mark.asyncio
async def test_str_s4_session_failed_also_stamps(monkeypatch):
    """STR-S4: session.failed 也 fetch issue + stamp，让崩掉的 stage_run 也能从
    dashboard 跳到对应 BKD chat 排查 agent 行为。

    REVIEW_RUNNING + SESSION_FAILED 是合法 transition（self-loop → ESCALATED via
    escalate action），derive_event 给 verifier issue session.failed 返回
    Event.SESSION_FAILED，让 webhook 进 stamp 分支。
    """
    from orchestrator import webhook
    from orchestrator.state import Event, ReqState

    stamp_calls, _ = _setup_passthrough(
        monkeypatch,
        cur_state=ReqState.REVIEW_RUNNING,
        derive_event_val=Event.SESSION_FAILED,
    )
    _mock_bkd_returning(
        monkeypatch, webhook,
        tags=["verifier", "verify:staging_test", "REQ-x"],
        external_session_id="sess-verifier-crashed",
    )

    req = _make_request("session.failed", None)
    await webhook.webhook(req)

    assert stamp_calls == [("REQ-x", "verifier", "sess-verifier-crashed")], (
        f"STR-S4: session.failed must also fetch + stamp so crashed agent runs "
        f"are still linked to BKD chat, got {stamp_calls!r}"
    )
