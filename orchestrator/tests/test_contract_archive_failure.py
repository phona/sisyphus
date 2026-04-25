"""Contract tests for done-archive failure detection (REQ-archive-failure-watchdog-1777084279).

Black-box behavioral contracts derived from:
  openspec/changes/REQ-archive-failure-watchdog-1777084279/specs/archive-failure/spec.md

Scenarios covered:
  ARCH-S1  watchdog ARCHIVING 卡死 → body.event="archive.failed" + Event.SESSION_FAILED
  ARCH-S2  watchdog 非 ARCHIVING state 仍贴 body.event="watchdog.stuck"
  ARCH-S3  escalate 收到 body.event="archive.failed" → auto_resume + reason="archive-failed"
  ARCH-S4  escalate 收到 session.failed + issueId 匹配 archive_issue_id → reason="archive-failed"
  ARCH-S5  escalate 收到 session.failed + issueId 不匹配 → reason 保持 "session-failed"
  ARCH-S6  archive-failed retry 用完 → escalated + reason="archive-failed-after-2-retries"
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

from orchestrator.state import Event, ReqState

# ─── Shared fakes (定义独立于 unit test，不复用 unit test 实现) ────────────


@dataclass
class _FakePool:
    rows: list = field(default_factory=list)
    executed: list = field(default_factory=list)

    async def fetch(self, sql, *args):
        return self.rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return None


@dataclass
class _FakeIssue:
    session_status: str | None = "failed"
    id: str = "issue-1"
    project_id: str = "proj-1"
    issue_number: int = 0
    title: str = ""
    status_id: str = "todo"
    tags: list = field(default_factory=list)


def _row(req_id: str, state: str, ctx: dict | None = None, stuck_sec: int = 2000) -> dict:
    return {
        "req_id": req_id,
        "project_id": "proj-1",
        "state": state,
        "context": json.dumps(ctx or {}),
        "stuck_sec": stuck_sec,
    }


def _patch_pool(monkeypatch, pool: _FakePool) -> None:
    monkeypatch.setattr("orchestrator.watchdog.db.get_pool", lambda: pool)


def _patch_bkd_watchdog(monkeypatch, issue: _FakeIssue) -> AsyncMock:
    fake = AsyncMock()
    fake.get_issue = AsyncMock(return_value=issue)

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    monkeypatch.setattr("orchestrator.watchdog.BKDClient", _ctx)
    return fake


def _patch_engine(monkeypatch) -> list[dict]:
    """捕获 engine.step 调用，包括 body.event（archive.failed 契约的核心断言点）。"""
    calls: list[dict] = []

    async def fake_step(pool, *, body, req_id, project_id, tags, cur_state, ctx, event, depth=0):
        calls.append({
            "req_id": req_id,
            "project_id": project_id,
            "cur_state": cur_state,
            "event": event,
            "body_event": getattr(body, "event", None),
            "body_issue": getattr(body, "issueId", None),
        })
        return {"action": "escalate", "next_state": "escalated"}

    monkeypatch.setattr("orchestrator.watchdog.engine.step", fake_step)
    return calls


def _patch_artifact(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_insert(pool, req_id, stage, result):
        calls.append({"req_id": req_id, "stage": stage, "result": result})

    monkeypatch.setattr("orchestrator.watchdog.artifact_checks.insert_check", fake_insert)
    return calls


def _make_bkd_fake() -> AsyncMock:
    bkd = AsyncMock()
    bkd.create_issue = AsyncMock(return_value=_FakeIssue(id="new-1"))
    bkd.update_issue = AsyncMock(return_value=_FakeIssue(id="new-1"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    bkd.list_issues = AsyncMock(return_value=[])
    bkd.get_issue = AsyncMock(return_value=_FakeIssue(id="x", tags=["foo"]))
    bkd.merge_tags_and_update = AsyncMock(return_value=_FakeIssue(id="x"))
    return bkd


def _patch_bkd_escalate(monkeypatch, fake: AsyncMock) -> None:
    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    monkeypatch.setattr("orchestrator.actions.escalate.BKDClient", _ctx)


def _patch_db_escalate(monkeypatch) -> None:
    class _Pool:
        async def execute(self, sql, *args): pass
        async def fetchrow(self, sql, *args): return None

    monkeypatch.setattr("orchestrator.actions.escalate.db.get_pool", lambda: _Pool())


def _make_body(issue_id: str = "src-1", project_id: str = "proj-1", event: str = "session.completed"):
    return type("Body", (), {
        "issueId": issue_id,
        "projectId": project_id,
        "event": event,
        "title": "T",
        "tags": [],
        "issueNumber": None,
    })()


# ─── ARCH-S1: ARCHIVING 卡死 → body.event="archive.failed" ────────────────


async def test_arch_s1_archiving_emits_archive_failed_event(monkeypatch):
    """
    ARCH-S1: watchdog._tick() 遇到 state=ARCHIVING + session=failed 时，
    传给 engine.step 的 body.event 必须是 "archive.failed"（不是 "watchdog.stuck"）。
    event 参数必须仍是 Event.SESSION_FAILED（复用现有 state machine transition）。
    """
    from orchestrator import watchdog

    pool = _FakePool(rows=[
        _row(
            "REQ-arch-1",
            ReqState.ARCHIVING.value,
            ctx={"archive_issue_id": "arch-1", "intent_issue_id": "intent-1"},
        ),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd_watchdog(monkeypatch, _FakeIssue(session_status="failed", id="arch-1"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result["escalated"] >= 1, "ARCHIVING stuck REQ must be escalated"
    assert len(step_calls) == 1, f"engine.step must be called exactly once, got {len(step_calls)}"

    call = step_calls[0]
    assert call["body_event"] == "archive.failed", (
        f"ARCH-S1 contract: body.event must be 'archive.failed' for ARCHIVING state, "
        f"got {call['body_event']!r}"
    )
    assert call["event"] == Event.SESSION_FAILED, (
        f"ARCH-S1 contract: state machine event must remain SESSION_FAILED "
        f"(to reuse existing ARCHIVING self-loop transition), got {call['event']!r}"
    )
    assert call["cur_state"] == ReqState.ARCHIVING, (
        f"cur_state must be ARCHIVING, got {call['cur_state']!r}"
    )


# ─── ARCH-S2: 非 ARCHIVING state 仍贴 watchdog.stuck ──────────────────────


async def test_arch_s2_non_archiving_state_keeps_watchdog_stuck(monkeypatch):
    """
    ARCH-S2: watchdog 对非 ARCHIVING state（如 STAGING_TEST_RUNNING）触发的 escalate
    必须仍使用 body.event="watchdog.stuck"（不被 archive 路径污染）。
    """
    from orchestrator import watchdog

    pool = _FakePool(rows=[
        _row(
            "REQ-st-1",
            ReqState.STAGING_TEST_RUNNING.value,
            ctx={"staging_test_issue_id": "st-1", "intent_issue_id": "intent-1"},
        ),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd_watchdog(monkeypatch, _FakeIssue(session_status="failed", id="st-1"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    await watchdog._tick()

    assert len(step_calls) == 1, f"engine.step must be called once, got {len(step_calls)}"
    call = step_calls[0]
    assert call["body_event"] == "watchdog.stuck", (
        f"ARCH-S2 contract: non-ARCHIVING state must keep body.event='watchdog.stuck', "
        f"got {call['body_event']!r}"
    )


# ─── ARCH-S3: watchdog 路径 archive.failed → reason archive-failed ─────────


async def test_arch_s3_watchdog_path_reason_is_archive_failed(monkeypatch):
    """
    ARCH-S3: escalate action 收到 body.event="archive.failed" 且 auto_retry_count=0 时，
    必须 auto-resume（follow_up_issue 一次）并返回 reason="archive-failed"。
    不得触发 merge_tags_and_update（没真 escalate）。
    """
    from orchestrator.actions import escalate as mod

    fake = _make_bkd_fake()
    _patch_bkd_escalate(monkeypatch, fake)
    _patch_db_escalate(monkeypatch)

    body = _make_body(issue_id="arch-1", event="archive.failed")
    out = await mod.escalate(
        body=body,
        req_id="REQ-arch-1",
        tags=["archive"],
        ctx={"intent_issue_id": "intent-1", "archive_issue_id": "arch-1"},
    )

    assert out.get("auto_resumed") is True, (
        f"ARCH-S3 contract: archive.failed on first attempt must auto-resume, got {out!r}"
    )
    assert out.get("reason") == "archive-failed", (
        f"ARCH-S3 contract: reason must be 'archive-failed', got {out.get('reason')!r}"
    )
    assert out.get("retry") == 1, (
        f"ARCH-S3 contract: retry counter must be incremented to 1, got {out.get('retry')!r}"
    )
    fake.follow_up_issue.assert_awaited_once()
    fake.merge_tags_and_update.assert_not_awaited()


# ─── ARCH-S4: BKD session.failed + issueId 匹配 archive_issue_id ──────────


async def test_arch_s4_session_failed_webhook_archive_issue_match(monkeypatch):
    """
    ARCH-S4: escalate 收到 body.event="session.failed" 且 body.issueId 匹配
    ctx.archive_issue_id 时，reason 必须被 override 为 "archive-failed"。
    （BKD webhook 路径，watchdog 没机会打 archive.failed 标签）
    """
    from orchestrator.actions import escalate as mod

    fake = _make_bkd_fake()
    _patch_bkd_escalate(monkeypatch, fake)
    _patch_db_escalate(monkeypatch)

    body = _make_body(issue_id="arch-2", event="session.failed")
    out = await mod.escalate(
        body=body,
        req_id="REQ-arch-2",
        tags=["archive"],
        ctx={"intent_issue_id": "intent-1", "archive_issue_id": "arch-2"},
    )

    assert out.get("auto_resumed") is True, (
        f"ARCH-S4 contract: archive issue session.failed on first attempt must auto-resume, "
        f"got {out!r}"
    )
    assert out.get("reason") == "archive-failed", (
        f"ARCH-S4 contract: issueId matching archive_issue_id must override reason to "
        f"'archive-failed', got {out.get('reason')!r}"
    )
    assert out.get("retry") == 1


# ─── ARCH-S5: session.failed + issueId 不匹配 → reason 不变 ──────────────


async def test_arch_s5_session_failed_non_archive_issue_unaffected(monkeypatch):
    """
    ARCH-S5: escalate 收到 body.event="session.failed" 但 body.issueId 与
    ctx.archive_issue_id 不匹配时，reason 必须保持 "session-failed"。
    archive override 不得污染无关 session.failed。
    """
    from orchestrator.actions import escalate as mod

    fake = _make_bkd_fake()
    _patch_bkd_escalate(monkeypatch, fake)
    _patch_db_escalate(monkeypatch)

    body = _make_body(issue_id="dev-1", event="session.failed")
    out = await mod.escalate(
        body=body,
        req_id="REQ-dev-1",
        tags=["dev"],
        ctx={"intent_issue_id": "intent-1", "archive_issue_id": "arch-other"},
    )

    assert out.get("reason") == "session-failed", (
        f"ARCH-S5 contract: non-matching issueId must keep reason='session-failed', "
        f"got {out.get('reason')!r}"
    )
    assert out.get("reason") != "archive-failed", (
        "ARCH-S5 contract: archive override must NOT fire when issueId != archive_issue_id"
    )


# ─── ARCH-S6: archive-failed retry 用完 → escalated + after-2-retries ─────


async def test_arch_s6_retries_exhausted_final_reason(monkeypatch):
    """
    ARCH-S6: escalate 收到 body.event="archive.failed" 且 auto_retry_count=2（已用完）时，
    必须真 escalate：
    - 返回 escalated=True
    - reason = "archive-failed-after-2-retries"
    - 用 merge_tags_and_update 给 intent issue 加 reason:archive-failed-after-2-retries tag
    - 不发 follow_up_issue
    """
    from orchestrator import k8s_runner as krunner
    from orchestrator.actions import escalate as mod
    from orchestrator.store import req_state as rs

    fake = _make_bkd_fake()
    _patch_bkd_escalate(monkeypatch, fake)
    _patch_db_escalate(monkeypatch)

    class _Row:
        state = ReqState.ARCHIVING

    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_Row()))
    monkeypatch.setattr(rs, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(
        krunner,
        "get_controller",
        lambda: type("Ctrl", (), {"cleanup_runner": AsyncMock()})(),
    )

    body = _make_body(issue_id="arch-1", event="archive.failed")
    out = await mod.escalate(
        body=body,
        req_id="REQ-arch-1",
        tags=["archive"],
        ctx={
            "intent_issue_id": "intent-1",
            "archive_issue_id": "arch-1",
            "auto_retry_count": 2,
        },
    )

    assert out.get("escalated") is True, (
        f"ARCH-S6 contract: retries exhausted must set escalated=True, got {out!r}"
    )
    assert out.get("reason") == "archive-failed-after-2-retries", (
        f"ARCH-S6 contract: final reason must be 'archive-failed-after-2-retries', "
        f"got {out.get('reason')!r}"
    )
    fake.merge_tags_and_update.assert_awaited_once()
    fake.follow_up_issue.assert_not_awaited()

    # verify the tag was passed to merge_tags_and_update
    tag_arg = str(fake.merge_tags_and_update.await_args_list)
    assert "archive-failed-after-2-retries" in tag_arg, (
        f"ARCH-S6 contract: intent issue must be tagged with reason:archive-failed-after-2-retries, "
        f"merge_tags_and_update args: {tag_arg!r}"
    )
