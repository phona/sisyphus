"""M14b：verifier-agent 框架 单测。

覆盖：
1. decision schema 校验 + Event 映射（router.validate_decision / decision_to_event）
2. decision 提取（tag base64 / description ```json```）
3. derive_verifier_event 整合：合规 / 非法 / 缺失 → 正确事件
4. invoke_verifier：prompt 渲染 + BKD issue 创建 + ctx 落字段
5. action handler：apply_verify_pass / apply_verify_retry_analyze 的行为
6. 12 个 stage_trigger prompt 模板都能渲染出来
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from orchestrator.router import (
    decision_to_event,
    derive_verifier_event,
    derive_verifier_event_with_retry_info,
    extract_decision_from_issue,
    validate_decision,
)
from orchestrator.state import Event

# ─── 1. validate_decision ────────────────────────────────────────────────

@pytest.mark.parametrize("decision,ok", [
    ({"action": "pass", "scope": None, "reason": "ok", "confidence": "high"}, True),
    ({"action": "retry-analyze", "scope": "src/", "reason": "bug", "confidence": "high"}, True),
    ({"action": "retry-analyze", "scope": "openspec/changes/REQ-1/", "reason": "x", "confidence": "low"}, True),
    # retry_checker 已砍 → 当 invalid action（unknown enum）
    ({"action": "retry_checker", "scope": None, "reason": "flaky", "confidence": "low"}, False),
    ({"action": "escalate", "scope": None, "reason": "need human", "confidence": "high"}, True),
    # invalid action
    ({"action": "nope", "scope": None, "reason": "", "confidence": "high"}, False),
    # invalid confidence
    ({"action": "pass", "scope": None, "reason": "", "confidence": "medium"}, False),
    # not a dict
    ("not a dict", False),
    (None, False),
])
def test_validate_decision(decision, ok):
    got_ok, _ = validate_decision(decision)
    assert got_ok == ok


def test_decision_to_event_mapping():
    assert decision_to_event({"action": "pass"}) == Event.VERIFY_PASS
    assert decision_to_event({"action": "retry-analyze"}) == Event.VERIFY_RETRY_ANALYZE
    assert decision_to_event({"action": "escalate"}) == Event.VERIFY_ESCALATE


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


# ─── 2. extract_decision_from_issue ──────────────────────────────────────

def test_extract_from_tag_base64():
    d = {"action": "pass", "scope": None, "reason": "ok", "confidence": "high"}
    b64 = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    tags = ["verifier", "REQ-1", f"decision:{b64}"]
    got = extract_decision_from_issue(None, tags)
    assert got == d


def test_extract_from_description_json_block():
    d = {"action": "retry-analyze", "scope": "src/", "reason": "need fix", "confidence": "high"}
    desc = f"some text\n```json\n{json.dumps(d)}\n```\nfooter"
    got = extract_decision_from_issue(desc, [])
    assert got == d


def test_extract_prefers_last_json_block():
    d1 = {"action": "pass", "scope": None}
    d2 = {"action": "escalate", "scope": None}
    desc = f"```json\n{json.dumps(d1)}\n```\n```json\n{json.dumps(d2)}\n```"
    got = extract_decision_from_issue(desc, [])
    assert got == d2


def test_extract_tag_beats_description_when_valid():
    d_tag = {"action": "pass", "scope": None}
    d_desc = {"action": "escalate", "scope": None}
    b64 = base64.urlsafe_b64encode(json.dumps(d_tag).encode()).decode().rstrip("=")
    desc = f"```json\n{json.dumps(d_desc)}\n```"
    got = extract_decision_from_issue(desc, [f"decision:{b64}"])
    assert got == d_tag


def test_extract_bad_tag_falls_back_to_description():
    d_desc = {"action": "pass", "scope": None}
    desc = f"```json\n{json.dumps(d_desc)}\n```"
    got = extract_decision_from_issue(desc, ["decision:!!!not-base64!!!"])
    assert got == d_desc


def test_extract_none_when_empty():
    assert extract_decision_from_issue(None, []) is None
    assert extract_decision_from_issue("", []) is None
    assert extract_decision_from_issue("no json here", []) is None


# ─── 3. derive_verifier_event 整合 ───────────────────────────────────────

def test_derive_verifier_event_pass():
    d = {"action": "pass", "scope": None, "reason": "ok", "confidence": "high"}
    b64 = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    ev, decision, why = derive_verifier_event(None, [f"decision:{b64}"])
    assert ev == Event.VERIFY_PASS
    assert decision == d
    assert why == ""


def test_derive_verifier_event_retry_analyze():
    d = {"action": "retry-analyze", "scope": "src/", "reason": "bug", "confidence": "high"}
    b64 = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    ev, got_decision, _ = derive_verifier_event(None, [f"decision:{b64}"])
    assert ev == Event.VERIFY_RETRY_ANALYZE
    assert got_decision == d


def test_derive_verifier_event_legacy_retry_checker_now_escalates():
    """retry_checker 已砍 → router 当 invalid action → escalate（避免老 prompt 卡死）。"""
    d = {"action": "retry_checker", "scope": None, "reason": "flaky", "confidence": "low"}
    desc = f"```json\n{json.dumps(d)}\n```"
    ev, _decision, why = derive_verifier_event(desc, [])
    assert ev == Event.VERIFY_ESCALATE
    assert "retry_checker" in why or "invalid" in why.lower()


def test_derive_verifier_event_invalid_decision_escalates():
    d = {"action": "nope"}  # invalid
    desc = f"```json\n{json.dumps(d)}\n```"
    ev, decision, why = derive_verifier_event(desc, [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision == d
    assert "invalid" in why.lower()


def test_derive_verifier_event_no_decision_escalates():
    ev, decision, why = derive_verifier_event("no json here", [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision is None
    assert "no decision" in why.lower()


# ─── 3.5 derive_verifier_event_with_retry_info ───────────────────────────

def test_derive_with_retry_info_valid_decision():
    d = {"action": "pass", "scope": None, "reason": "ok", "confidence": "high"}
    ev, decision, why, retry = derive_verifier_event_with_retry_info(None, [f"decision:{_b64(d)}"])
    assert ev == Event.VERIFY_PASS
    assert decision == d
    assert why == ""
    assert retry is False


def test_derive_with_retry_info_schema_invalid_is_retry_worthy():
    d = {"action": "nope"}  # invalid action
    desc = f"```json\n{json.dumps(d)}\n```"
    ev, decision, why, retry = derive_verifier_event_with_retry_info(desc, [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision == d
    assert "invalid" in why.lower()
    assert retry is True


def test_derive_with_retry_info_no_decision_not_retry_worthy():
    ev, decision, _why, retry = derive_verifier_event_with_retry_info("no json here", [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision is None
    assert retry is False


def test_derive_with_retry_info_unparseable_but_retry_worthy():
    """找到了 decision-like 文本但解析失败 → retry_worthy=True。"""
    desc = "My decision is {action: pass, scope: None} because..."
    ev, decision, _why, retry = derive_verifier_event_with_retry_info(desc, [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision is None
    assert retry is True


# ─── 4. invoke_verifier ─────────────────────────────────────────────────

@dataclass
class FakeIssue:
    id: str
    project_id: str = "p"
    issue_number: int = 0
    title: str = ""
    status_id: str = "todo"
    tags: list = None
    session_status: str | None = None
    description: str | None = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


def make_fake_bkd():
    bkd = AsyncMock()
    bkd.create_issue = AsyncMock(return_value=FakeIssue(id="vfy-1"))
    bkd.update_issue = AsyncMock(return_value=FakeIssue(id="vfy-1"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    return bkd


def patch_bkd(monkeypatch, fake):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake
    monkeypatch.setattr("orchestrator.actions._verifier.BKDClient", _ctx)


def patch_db(monkeypatch):
    writes: list = []

    class P:
        async def execute(self, sql, *args):
            writes.append((sql.strip()[:40], args))
        async def fetchrow(self, sql, *args):
            return None
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: P())
    return writes


@pytest.fixture(autouse=True)
def _mock_verifier_dispatch_slugs(monkeypatch):
    """REQ-427: prevent real DB hits from slug dedup added to _verifier.py."""
    from orchestrator.actions import _verifier as v
    monkeypatch.setattr(v.dispatch_slugs, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(v.dispatch_slugs, "put", AsyncMock())


@pytest.mark.asyncio
async def test_invoke_verifier_creates_issue_with_right_tags(monkeypatch):
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_db(monkeypatch)

    out = await v.invoke_verifier(
        stage="staging_test",
        trigger="fail",
        req_id="REQ-9",
        project_id="proj-1",
        stderr_tail="oops",
        ctx={"intent_title": "加登录"},
    )
    assert out["verifier_issue_id"] == "vfy-1"
    assert out["stage"] == "staging_test"
    assert out["trigger"] == "fail"

    _args, kwargs = fake.create_issue.await_args
    assert kwargs["project_id"] == "proj-1"
    assert "verifier" in kwargs["tags"]
    assert "REQ-9" in kwargs["tags"]
    assert "verify:staging_test" in kwargs["tags"]
    assert "trigger:fail" in kwargs["tags"]
    assert kwargs["use_worktree"] is True
    assert "[VERIFY staging_test] fail" in kwargs["title"]
    assert "加登录" in kwargs["title"]

    fake.follow_up_issue.assert_awaited_once()
    # update_issue 被调 1 次（→working）
    fake.update_issue.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_verifier_renders_template(monkeypatch):
    """验 prompt 渲染结果带 stderr_tail + stage hint。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_db(monkeypatch)

    await v.invoke_verifier(
        stage="dev_cross_check", trigger="fail",
        req_id="REQ-7", project_id="p",
        stderr_tail="TypeError: whoopsie",
    )
    _, kwargs = fake.follow_up_issue.await_args
    prompt = kwargs["prompt"]
    assert "verifier-agent" in prompt
    assert "REQ-7" in prompt
    assert "TypeError: whoopsie" in prompt
    # decision schema 指示必须在
    assert '"action":' in prompt
    assert "pass" in prompt and "retry-analyze" in prompt and "escalate" in prompt
    # retry_checker 已砍：prompt 不应再提该选项
    assert "retry_checker" not in prompt


