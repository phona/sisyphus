"""Verifier resume webhook fix v2 (REQ-fix-webhook-resume-380-v2-1777866642).

Covers spec scenarios in
  openspec/changes/REQ-fix-webhook-resume-380-v2-1777866642/specs/orch-webhook-verifier-resume/spec.md

Scenarios:
  VWR-S1  same-executionId redelivery on verifier issue while REVIEW_RUNNING bypasses dedup
  VWR-S2  stale redelivery after state advances continues to skip
  VWR-S3  dedup status observability event always emitted
  VWR-S4  admin retrigger-verifier reads BKD chat and emits VERIFY_PASS
  VWR-S5  admin retrigger-verifier returns 422 when decision cannot be parsed
"""
from __future__ import annotations

import json
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from orchestrator import admin as admin_mod
from orchestrator import engine, webhook
from orchestrator import router as router_lib
from orchestrator.state import Event, ReqState
from orchestrator.store import db, dedup
from orchestrator.store import req_state as rs_mod
from orchestrator.store import verifier_decisions as vd_mod


class _FakePool:
    async def fetchrow(self, *a, **kw):
        return None

    async def execute(self, *a, **kw):
        return None


class _Row:
    """req_state row stub with a writable state attribute."""
    def __init__(self, state: ReqState, project_id: str = "proj-vwr",
                 context: dict | None = None):
        self.state = state
        self.project_id = project_id
        self.context = context or {}


def _make_request(
    *, event: str, tags: list[str] | None, execution_id: str,
    issue_id: str = "verifier-issue-1",
    project_id: str = "proj-vwr",
) -> object:
    class _Req:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}

        async def json(self_inner):
            payload = {
                "event": event,
                "issueId": issue_id,
                "issueNumber": None,
                "projectId": project_id,
                "executionId": execution_id,
            }
            if tags is not None:
                payload["tags"] = tags
            return payload

    return _Req()


def _mock_bkd_for_webhook(
    monkeypatch, *, tags: list[str], last_message: str | None = None,
) -> None:
    """Stub webhook.BKDClient.get_issue + get_last_assistant_message."""

    class _BKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_issue(self, *a, **kw):
            class _R:
                pass
            _R.tags = list(tags)
            _R.external_session_id = None
            _R.description = None
            return _R()

        async def get_last_assistant_message(self, *a, **kw):
            return last_message

        async def update_issue(self, *a, **kw):
            return None

        async def follow_up_issue(self, *a, **kw):
            return None

    monkeypatch.setattr(webhook, "BKDClient", _BKD)


def _common_webhook_mocks(monkeypatch) -> dict:
    """Mocks shared by all webhook tests; returns call trackers."""
    import orchestrator.observability as obs

    obs_events: list[tuple] = []
    step_calls: list[dict] = []
    update_ctx_calls: list[tuple] = []

    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock())
    monkeypatch.setattr(
        obs, "record_event",
        AsyncMock(side_effect=lambda *a, **kw: obs_events.append((a, kw))),
    )
    monkeypatch.setattr(
        engine, "step",
        AsyncMock(side_effect=lambda *a, **kw: step_calls.append(kw) or {"action": "stepped"}),
    )
    # 防 webhook 在 engine.step 之前 / 之后写 ctx 失败
    monkeypatch.setattr(
        rs_mod, "update_context",
        AsyncMock(side_effect=lambda _p, req_id, patch: update_ctx_calls.append((req_id, patch))),
    )
    monkeypatch.setattr(rs_mod, "insert_init", AsyncMock())
    monkeypatch.setattr(vd_mod, "insert_decision", AsyncMock())
    # _push_upstream_status 是无副作用的 BKD 写，stub 掉防干扰
    monkeypatch.setattr(webhook, "_push_upstream_status", AsyncMock())
    # stage_runs stamp helpers tolerate missing pieces
    from orchestrator.store import stage_runs
    monkeypatch.setattr(stage_runs, "stamp_bkd_session_id", AsyncMock())
    monkeypatch.setattr(stage_runs, "stamp_bkd_issue_id", AsyncMock())

    return {
        "obs_events": obs_events,
        "step_calls": step_calls,
        "update_ctx_calls": update_ctx_calls,
    }


# ── VWR-S1: dedup skip + verifier + REVIEW_RUNNING → bypass + engine.step ───


