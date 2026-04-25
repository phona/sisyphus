"""watchdog 单测：mock PG fetch + BKD get_issue + engine.step，
验不同 stuck row 的分流（escalate / skip / session-running）。"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from orchestrator import watchdog
from orchestrator.state import Event, ReqState


# ─── Fake pool（只实现 fetch + execute，watchdog 用到这两）──────────────────
@dataclass
class FakePool:
    rows: list = field(default_factory=list)
    executed: list = field(default_factory=list)

    async def fetch(self, sql, *args):
        return self.rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return None


def _row(req_id, state, ctx=None, stuck_sec=2000):
    return {
        "req_id": req_id,
        "project_id": "proj-1",
        "state": state,
        "context": json.dumps(ctx or {}),
        "stuck_sec": stuck_sec,
    }


@dataclass
class FakeIssue:
    session_status: str | None = "failed"
    id: str = "dev-1"
    project_id: str = "proj-1"
    issue_number: int = 0
    title: str = ""
    status_id: str = "todo"
    tags: list = field(default_factory=list)


def _patch_bkd(monkeypatch, issue: FakeIssue | None, side_effect: Exception | None = None):
    fake = AsyncMock()
    if side_effect:
        fake.get_issue = AsyncMock(side_effect=side_effect)
    else:
        fake.get_issue = AsyncMock(return_value=issue)

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    monkeypatch.setattr("orchestrator.watchdog.BKDClient", _ctx)
    return fake


def _patch_pool(monkeypatch, pool):
    monkeypatch.setattr("orchestrator.watchdog.db.get_pool", lambda: pool)


def _patch_engine(monkeypatch):
    """捕获 engine.step 调用，不真推状态机（避免依赖 actions）。"""
    calls: list = []

    async def fake_step(pool, *, body, req_id, project_id, tags, cur_state, ctx, event, depth=0):
        calls.append({
            "req_id": req_id,
            "project_id": project_id,
            "cur_state": cur_state,
            "event": event,
            "body_issue": getattr(body, "issueId", None),
            "body_proj": getattr(body, "projectId", None),
        })
        return {"action": "escalate", "next_state": "escalated"}

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


# ─── Case 1：session=failed → escalate（写 artifact + engine.step SESSION_FAILED）
@pytest.mark.asyncio
async def test_stuck_with_failed_session_escalates(monkeypatch):
    pool = FakePool(rows=[
        _row("REQ-1", ReqState.STAGING_TEST_RUNNING.value,
             ctx={"staging_test_issue_id": "st-1", "intent_issue_id": "intent-1"}),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="failed", id="st-1"))
    step_calls = _patch_engine(monkeypatch)
    art_calls = _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    # engine 被调且是 SESSION_FAILED
    assert len(step_calls) == 1
    assert step_calls[0]["event"] == Event.SESSION_FAILED
    assert step_calls[0]["cur_state"] == ReqState.STAGING_TEST_RUNNING
    assert step_calls[0]["body_issue"] == "st-1"
    assert step_calls[0]["body_proj"] == "proj-1"
    # artifact_checks 写了一笔 stage=watchdog:staging-test-running
    assert len(art_calls) == 1
    assert art_calls[0]["stage"] == "watchdog:staging-test-running"
    assert art_calls[0]["result"].passed is False
    assert art_calls[0]["result"].reason == "watchdog_stuck"


# ─── Case 2：session=running → skip，不 escalate
@pytest.mark.asyncio
async def test_stuck_but_session_running_skips(monkeypatch):
    pool = FakePool(rows=[
        _row("REQ-2", ReqState.STAGING_TEST_RUNNING.value,
             ctx={"staging_test_issue_id": "st-2"}),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="running", id="st-2"))
    step_calls = _patch_engine(monkeypatch)
    art_calls = _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 0}
    assert step_calls == []
    assert art_calls == []


# ─── Case 3：BKD get_issue 抛异常 → 保守 escalate
@pytest.mark.asyncio
async def test_stuck_bkd_lookup_fails_escalates(monkeypatch):
    pool = FakePool(rows=[
        _row("REQ-3", ReqState.STAGING_TEST_RUNNING.value,
             ctx={"staging_test_issue_id": "st-3"}),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, None, side_effect=RuntimeError("404 not found"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    assert len(step_calls) == 1
    assert step_calls[0]["event"] == Event.SESSION_FAILED


# ─── Case 4：state 无 issue_key 映射（M15 objective checker）→ 不查 BKD 直接 escalate
@pytest.mark.asyncio
async def test_spec_lint_escalates_without_bkd_lookup(monkeypatch):
    """M15 spec-lint 是 orchestrator 驱动的 objective checker，无关联 BKD issue。"""
    pool = FakePool(rows=[
        _row("REQ-4", ReqState.SPEC_LINT_RUNNING.value,
             ctx={"intent_issue_id": "intent-4"}),
    ])
    _patch_pool(monkeypatch, pool)
    fake_bkd = _patch_bkd(monkeypatch, FakeIssue(session_status="running"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    # spec-lint 无 issue_key → 不查 BKD
    fake_bkd.get_issue.assert_not_called()
    assert len(step_calls) == 1
    # body.issueId 回落到 intent_issue_id
    assert step_calls[0]["body_issue"] == "intent-4"


# ─── Case 5：ctx 里没 issue_id（比如 create_dev 还没落 ctx 就挂了）→ escalate
@pytest.mark.asyncio
async def test_missing_issue_id_in_ctx_escalates(monkeypatch):
    """stage 有 issue_key 但 ctx 里缺少该 issue_id → 无法查 BKD，保守 escalate。"""
    pool = FakePool(rows=[
        _row("REQ-5", ReqState.STAGING_TEST_RUNNING.value, ctx={}),
    ])
    _patch_pool(monkeypatch, pool)
    fake_bkd = _patch_bkd(monkeypatch, FakeIssue(session_status="running"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    # 无 issue_id 不查
    fake_bkd.get_issue.assert_not_called()
    assert len(step_calls) == 1


# ─── Case 6：SQL 过滤（未到阈值的不返回）由 DB 负责 — 空 rows 直接 0
@pytest.mark.asyncio
async def test_no_stuck_rows_does_nothing(monkeypatch):
    pool = FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue())
    step_calls = _patch_engine(monkeypatch)
    art_calls = _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 0, "escalated": 0}
    assert step_calls == []
    assert art_calls == []


# ─── Case 7：SQL 参数正确下发（_SKIP_STATES 含终态 + init）────
@pytest.mark.asyncio
async def test_tick_passes_skip_states_and_threshold_to_sql(monkeypatch):
    captured: dict = {}

    class _CapturingPool:
        async def fetch(self, sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

    _patch_pool(monkeypatch, _CapturingPool())
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_stuck_threshold_sec", 1800,
    )

    await watchdog._tick()

    skip_arr, threshold = captured["args"]
    assert threshold == 1800
    assert "done" in skip_arr
    assert "escalated" in skip_arr
    assert "init" in skip_arr
    # M12：pending-human state 已删，不应出现在 skip 列表
    assert "analyzing-pending-human" not in skip_arr


# ─── Case 8：watchdog_enabled=False → run_loop 立即 return 不跑循环 ─────────
@pytest.mark.asyncio
async def test_loop_disabled_returns_immediately(monkeypatch):
    monkeypatch.setattr("orchestrator.watchdog.settings.watchdog_enabled", False)
    # 无需 mock _tick / asyncio.sleep — 直接 return 不进 while
    await watchdog.run_loop()   # 不应 hang


# ─── Case 9b：FIXER_RUNNING + fixer_round 已到 cap → 标 escalated_reason=fixer-round-cap ─
@pytest.mark.asyncio
async def test_fixer_round_cap_marks_reason(monkeypatch):
    """defense in depth：start_fixer 写完 ctx.fixer_round 后挂掉 / engine.step 失败
    留下孤儿 FIXER_RUNNING；watchdog 30 min 后扫到，发现 round 已达 cap → 把
    escalated_reason 标 fixer-round-cap，escalate.py 会识别为 hard reason 不
    auto-resume + tag intent issue reason:fixer-round-cap。
    """
    pool = FakePool(rows=[
        _row("REQ-FX", ReqState.FIXER_RUNNING.value,
             ctx={
                 "fixer_issue_id": "fix-9",
                 "fixer_round": 5,
                 "intent_issue_id": "intent-fx",
             }),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="failed", id="fix-9"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    update_calls: list = []

    async def fake_update(pool, req_id, patch):
        update_calls.append((req_id, patch))

    monkeypatch.setattr("orchestrator.watchdog.req_state.update_context", fake_update)
    # 显式锁 cap 默认为 5（防 helm values 覆盖污染测试）
    monkeypatch.setattr("orchestrator.watchdog.settings.fixer_round_cap", 5)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    # 写了 escalated_reason=fixer-round-cap
    assert any(
        p.get("escalated_reason") == "fixer-round-cap" for _, p in update_calls
    )
    # 仍走 SESSION_FAILED 推到 escalate
    assert step_calls[0]["event"] == Event.SESSION_FAILED


@pytest.mark.asyncio
async def test_fixer_round_below_cap_does_not_mark(monkeypatch):
    """FIXER_RUNNING + fixer_round < cap → 不写 fixer-round-cap，走原 watchdog-stuck 路径。"""
    pool = FakePool(rows=[
        _row("REQ-FX", ReqState.FIXER_RUNNING.value,
             ctx={"fixer_issue_id": "fix-9", "fixer_round": 2,
                  "intent_issue_id": "intent-fx"}),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="failed", id="fix-9"))
    _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    update_calls: list = []

    async def fake_update(pool, req_id, patch):
        update_calls.append((req_id, patch))

    monkeypatch.setattr("orchestrator.watchdog.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.watchdog.settings.fixer_round_cap", 5)

    await watchdog._tick()

    assert not any(
        p.get("escalated_reason") == "fixer-round-cap" for _, p in update_calls
    )


# ─── Case 9：engine.step 抛异常不阻塞后续 row ─────────────────────────────
@pytest.mark.asyncio
async def test_engine_step_failure_isolated(monkeypatch):
    """engine.step 对某行抛异常不阻塞后续行处理（fault isolation）。"""
    pool = FakePool(rows=[
        _row("REQ-A", ReqState.STAGING_TEST_RUNNING.value, ctx={"staging_test_issue_id": "st-a"}),
        _row("REQ-B", ReqState.STAGING_TEST_RUNNING.value, ctx={"staging_test_issue_id": "st-b"}),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="failed"))
    _patch_artifact(monkeypatch)

    calls: list = []

    async def flaky_step(pool, **kw):
        calls.append(kw["req_id"])
        if kw["req_id"] == "REQ-A":
            raise RuntimeError("downstream boom")
        return {}

    monkeypatch.setattr("orchestrator.watchdog.engine.step", flaky_step)

    result = await watchdog._tick()

    # 两行都被处理，但只有 REQ-B 成功 escalate（REQ-A engine.step 抛异常返 False）
    assert result["checked"] == 2
    assert result["escalated"] == 1
    assert calls == ["REQ-A", "REQ-B"]