@pytest.mark.asyncio
async def test_invoke_verifier_rejects_unknown_stage():
    from orchestrator.actions import _verifier as v
    with pytest.raises(ValueError):
        await v.invoke_verifier(
            stage="nonsense", trigger="success",
            req_id="R", project_id="p",
        )


@pytest.mark.asyncio
async def test_invoke_verifier_rejects_unknown_trigger():
    from orchestrator.actions import _verifier as v
    with pytest.raises(ValueError):
        await v.invoke_verifier(
            stage="dev_cross_check", trigger="maybe",
            req_id="R", project_id="p",
        )


# ─── 5. 所有 12 个 prompt 模板都能渲染 ─────────────────────────────────────

@pytest.mark.parametrize("stage", [
    "analyze", "spec_lint", "dev_cross_check", "staging_test", "pr_ci", "accept",
])
@pytest.mark.parametrize("trigger", ["success", "fail"])
def test_all_verifier_prompts_render(stage, trigger):
    from orchestrator.prompts import render
    out = render(
        f"verifier/{stage}_{trigger}.md.j2",
        req_id="REQ-42",
        stage=stage,
        trigger=trigger,
        stderr_tail="boom",
        history=[],
        artifact_paths=[],
    )
    assert "REQ-42" in out
    assert "verifier-agent" in out
    # decision schema 文本必须在 —— 强校验 agent 输出格式
    assert '"action":' in out
    assert "pass" in out and "escalate" in out


