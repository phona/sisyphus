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
            "body_event": getattr(body, "event", None),
            "tags": list(tags or []),
            "ctx": dict(ctx or {}),
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
    # 让 fast/slow 都 >=1800 且 fast 是较小值，验 min(fast, slow) 拿 1800
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_stuck_threshold_sec", 3600,
    )
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 1800,
    )

    await watchdog._tick()

    skip_arr, threshold = captured["args"]
    assert threshold == 1800
    assert "done" in skip_arr
    assert "escalated" in skip_arr
    assert "init" in skip_arr
    # REQ-watchdog-stage-policy-1777269909: human-in-loop INTAKING 进 SQL skip
    assert "intaking" in skip_arr
    # M12：pending-human state 已删，不应出现在 skip 列表
    assert "analyzing-pending-human" not in skip_arr


# ─── Case 8：watchdog_enabled=False → run_loop 立即 return 不跑循环 ─────────
@pytest.mark.asyncio
async def test_loop_disabled_returns_immediately(monkeypatch):
    monkeypatch.setattr("orchestrator.watchdog.settings.watchdog_enabled", False)
    # 无需 mock _tick / asyncio.sleep — 直接 return 不进 while
    await watchdog.run_loop()   # 不应 hang


# ─── Case ARCHIVING：state==ARCHIVING + session=failed → body.event="archive.failed" ─
@pytest.mark.asyncio
async def test_archiving_stuck_uses_archive_failed_synthetic_event(monkeypatch):
    """REQ-archive-failure-watchdog: ARCHIVING 卡死时 watchdog 贴 body.event='archive.failed'，
    让 escalate 把 reason 标成 'archive-failed' 而不是通用 'watchdog-stuck'，
    M7 04-fail-kind-distribution dashboard 才能区分 done-archive 阶段崩溃 vs 通用卡死。"""
    pool = FakePool(rows=[
        _row("REQ-arch-1", ReqState.ARCHIVING.value,
             ctx={"archive_issue_id": "arch-1", "intent_issue_id": "intent-1"}),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="failed", id="arch-1"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    assert len(step_calls) == 1
    assert step_calls[0]["cur_state"] == ReqState.ARCHIVING
    assert step_calls[0]["event"] == Event.SESSION_FAILED
    # 关键：贴的是 archive 专属 synthetic event（不是通用 watchdog.stuck）
    assert step_calls[0]["body_event"] == "archive.failed"
    assert step_calls[0]["body_issue"] == "arch-1"


# ─── Case 非 ARCHIVING 仍用通用 watchdog.stuck（确保我们没误改其他 state）─────
@pytest.mark.asyncio
async def test_non_archiving_keeps_generic_watchdog_stuck_event(monkeypatch):
    """STAGING_TEST_RUNNING 等非 ARCHIVING state 仍贴 body.event='watchdog.stuck'。
    确保 archive 细分逻辑没污染其他 state。"""
    pool = FakePool(rows=[
        _row("REQ-st-1", ReqState.STAGING_TEST_RUNNING.value,
             ctx={"staging_test_issue_id": "st-1"}),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="failed", id="st-1"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    await watchdog._tick()

    assert step_calls[0]["body_event"] == "watchdog.stuck"


# ─── REQ-watchdog-stage-policy-1777269909: INTAKING 完全豁免 watchdog ─────────
# 之前用 intake-no-result-tag 专属路径区分 "agent 忘 tag" vs "用户在思考"；实际
# 两者从 watchdog 视角不可分（都是 session ended + tags 缺 result:*），现在退化
# "human-in-loop 不杀"，由 SQL 预过滤直接吞 INTAKING 行。

@pytest.mark.asyncio
async def test_intaking_state_excluded_from_sql_fetch(monkeypatch):
    """WSP-S5：watchdog SQL skip 数组应含 'intaking'，预过滤把 INTAKING 行排除。"""
    captured: dict = {}

    class _CapturingPool:
        async def fetch(self, sql, *args):
            captured["args"] = args
            return []

    _patch_pool(monkeypatch, _CapturingPool())

    await watchdog._tick()

    skip_arr, _threshold = captured["args"]
    assert "intaking" in skip_arr
    # 验证 _NO_WATCHDOG_STATES 集合本身
    assert ReqState.INTAKING in watchdog._NO_WATCHDOG_STATES


def test_no_watchdog_states_set_membership():
    """_NO_WATCHDOG_STATES 列表 = 所有 human-in-loop state（无 BKD agent / 等用户）。

    INTAKING（REQ-watchdog-stage-policy-1777269909, 等用户在 BKD chat 多轮澄清）+
    PENDING_USER_REVIEW（REQ-bkd-acceptance-feedback-loop-1777278984, 等用户改 BKD
    intent issue statusId 表态验收）。新增同类 state 时记得补本测试 + spec。
    """
    assert watchdog._NO_WATCHDOG_STATES == frozenset({
        ReqState.INTAKING,
        ReqState.PENDING_USER_REVIEW,
    })


@pytest.mark.asyncio
async def test_pending_user_review_excluded_from_sql_fetch(monkeypatch):
    """USR-WD1: watchdog SQL skip 数组含 'pending-user-review'，行不会被 watchdog 扫到。"""
    captured: dict = {}

    class _CapturingPool:
        async def fetch(self, sql, *args):
            captured["args"] = args
            return []

    _patch_pool(monkeypatch, _CapturingPool())

    await watchdog._tick()

    skip_arr, _threshold = captured["args"]
    assert "pending-user-review" in skip_arr
    assert ReqState.PENDING_USER_REVIEW in watchdog._NO_WATCHDOG_STATES


def test_intake_no_result_tag_machinery_removed():
    """REQ-watchdog-stage-policy-1777269909 删除了 intake-no-result-tag 全部死代码。"""
    assert not hasattr(watchdog, "_INTAKE_RESULT_TAGS")
    assert not hasattr(watchdog, "_INTAKE_NO_RESULT_EVENT")
    assert not hasattr(watchdog, "_INTAKE_NO_RESULT_REASON")
    assert not hasattr(watchdog, "_is_intake_no_result_tag")
    # _STATE_ISSUE_KEY 不再 dispatch INTAKING（SQL 已先过滤）
    assert ReqState.INTAKING not in watchdog._STATE_ISSUE_KEY


def test_session_end_signals_no_longer_lists_intake_no_result_tag():
    """WSP-S6：escalate._SESSION_END_SIGNALS 移除 watchdog.intake_no_result_tag
    死字符串（watchdog 不再 emit 它），其他 canonical 信号保留。"""
    from orchestrator.actions.escalate import _SESSION_END_SIGNALS

    assert "watchdog.intake_no_result_tag" not in _SESSION_END_SIGNALS
    assert "session.failed" in _SESSION_END_SIGNALS
    assert "watchdog.stuck" in _SESSION_END_SIGNALS
    assert "archive.failed" in _SESSION_END_SIGNALS


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


# ─── REQ-bkd-analyze-hang-debug-1777247423: ended-session fast lane ─────────
# 拆出 fast (300s) / slow (3600s) 双阈值后的行为矩阵，对应 spec 场景 WFD-S1..S6。

@pytest.mark.asyncio
async def test_sql_filter_uses_min_of_fast_and_slow_thresholds(monkeypatch):
    """WFD-S1：fast=300 / slow=3600 → SQL 用 300。"""
    captured: dict = {}

    class _CapturingPool:
        async def fetch(self, sql, *args):
            captured["args"] = args
            return []

    _patch_pool(monkeypatch, _CapturingPool())
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 300,
    )
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_stuck_threshold_sec", 3600,
    )

    await watchdog._tick()

    _skip_arr, threshold = captured["args"]
    assert threshold == 300


@pytest.mark.asyncio
async def test_sql_filter_picks_slow_when_smaller(monkeypatch):
    """WFD-S2：operator 把 fast 调到 1800（>slow=600）→ SQL 仍用较小的 600。
    保证拆双阈值后旧 helm values（仅设 stuck）行为可控。"""
    captured: dict = {}

    class _CapturingPool:
        async def fetch(self, sql, *args):
            captured["args"] = args
            return []

    _patch_pool(monkeypatch, _CapturingPool())
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 1800,
    )
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_stuck_threshold_sec", 600,
    )

    await watchdog._tick()

    _skip_arr, threshold = captured["args"]
    assert threshold == 600