@pytest.mark.asyncio
async def test_VWR_S1_verifier_resume_bypass_when_review_running(monkeypatch):
    """同 executionId 的 redelivery + state==REVIEW_RUNNING + verifier issue
    → bypass dedup → 解析决策 → engine.step(VERIFY_PASS)。
    """
    tracking = _common_webhook_mocks(monkeypatch)

    decision_json = (
        '```json\n'
        + json.dumps({
            "action": "pass",
            "fixer": None,
            "scope": "",
            "reason": "tests pass after fix",
            "confidence": "high",
        })
        + '\n```'
    )
    _mock_bkd_for_webhook(
        monkeypatch,
        tags=["verifier", "verify:dev_cross_check", "REQ-vwr-1"],
        last_message=decision_json,
    )

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="skip"))

    review_row = _Row(state=ReqState.REVIEW_RUNNING, context={
        "verifier_stage": "dev_cross_check",
        "verifier_trigger": "fail",
        # closes #457: bypass identity check requires the redelivery's issueId
        # to match the verifier_issue_id stored when this round started.
        "verifier_issue_id": "verifier-issue-1",
    })
    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=review_row))

    req = _make_request(
        event="session.completed",
        tags=None,  # 触发 BKD fetch（模拟 session events 不带 tags）
        execution_id="exec-A",
    )

    result = await webhook.webhook(req)

    # bypass 必须放行 engine.step
    assert len(tracking["step_calls"]) == 1, (
        f"VWR-S1: bypass 必须最终调 engine.step；got {len(tracking['step_calls'])}"
    )
    fired_event = tracking["step_calls"][0]["event"]
    # router.decision_to_event(stage='dev_cross_check') 路由到 VERIFY_PASS_DEV_CROSS_CHECK
    expected_pass_event = router_lib.pass_event_for_stage("dev_cross_check")
    assert fired_event == expected_pass_event, (
        f"VWR-S1: bypass 应推 verify-pass 系列事件 ({expected_pass_event}); got {fired_event}"
    )

    # 必须 emit verifier_resume_bypass obs event
    bypass_obs = [
        e for e in tracking["obs_events"]
        if e[0] and e[0][0] == "dedup.verifier_resume_bypass"
    ]
    assert len(bypass_obs) == 1, (
        "VWR-S1: 必须 emit obs event 'dedup.verifier_resume_bypass'"
    )
    extras = bypass_obs[0][1].get("extras", {})
    assert extras.get("executionId") == "exec-A", (
        "VWR-S1: bypass obs event 必须含 executionId（事故复盘锚点）"
    )

    # 而原本的 short-circuit skip 路径不应触发
    assert result.get("action") != "skip" or result.get("reason") != \
        "duplicate event already processed", (
        "VWR-S1: bypass 触发后不应再返 'duplicate event already processed' skip"
    )


# ── VWR-S2: dedup skip + verifier 但 state 已转出 REVIEW_RUNNING → 维持 skip ──


@pytest.mark.asyncio
async def test_VWR_S2_stale_redelivery_after_state_advances_keeps_skip(monkeypatch):
    """state 已离开 REVIEW_RUNNING 时即使是 verifier issue dedup skip 也不能 bypass，
    防 BKD redelivery 反向 escalate 已推进的 REQ。
    """
    tracking = _common_webhook_mocks(monkeypatch)

    _mock_bkd_for_webhook(
        monkeypatch,
        tags=["verifier", "verify:dev_cross_check", "REQ-vwr-2"],
        last_message='```json\n{"action":"pass","reason":"x","confidence":"high"}\n```',
    )

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="skip"))

    advanced_row = _Row(state=ReqState.STAGING_TEST_RUNNING, context={})
    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=advanced_row))

    req = _make_request(
        event="session.completed", tags=None, execution_id="exec-B",
    )

    result = await webhook.webhook(req)

    assert result.get("action") == "skip", (
        "VWR-S2: state 已离开 REVIEW_RUNNING 时 dedup skip 必须守住"
    )
    assert "duplicate" in (result.get("reason") or ""), (
        "VWR-S2: skip reason 应明示 dedup 原因"
    )
    # engine.step 必须不被调用
    assert len(tracking["step_calls"]) == 0, (
        f"VWR-S2: stale dedup skip 不能放行 engine.step；"
        f"called {len(tracking['step_calls'])}"
    )
    # 不应 emit bypass obs event
    bypass_obs = [
        e for e in tracking["obs_events"]
        if e[0] and e[0][0] == "dedup.verifier_resume_bypass"
    ]
    assert len(bypass_obs) == 0, (
        "VWR-S2: state 已转出 REVIEW_RUNNING 时不能 emit verifier_resume_bypass"
    )


# ── VWR-S2b: REVIEW_RUNNING 但 redelivery 来自老 verifier issue → skip 不 bypass ──
#
# closes #457：bypass 仅当 redelivery issue id == ctx.verifier_issue_id。
# 若来 verifier 类型 redelivery 但 issue 是老一轮的（不是当前 review 等的那个），
# 必须保持 dedup skip。dogfood batch 7/7 全部撞这一条卡死。