# ─── 6. action handlers ─────────────────────────────────────────────────

def make_body(issue_id="src-1", project_id="p", event="session.completed"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": event, "title": "", "tags": [], "issueNumber": None,
    })()


@pytest.mark.asyncio
async def test_apply_verify_pass_chains_staging_test_pass(monkeypatch):
    """verifier_stage=staging_test + VERIFY_PASS → CAS REVIEW_RUNNING→STAGING_TEST_RUNNING
    + emit STAGING_TEST_PASS（走原主链）。"""
    from orchestrator.actions import _verifier as v

    cas_calls: list = []

    async def fake_cas(pool, req_id, expected, nxt, event, action, context_patch=None):
        cas_calls.append((req_id, expected, nxt, event, action))
        return True

    monkeypatch.setattr("orchestrator.actions._verifier.req_state.cas_transition", fake_cas)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.apply_verify_pass(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "staging_test"},
    )
    assert out["emit"] == Event.STAGING_TEST_PASS.value
    assert out["stage"] == "staging_test"
    assert len(cas_calls) == 1
    (_, expected, nxt, event, _) = cas_calls[0]
    assert expected.value == "review-running"
    assert nxt.value == "staging-test-running"
    assert event == Event.VERIFY_PASS


@pytest.mark.asyncio
async def test_apply_verify_pass_unknown_stage_escalates(monkeypatch):
    from orchestrator.actions import _verifier as v
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)
    out = await v.apply_verify_pass(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": None},
    )
    assert out["emit"] == Event.VERIFY_ESCALATE.value