@pytest.mark.asyncio
async def test_ended_session_at_fast_threshold_escalates(monkeypatch):
    """WFD-S3：BKD 报 session=failed + stuck_sec=305（刚过 fast 300） → 立即 escalate。
    这是本 REQ 的核心 fix —— 旧行为要等 stuck_sec >= 3600 才 escalate。"""
    pool = FakePool(rows=[
        _row("REQ-fast", ReqState.ANALYZING.value,
             ctx={"intent_issue_id": "intent-fast"},
             stuck_sec=305),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="failed", id="intent-fast"))
    step_calls = _patch_engine(monkeypatch)
    art_calls = _patch_artifact(monkeypatch)
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 300,
    )
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_stuck_threshold_sec", 3600,
    )

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    assert len(step_calls) == 1
    assert step_calls[0]["event"] == Event.SESSION_FAILED
    assert step_calls[0]["body_event"] == "watchdog.stuck"
    assert step_calls[0]["cur_state"] == ReqState.ANALYZING
    assert len(art_calls) == 1
    assert art_calls[0]["stage"] == "watchdog:analyzing"


@pytest.mark.asyncio
async def test_running_session_above_fast_threshold_still_skips(monkeypatch):
    """WFD-S6：BKD 报 session=running + stuck_sec=305 → skip（不受 fast lane 影响）。
    fast lane 仅对 ended session 生效；in-loop still_running 检查继续保护长尾真分析。"""
    pool = FakePool(rows=[
        _row("REQ-run-fast", ReqState.ANALYZING.value,
             ctx={"intent_issue_id": "intent-run"},
             stuck_sec=305),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="running", id="intent-run"))
    step_calls = _patch_engine(monkeypatch)
    art_calls = _patch_artifact(monkeypatch)
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 300,
    )
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_stuck_threshold_sec", 3600,
    )

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 0}
    assert step_calls == []
    assert art_calls == []


