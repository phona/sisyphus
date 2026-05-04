"""Contract tests for REQ-feat-watchdog-self-heal-1777723955.

Black-box behavioral contracts:
  SH-S1   session ended + no result tag + stage needs result tag → follow-up self-heal
  SH-S2   session ended + has result tag → normal escalate (not self-heal)
  SH-S3   session ended + no result tag + stage does NOT need result tag → normal escalate
  SH-S4   self-heal follow_up fails → fall through to normal escalate
  SH-S5   pr-ci stage with pr-ci:timeout tag → normal escalate
  SH-S6   session still running → skip regardless of result tags
  SH-S7   BKD lookup fails + stage needs result tag → normal escalate
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

# ─── shared fakes ──────────────────────────────────────────────────────────


@dataclass
class _FakePool:
    rows: list = field(default_factory=list)
    fetch_calls: list = field(default_factory=list)
    executed: list = field(default_factory=list)

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self.rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


@dataclass
class _FakeIssue:
    session_status: str | None = "completed"
    id: str = "issue-1"
    project_id: str = "proj-1"
    issue_number: int = 0
    title: str = ""
    status_id: str = "todo"
    tags: list = field(default_factory=list)


def _make_row(state: str, *, req_id="REQ-x", project_id="proj-1",
              ctx: dict | None = None, stuck_sec: int = 7200):
    return {
        "req_id": req_id,
        "project_id": project_id,
        "state": state,
        "context": json.dumps(ctx or {}),
        "stuck_sec": stuck_sec,
    }


def _patch_pool(monkeypatch, pool):
    monkeypatch.setattr("orchestrator.watchdog.db.get_pool", lambda: pool)


def _patch_bkd(monkeypatch, issue, follow_up_side_effect=None):
    fake = AsyncMock()
    fake.get_issue = AsyncMock(return_value=issue)
    fake.follow_up_issue = AsyncMock(side_effect=follow_up_side_effect)

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    monkeypatch.setattr("orchestrator.watchdog.BKDClient", _ctx)
    return fake


def _patch_engine(monkeypatch):
    calls: list = []

    async def fake_step(pool, *, body, req_id, project_id, tags, cur_state,
                        ctx, event, depth=0):
        calls.append({
            "req_id": req_id,
            "cur_state": cur_state,
            "event": event,
            "body_event": getattr(body, "event", None),
        })
        return {}

    monkeypatch.setattr("orchestrator.watchdog.engine.step", fake_step)
    return calls


def _patch_artifact(monkeypatch):
    calls: list = []

    async def fake_insert(pool, req_id, stage, result):
        calls.append({"req_id": req_id, "stage": stage, "result": result})

    monkeypatch.setattr(
        "orchestrator.watchdog.artifact_checks.insert_check", fake_insert,
    )
    return calls


# ─── SH-S1: session ended + no result tag → self-heal ───────────────────────


@pytest.mark.asyncio
async def test_s1_self_heal_no_result_tag(monkeypatch):
    """SH-S1: CHALLENGER_RUNNING + session=completed + no result tag → follow-up。"""
    from orchestrator import watchdog
    from orchestrator.state import ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.CHALLENGER_RUNNING.value,
            req_id="REQ-sh1",
            ctx={"challenger_issue_id": "ch-sh1"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    fake_bkd = _patch_bkd(
        monkeypatch,
        _FakeIssue(session_status="completed", id="ch-sh1", tags=["challenger"]),
    )
    step_calls = _patch_engine(monkeypatch)
    art_calls = _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 0}, f"got {result}"
    fake_bkd.follow_up_issue.assert_awaited_once()
    assert step_calls == []
    assert art_calls == []


# ─── SH-S2: session ended + has result tag → normal escalate ────────────────


@pytest.mark.asyncio
async def test_s2_has_result_tag_escalates(monkeypatch):
    """SH-S2: STAGING_TEST_RUNNING + result:fail → escalate，不 follow-up。"""
    from orchestrator import watchdog
    from orchestrator.state import Event, ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.STAGING_TEST_RUNNING.value,
            req_id="REQ-sh2",
            ctx={"staging_test_issue_id": "st-sh2"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    fake_bkd = _patch_bkd(
        monkeypatch,
        _FakeIssue(session_status="completed", id="st-sh2",
                   tags=["staging-test", "result:fail"]),
    )
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    fake_bkd.follow_up_issue.assert_not_called()
    assert len(step_calls) == 1
    assert step_calls[0]["event"] == Event.SESSION_FAILED


# ─── SH-S3: stage does NOT need result tag → normal escalate ────────────────


@pytest.mark.asyncio
async def test_s3_analyze_no_result_tag_escalates(monkeypatch):
    """SH-S3: EXECUTING 不在 _STAGES_NEEDING_RESULT_TAG 中，session=completed → escalate。"""
    from orchestrator import watchdog
    from orchestrator.state import Event, ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.EXECUTING.value,
            req_id="REQ-sh3",
            ctx={"intent_issue_id": "intent-sh3"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    fake_bkd = _patch_bkd(
        monkeypatch,
        _FakeIssue(session_status="completed", id="intent-sh3", tags=["execute"]),
    )
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    fake_bkd.follow_up_issue.assert_not_called()
    assert step_calls[0]["event"] == Event.SESSION_FAILED


# ─── SH-S4: follow_up fails → fall through to escalate ──────────────────────


@pytest.mark.asyncio
async def test_s4_follow_up_failure_falls_through(monkeypatch):
    """SH-S4: follow_up_issue 抛异常 → 继续走正常 escalate。"""
    from orchestrator import watchdog
    from orchestrator.state import Event, ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.FIXER_RUNNING.value,
            req_id="REQ-sh4",
            ctx={"fixer_issue_id": "fx-sh4"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    fake_bkd = _patch_bkd(
        monkeypatch,
        _FakeIssue(session_status="completed", id="fx-sh4", tags=["fixer"]),
        follow_up_side_effect=RuntimeError("BKD 502"),
    )
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    fake_bkd.follow_up_issue.assert_awaited_once()
    assert step_calls[0]["event"] == Event.SESSION_FAILED


# ─── SH-S5: pr-ci with pr-ci:timeout tag → escalate ─────────────────────────


@pytest.mark.asyncio
async def test_s5_pr_ci_with_timeout_tag_escalates(monkeypatch):
    """SH-S5: PR_CI_RUNNING + pr-ci:timeout → 有 result tag，正常 escalate。"""
    from orchestrator import watchdog
    from orchestrator.state import ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.PR_CI_RUNNING.value,
            req_id="REQ-sh5",
            ctx={"pr_ci_watch_issue_id": "ci-sh5"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    fake_bkd = _patch_bkd(
        monkeypatch,
        _FakeIssue(session_status="completed", id="ci-sh5",
                   tags=["pr-ci", "pr-ci:timeout"]),
    )
    _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    fake_bkd.follow_up_issue.assert_not_called()


# ─── SH-S6: session running → skip regardless ───────────────────────────────


@pytest.mark.asyncio
async def test_s6_running_session_skips(monkeypatch):
    """SH-S6: session=running → skip，不检查 result tag。"""
    from orchestrator import watchdog
    from orchestrator.state import ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.CHALLENGER_RUNNING.value,
            req_id="REQ-sh6",
            ctx={"challenger_issue_id": "ch-sh6"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    fake_bkd = _patch_bkd(
        monkeypatch,
        _FakeIssue(session_status="running", id="ch-sh6", tags=["challenger"]),
    )
    step_calls = _patch_engine(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 0}
    fake_bkd.follow_up_issue.assert_not_called()
    assert step_calls == []


# ─── SH-S7: BKD lookup fails → escalate ─────────────────────────────────────


@pytest.mark.asyncio
async def test_s7_bkd_lookup_fails_escalates(monkeypatch):
    """SH-S7: BKD get_issue 抛异常 → issue=None → fall through escalate。"""
    from orchestrator import watchdog
    from orchestrator.state import Event, ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.ACCEPT_RUNNING.value,
            req_id="REQ-sh7",
            ctx={"accept_issue_id": "ac-sh7"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    fake = AsyncMock()
    fake.get_issue = AsyncMock(side_effect=RuntimeError("BKD 404"))
    fake.follow_up_issue = AsyncMock()

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    monkeypatch.setattr("orchestrator.watchdog.BKDClient", _ctx)
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    fake.follow_up_issue.assert_not_called()
    assert step_calls[0]["event"] == Event.SESSION_FAILED