@pytest.mark.asyncio
async def test_VWR_S2b_stale_redelivery_from_other_verifier_issue_keeps_skip(monkeypatch):
    """state==REVIEW_RUNNING 但 redelivery issue 跟 ctx.verifier_issue_id 不一致 → 不 bypass。"""
    tracking = _common_webhook_mocks(monkeypatch)

    _mock_bkd_for_webhook(
        monkeypatch,
        tags=["verifier", "verify:dev_cross_check", "REQ-vwr-2b"],
        last_message='```json\n{"action":"fix-needed","fixer":"dev","reason":"x","confidence":"high"}\n```',
    )

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="skip"))

    # state=REVIEW_RUNNING, ctx 等的是 verifier-issue-CURRENT (e.g. staging_test
    # round 的 verifier)；redelivery 来自 verifier-issue-OLD (上一轮 dev_cross_check
    # verifier 10min 后 redelivery)。
    review_row = _Row(state=ReqState.REVIEW_RUNNING, context={
        "verifier_stage": "staging_test",
        "verifier_trigger": "fail",
        "verifier_issue_id": "verifier-issue-CURRENT",
    })
    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=review_row))

    req = _make_request(
        event="session.completed",
        tags=None,
        execution_id="exec-OLD",
        issue_id="verifier-issue-OLD",  # 老 verifier issue
    )

    result = await webhook.webhook(req)

    assert result.get("action") == "skip", (
        "VWR-S2b: 老 verifier issue redelivery 必须保持 dedup skip"
    )
    # engine.step 必须不被调用
    assert len(tracking["step_calls"]) == 0, (
        f"VWR-S2b: stale issue redelivery 不能放行 engine.step; "
        f"called {len(tracking['step_calls'])}"
    )
    # 不应 emit verifier_resume_bypass obs event
    bypass_obs = [
        e for e in tracking["obs_events"]
        if e[0] and e[0][0] == "dedup.verifier_resume_bypass"
    ]
    assert len(bypass_obs) == 0, (
        "VWR-S2b: 老 verifier issue redelivery 不能 emit verifier_resume_bypass"
    )
    # 应 emit bypass_rejected_stale_verifier obs event 留 audit
    rejected_obs = [
        e for e in tracking["obs_events"]
        if e[0] and e[0][0] == "dedup.bypass_rejected_stale_verifier"
    ]
    assert len(rejected_obs) == 1, (
        "VWR-S2b: 必须 emit obs event 'dedup.bypass_rejected_stale_verifier' 留事故复盘锚点"
    )


# ── VWR-S3: dedup observability event emit（new / retry，noise 不 emit）──────


@pytest.mark.asyncio
@pytest.mark.parametrize("dedup_status", ["new", "retry"])
async def test_VWR_S3_dedup_observability_event_emitted_for_processed(
    monkeypatch, dedup_status,
):
    """REQ-tagged 事件经 noise filter 通过后，必须 emit
    'webhook.dedup.observed'，extras 含 event_id + executionId + status。
    """
    tracking = _common_webhook_mocks(monkeypatch)

    _mock_bkd_for_webhook(
        monkeypatch,
        tags=["analyze", "REQ-vwr-3"],
        last_message=None,
    )

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value=dedup_status))

    # 任何 state 都行；skip 路径会早 return 不进 engine.step
    row = _Row(state=ReqState.ANALYZING, context={})
    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=row))

    req = _make_request(
        event="session.completed",
        tags=None,
        execution_id="exec-S3",
    )

    await webhook.webhook(req)

    observed = [
        e for e in tracking["obs_events"]
        if e[0] and e[0][0] == "webhook.dedup.observed"
    ]
    assert len(observed) == 1, (
        f"VWR-S3 [{dedup_status}]: 必须 emit 'webhook.dedup.observed' 一次；"
        f"got {len(observed)}"
    )
    extras = observed[0][1].get("extras", {})
    assert extras.get("status") == dedup_status, (
        f"VWR-S3: extras.status 必须等于 dedup_status={dedup_status!r}，"
        f"got {extras.get('status')!r}"
    )
    assert extras.get("executionId") == "exec-S3", (
        f"VWR-S3: extras.executionId 必须含 payload 的 executionId，"
        f"got {extras.get('executionId')!r}"
    )
    assert extras.get("event_id"), (
        "VWR-S3: extras 必须含非空 event_id"
    )


# ── VWR-S4: admin retrigger-verifier 解 BKD chat 决策 → engine.step ─────────