@pytest.mark.asyncio
async def test_apply_verify_pass_cas_fail_returns_skip(monkeypatch):
    """并发导致 REVIEW_RUNNING 已被别的事件改走 → action 不抛，返 cas_failed。"""
    from orchestrator.actions import _verifier as v

    async def fake_cas(*a, **kw):
        return False
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.cas_transition", fake_cas)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.apply_verify_pass(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "dev_cross_check"},
    )
    assert out == {"cas_failed": True}


@pytest.mark.asyncio
async def test_apply_verify_pass_closes_orphan_verifier_stage_run(monkeypatch):
    """REGRESSION: (REVIEW_RUNNING, VERIFY_PASS) 是 self-loop transition →
    engine._record_stage_transitions 跳过 close → verifier 行 outcome 永远为 None。

    DB 实证：18 条 verifier outcome=NULL（占 verifier 总数 29%）污染 stage_stats /
    agent_quality / failure_mode 三张 Metabase 看板。修：apply_verify_pass 在
    REVIEW_RUNNING → target_state CAS 成功后，手动 close_latest_stage_run("verifier",
    outcome="pass")。
    """
    from orchestrator.actions import _verifier as v

    close_calls: list = []

    async def fake_cas(*a, **kw):
        return True

    async def fake_close(pool, req_id, stage, *, outcome, fail_reason=None):
        close_calls.append({"req_id": req_id, "stage": stage, "outcome": outcome})
        return 42  # any non-None id

    monkeypatch.setattr(
        "orchestrator.actions._verifier.req_state.cas_transition", fake_cas,
    )
    monkeypatch.setattr(
        "orchestrator.actions._verifier.stage_runs.close_latest_stage_run",
        fake_close,
    )
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.apply_verify_pass(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "pr_ci"},
    )

    assert out["emit"] == Event.PR_CI_PASS.value
    assert close_calls == [
        {"req_id": "REQ-9", "stage": "verifier", "outcome": "pass"},
    ], "verifier orphan stage_run must be closed with outcome=pass when CAS from REVIEW_RUNNING"


