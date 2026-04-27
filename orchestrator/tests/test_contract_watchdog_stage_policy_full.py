"""Contract tests for REQ-stage-watchdog-policy-full-1777280786.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-stage-watchdog-policy-full-1777280786/specs/watchdog-stage-policy-full/spec.md

Scenarios:
  WSPF-S1   INTAKING (human-loop) excluded by SQL pre-filter
  WSPF-S2   deterministic-checker stage with ended session escalates after ended_sec
  WSPF-S3   autonomous-bounded running session with stuck_sec=None NOT escalated
  WSPF-S3b  STAGING_TEST_RUNNING running session NOT escalated regardless of duration
  WSPF-S4   autonomous-bounded ended session escalates after ended_sec
  WSPF-S5   external-poll running under stuck_sec NOT escalated
  WSPF-S6   external-poll running over stuck_sec IS escalated
  WSPF-S7   unmapped state falls back to global watchdog thresholds
  WSPF-S8   SQL pre-filter threshold is min over all configured policy windows
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
    session_status: str | None = "failed"
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


def _patch_bkd(monkeypatch, issue: _FakeIssue | None):
    fake = AsyncMock()
    fake.get_issue = AsyncMock(return_value=issue)

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


# ─── WSPF-S1: INTAKING excluded by SQL pre-filter ───────────────────────────


@pytest.mark.asyncio
async def test_s1_intaking_excluded_by_sql_prefilter(monkeypatch):
    """WSPF-S1: _STAGE_POLICY[INTAKING] is None → SQL skip_arr contains 'intaking'."""
    from orchestrator import watchdog
    from orchestrator.state import ReqState

    pool = _FakePool(rows=[])
    _patch_pool(monkeypatch, pool)

    await watchdog._tick()

    assert pool.fetch_calls, "watchdog must issue at least one SQL fetch"
    skip_arr, _threshold = pool.fetch_calls[0][1]
    assert "intaking" in skip_arr, (
        f"WSPF-S1: SQL skip_arr must include 'intaking'. Got: {skip_arr!r}"
    )
    # _STAGE_POLICY 表里 INTAKING 必须显式标 None
    assert watchdog._STAGE_POLICY[ReqState.INTAKING] is None
    # 派生的 _NO_WATCHDOG_STATES 必须包含 INTAKING
    assert ReqState.INTAKING in watchdog._NO_WATCHDOG_STATES


# ─── WSPF-S2: deterministic-checker stage with ended session escalates ──────


@pytest.mark.asyncio
async def test_s2_deterministic_checker_ended_session_escalates(monkeypatch):
    """WSPF-S2: SPEC_LINT_RUNNING + stuck=400 (≥ ended_sec=300) + no BKD issue → escalate."""
    from orchestrator import watchdog
    from orchestrator.state import Event, ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.SPEC_LINT_RUNNING.value,
            req_id="REQ-s2",
            ctx={"intent_issue_id": "intent-s2"},
            stuck_sec=400,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    fake_bkd = _patch_bkd(monkeypatch, _FakeIssue(session_status="running"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}, f"got {result}"
    # SPEC_LINT_RUNNING 在 _STATE_ISSUE_KEY 中是 None → 跳过 BKD 查询
    fake_bkd.get_issue.assert_not_called()
    assert len(step_calls) == 1
    assert step_calls[0]["event"] == Event.SESSION_FAILED
    assert step_calls[0]["cur_state"] == ReqState.SPEC_LINT_RUNNING
    assert step_calls[0]["body_event"] == "watchdog.stuck"


# ─── WSPF-S3: autonomous-bounded running with stuck_sec=None NOT escalated ──


@pytest.mark.asyncio
async def test_s3_autonomous_bounded_running_not_escalated(monkeypatch):
    """WSPF-S3: ANALYZING + session=running + 任意长 stuck → policy.stuck_sec=None → skip."""
    from orchestrator import watchdog
    from orchestrator.state import ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.ANALYZING.value,
            req_id="REQ-s3",
            ctx={"intent_issue_id": "intent-s3"},
            stuck_sec=10000,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, _FakeIssue(session_status="running"))
    step_calls = _patch_engine(monkeypatch)
    art_calls = _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 0}
    assert step_calls == []
    assert art_calls == []
    # 验证 policy 形状
    policy = watchdog._STAGE_POLICY[ReqState.ANALYZING]
    assert policy is not None and policy.stuck_sec is None


# ─── WSPF-S3b: STAGING_TEST_RUNNING running session NOT escalated ───────────


@pytest.mark.asyncio
async def test_s3b_staging_test_running_not_escalated(monkeypatch):
    """WSPF-S3b: STAGING_TEST_RUNNING 名义是 deterministic-checker 但 stuck_sec=None
    保留长跑测试套件不被 watchdog 杀的语义。"""
    from orchestrator import watchdog
    from orchestrator.state import ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.STAGING_TEST_RUNNING.value,
            req_id="REQ-s3b",
            ctx={"staging_test_issue_id": "st-s3b"},
            stuck_sec=20000,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, _FakeIssue(session_status="running", id="st-s3b"))
    step_calls = _patch_engine(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 0}
    assert step_calls == []
    policy = watchdog._STAGE_POLICY[ReqState.STAGING_TEST_RUNNING]
    assert policy is not None and policy.stuck_sec is None


# ─── WSPF-S4: autonomous-bounded ended session escalates after ended_sec ────


@pytest.mark.asyncio
async def test_s4_autonomous_bounded_ended_session_escalates(monkeypatch):
    """WSPF-S4: ANALYZING + stuck=320 (≥ ended_sec=300) + session=failed → escalate."""
    from orchestrator import watchdog
    from orchestrator.state import Event, ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.ANALYZING.value,
            req_id="REQ-s4",
            ctx={"intent_issue_id": "intent-s4"},
            stuck_sec=320,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, _FakeIssue(session_status="failed", id="intent-s4"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    assert len(step_calls) == 1
    assert step_calls[0]["event"] == Event.SESSION_FAILED
    assert step_calls[0]["cur_state"] == ReqState.ANALYZING


# ─── WSPF-S5: external-poll running under stuck_sec NOT escalated ───────────


@pytest.mark.asyncio
async def test_s5_external_poll_under_cap_not_escalated(monkeypatch):
    """WSPF-S5: PR_CI_RUNNING + stuck=3600 < stuck_sec=14400 + session=running → skip."""
    from orchestrator import watchdog
    from orchestrator.state import ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.PR_CI_RUNNING.value,
            req_id="REQ-s5",
            ctx={"pr_ci_watch_issue_id": "ci-s5"},
            stuck_sec=3600,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, _FakeIssue(session_status="running", id="ci-s5"))
    step_calls = _patch_engine(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 0}
    assert step_calls == []
    policy = watchdog._STAGE_POLICY[ReqState.PR_CI_RUNNING]
    assert policy is not None and policy.stuck_sec == 14400


# ─── WSPF-S6: external-poll running over stuck_sec IS escalated ─────────────


@pytest.mark.asyncio
async def test_s6_external_poll_over_cap_escalates(monkeypatch):
    """WSPF-S6: PR_CI_RUNNING + stuck=15000 ≥ stuck_sec=14400 + session=running → escalate."""
    from orchestrator import watchdog
    from orchestrator.state import Event, ReqState

    pool = _FakePool(rows=[
        _make_row(
            ReqState.PR_CI_RUNNING.value,
            req_id="REQ-s6",
            ctx={"pr_ci_watch_issue_id": "ci-s6"},
            stuck_sec=15000,
        ),
    ])
    _patch_pool(monkeypatch, pool)
    _patch_bkd(monkeypatch, _FakeIssue(session_status="running", id="ci-s6"))
    step_calls = _patch_engine(monkeypatch)
    _patch_artifact(monkeypatch)

    result = await watchdog._tick()

    assert result == {"checked": 1, "escalated": 1}
    assert len(step_calls) == 1
    assert step_calls[0]["event"] == Event.SESSION_FAILED
    assert step_calls[0]["cur_state"] == ReqState.PR_CI_RUNNING
    assert step_calls[0]["body_event"] == "watchdog.stuck"


# ─── WSPF-S7: unmapped state falls back to global thresholds ────────────────


def test_s7_unmapped_state_falls_back_to_global_thresholds(monkeypatch):
    """WSPF-S7: 不在 _STAGE_POLICY 的 state → fallback _StagePolicy(ended=settings, stuck=settings)."""
    from orchestrator import watchdog

    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 300,
    )
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_stuck_threshold_sec", 3600,
    )

    # 用一个肯定不在 _STAGE_POLICY 表的"未来 state"占位（直接传字符串绕过 enum）
    class _Fake:
        pass

    fake_state = _Fake()  # not equal to any ReqState member → not in _STAGE_POLICY
    resolved = watchdog._resolve_policy(fake_state)
    assert resolved is not None
    assert resolved.ended_sec == 300
    assert resolved.stuck_sec == 3600


# ─── WSPF-S8: SQL pre-filter threshold is min over all policy windows ───────


@pytest.mark.asyncio
async def test_s8_sql_threshold_is_min_over_policy_windows(monkeypatch):
    """WSPF-S8: SQL threshold ≤ min(ended_sec across all policies)."""
    from orchestrator import watchdog

    pool = _FakePool(rows=[])
    _patch_pool(monkeypatch, pool)
    # 把全局阈值故意调高，验证 SQL threshold 还是被 stage policy 拉低
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_session_ended_threshold_sec", 9000,
    )
    monkeypatch.setattr(
        "orchestrator.watchdog.settings.watchdog_stuck_threshold_sec", 9000,
    )

    await watchdog._tick()

    assert pool.fetch_calls
    _skip_arr, threshold = pool.fetch_calls[0][1]
    # 表里至少有一条 ended_sec=300 → threshold 必须 ≤ 300
    assert threshold <= 300, (
        f"WSPF-S8: SQL threshold should be ≤ 300 (min ended_sec across stage policies), "
        f"got {threshold}"
    )