@pytest.mark.asyncio
async def test_VWR_S4_admin_retrigger_reads_bkd_and_steps_pass(monkeypatch):
    """retrigger-verifier 读 BKD chat 解决策 + 喂 engine.step + 返 decision payload。"""
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    row = _Row(state=ReqState.REVIEW_RUNNING, context={
        "verifier_stage": "dev_cross_check",
        "verifier_trigger": "fail",
    })
    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=row))
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(vd_mod, "insert_decision", AsyncMock())

    step_calls: list = []
    monkeypatch.setattr(
        engine, "step",
        AsyncMock(
            side_effect=lambda *a, **kw: step_calls.append(kw)
            or {"action": "stepped"},
        ),
    )

    decision_msg = (
        '```json\n'
        + json.dumps({
            "action": "pass",
            "fixer": None,
            "scope": "",
            "reason": "verified manually",
            "confidence": "high",
        })
        + '\n```'
    )

    class _BKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_issue(self, *a, **kw):
            class _R:
                tags: ClassVar = ["verifier", "verify:dev_cross_check", "REQ-vwr-4"]
            return _R()

        async def get_last_assistant_message(self, *a, **kw):
            return decision_msg

    monkeypatch.setattr(admin_mod, "BKDClient", _BKD)

    body = admin_mod.RetriggerVerifierBody(issue_id="verifier-issue-vwr4")

    result = await admin_mod.retrigger_verifier(
        "REQ-vwr-4", body, authorization="Bearer x",
    )

    assert result.get("action") == "retriggered"
    assert result.get("decision", {}).get("action") == "pass"
    expected_pass_event = router_lib.pass_event_for_stage("dev_cross_check")
    assert result.get("event") == expected_pass_event.value
    assert len(step_calls) == 1, (
        "VWR-S4: engine.step 必须被调用一次"
    )
    assert step_calls[0]["event"] == expected_pass_event


# ── VWR-S5: admin retrigger-verifier — 解析失败 → 422 + 不动 state ─────────


@pytest.mark.asyncio
async def test_VWR_S5_admin_retrigger_returns_422_on_parse_fail(monkeypatch):
    """没 JSON / malformed → 422 + engine.step 不调用。"""
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    row = _Row(state=ReqState.REVIEW_RUNNING, context={})
    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=row))
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())

    step_calls: list = []
    monkeypatch.setattr(
        engine, "step",
        AsyncMock(side_effect=lambda *a, **kw: step_calls.append(kw)),
    )

    class _BKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_issue(self, *a, **kw):
            class _R:
                tags: ClassVar = ["verifier"]
            return _R()

        async def get_last_assistant_message(self, *a, **kw):
            return "no decision json here, just words"

    monkeypatch.setattr(admin_mod, "BKDClient", _BKD)

    body = admin_mod.RetriggerVerifierBody(issue_id="verifier-issue-vwr5")

    with pytest.raises(HTTPException) as ei:
        await admin_mod.retrigger_verifier(
            "REQ-vwr-5", body, authorization="Bearer x",
        )

    assert ei.value.status_code == 422, (
        f"VWR-S5: 解析失败必须返 422；got {ei.value.status_code}"
    )
    assert "parse" in (ei.value.detail or "").lower() or \
        "decision" in (ei.value.detail or "").lower(), (
        f"VWR-S5: 422 detail 应说明 parse / decision 失败原因；got {ei.value.detail!r}"
    )
    assert len(step_calls) == 0, (
        "VWR-S5: 解析失败时不能调 engine.step"
    )


@pytest.mark.asyncio
async def test_VWR_S5b_admin_retrigger_404_on_missing_req(monkeypatch):
    """req_id 不存在 → 404。"""
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)
    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())

    body = admin_mod.RetriggerVerifierBody(issue_id="x")

    with pytest.raises(HTTPException) as ei:
        await admin_mod.retrigger_verifier(
            "REQ-missing", body, authorization="Bearer x",
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_VWR_S5c_admin_retrigger_400_on_empty_issue_id(monkeypatch):
    """body.issue_id 空 → 400。"""
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())

    body = admin_mod.RetriggerVerifierBody(issue_id="   ")
    with pytest.raises(HTTPException) as ei:
        await admin_mod.retrigger_verifier(
            "REQ-vwr-x", body, authorization="Bearer x",
        )
    assert ei.value.status_code == 400


# Sanity: import-time hooks confirm Event has expected verifier routing events.
def test_VWR_S1_assumes_verify_pass_routing_present():
    """Sanity — 测试假设 dev_cross_check 有合法 stage-specific PASS event。"""
    expected = router_lib.pass_event_for_stage("dev_cross_check")
    assert expected is not None and isinstance(expected, Event)