@pytest.mark.asyncio
async def test_running_session_above_slow_threshold_still_skips(monkeypatch):
    """WFD-S5：BKD 报 session=running + stuck_sec=5000（远超 slow=3600） → 仍 skip。
    保留现有行为 —— BKD 报 running 时无条件信任，不主动 kill 长尾分析。"""
    pool = FakePool(rows=[
        _row("REQ-long-run", ReqState.ANALYZING.value,
             ctx={"intent_issue_id": "intent-long"},
             stuck_sec=5000),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, FakeIssue(session_status="running", id="intent-long"))
    step_calls = _patch_engine(monkeypatch)
    art_calls = _patch_artifact(monkeypatch)
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 300,
    )
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_stuck_threshold_sec", 3600,
    )

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 0}
    assert step_calls == []
    assert art_calls == []


def test_settings_default_session_ended_threshold_is_300():
    """WFD-S7：settings 默认 watchdog_session_ended_threshold_sec=300（5min）。"""
    from orchestrator.config import Settings

    s = Settings(
        bkd_token="x", webhook_token="x", pg_dsn="postgresql://x:x@x/x",  # type: ignore[call-arg]
    )
    assert s.watchdog_session_ended_threshold_sec == 300
    # legacy 阈值默认未变
    assert s.watchdog_stuck_threshold_sec == 3600


def test_settings_session_ended_threshold_env_override(monkeypatch):
    """WFD-S8：SISYPHUS_WATCHDOG_SESSION_ENDED_THRESHOLD_SEC=120 override 默认。"""
    from orchestrator.config import Settings

    monkeypatch.setenv("SISYPHUS_WATCHDOG_SESSION_ENDED_THRESHOLD_SEC", "120")

    s = Settings(
        bkd_token="x", webhook_token="x", pg_dsn="postgresql://x:x@x/x",  # type: ignore[call-arg]
    )
    assert s.watchdog_session_ended_threshold_sec == 120