@pytest.mark.asyncio
async def test_apply_verify_pass_skips_close_on_escalated_resume(monkeypatch):
    """resume from ESCALATED：上一轮 verifier 行已被前次 escalate 路径关掉
    （outcome=escalate）；此时不应再 close 一次，避免改写 outcome 或关错行。"""
    from orchestrator.actions import _verifier as v

    close_calls: list = []

    async def fake_cas(pool, req_id, expected, nxt, event, action, context_patch=None):
        # 模拟 REQ 在 ESCALATED：CAS REVIEW_RUNNING 失败，CAS ESCALATED 成功
        return expected.value == "escalated"

    async def fake_close(pool, req_id, stage, *, outcome, fail_reason=None):
        close_calls.append({"req_id": req_id, "stage": stage, "outcome": outcome})
        return 42

    monkeypatch.setattr(
        "orchestrator.actions._verifier.req_state.cas_transition", fake_cas,
    )
    monkeypatch.setattr(
        "orchestrator.actions._verifier.stage_runs.close_latest_stage_run",
        fake_close,
    )
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.apply_verify_pass(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "pr_ci"},
    )

    assert out["emit"] == Event.PR_CI_PASS.value
    assert close_calls == [], (
        "ESCALATED-resume 路径不应 close verifier stage_run "
        "（前次 escalate 已 close，重复 close 会改写 outcome）"
    )


@pytest.mark.asyncio
async def test_apply_verify_pass_resumes_from_escalated(monkeypatch):
    """resume 路径：用户 follow-up escalate 的 verifier issue → 新 decision=pass →
    apply_verify_pass 第一次 CAS REVIEW_RUNNING 失败（state 是 ESCALATED），
    第二次 CAS ESCALATED → STAGE_RUNNING 成功 + chain emit 上游 pass event。"""
    from orchestrator.actions import _verifier as v

    cas_calls: list = []

    async def fake_cas(pool, req_id, expected, nxt, event, action, context_patch=None):
        cas_calls.append(expected.value)
        # 模拟"REQ 当前在 ESCALATED"——CAS REVIEW_RUNNING 失败，CAS ESCALATED 成功
        return expected.value == "escalated"

    monkeypatch.setattr("orchestrator.actions._verifier.req_state.cas_transition", fake_cas)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.apply_verify_pass(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "pr_ci"},
    )
    assert cas_calls == ["review-running", "escalated"]
    assert out["emit"] == Event.PR_CI_PASS.value
    assert out["stage"] == "pr_ci"


@pytest.mark.asyncio
async def test_invoke_verifier_for_staging_test_fail(monkeypatch):
    """B2：专门 handler 写死 stage=staging_test，不再从 tags sniff。

    用上游 dev issue 的 tags 调（机械 checker 没自己 issue，webhook tags
    就是 dev）—— 旧实现会按 tag 误路成 dev，新实现应稳定落 staging_test。
    """
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="vfy-3")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.invoke_verifier_for_staging_test_fail(
        body=make_body(project_id="proj-x"),
        req_id="REQ-9",
        tags=["dev", "REQ-9"],
        ctx={},
    )
    assert out["stage"] == "staging_test"
    assert out["trigger"] == "fail"

    _, kwargs = fake.create_issue.await_args
    assert "verify:staging_test" in kwargs["tags"]
    assert "trigger:fail" in kwargs["tags"]


@pytest.mark.asyncio
async def test_invoke_verifier_for_pr_ci_fail(monkeypatch):
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="vfy-pr")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.invoke_verifier_for_pr_ci_fail(
        body=make_body(project_id="proj-x"),
        req_id="REQ-9",
        tags=["dev", "REQ-9"],
        ctx={},
    )
    assert out["stage"] == "pr_ci"
    assert out["trigger"] == "fail"

    _, kwargs = fake.create_issue.await_args
    assert "verify:pr_ci" in kwargs["tags"]


@pytest.mark.asyncio
async def test_invoke_verifier_for_accept_fail(monkeypatch):
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="vfy-ac")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.invoke_verifier_for_accept_fail(
        body=make_body(project_id="proj-x"),
        req_id="REQ-9",
        tags=["watchdog:accept-tearing-down"],
        ctx={},
    )
    assert out["stage"] == "accept"
    assert out["trigger"] == "fail"

    _, kwargs = fake.create_issue.await_args
    assert "verify:accept" in kwargs["tags"]


