"""INTAKING stage テスト。

カバー：
1. extract_intake_finalized_intent：JSON 解析 + 6 フィールド schema 検証
2. router 3 路：INTENT_INTAKE / INTAKE_PASS / INTAKE_FAIL / 中間ラウンド None
3. state machine 3 transition 検証
4. start_intake action smoke test
5. start_analyze_with_finalized_intent action smoke test（新 issue 作成 / ctx 欠如→ escalate）
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from orchestrator.router import derive_event, extract_intake_finalized_intent
from orchestrator.state import Event, ReqState, decide

# ─── 1. extract_intake_finalized_intent ──────────────────────────────────────

_VALID_INTENT = {
    "involved_repos": ["owner/repo-a"],
    "business_behavior": "ユーザー視点の動作説明",
    "data_constraints": "フィールド / endpoint / エラー形式",
    "edge_cases": "境界 / エラー",
    "do_not_touch": "触れてはいけない範囲",
    "acceptance": "完了の定義",
}


def test_extract_from_json_codeblock():
    text = f"いくつか質問があります。\n```json\n{json.dumps(_VALID_INTENT)}\n```\nよろしくお願いします。"
    got = extract_intake_finalized_intent(text)
    assert got == _VALID_INTENT


def test_extract_from_plain_codeblock():
    text = f"draft:\n```\n{json.dumps(_VALID_INTENT)}\n```\nOK?"
    got = extract_intake_finalized_intent(text)
    assert got == _VALID_INTENT


def test_extract_prefers_last_json_block():
    first = {**_VALID_INTENT, "business_behavior": "old"}
    last = {**_VALID_INTENT, "business_behavior": "new"}
    text = f"```json\n{json.dumps(first)}\n```\n後で更新\n```json\n{json.dumps(last)}\n```"
    got = extract_intake_finalized_intent(text)
    assert got["business_behavior"] == "new"


def test_extract_bare_braces_fallback():
    d = dict(_VALID_INTENT)
    text = f"最終意図：{json.dumps(d)} 以上です。"
    got = extract_intake_finalized_intent(text)
    assert got == d


def test_extract_none_when_missing_required_field():
    bad = dict(_VALID_INTENT)
    del bad["acceptance"]
    text = f"```json\n{json.dumps(bad)}\n```"
    got = extract_intake_finalized_intent(text)
    assert got is None


def test_extract_invalid_schema_returns_none():
    text = '```json\n{"action": "pass", "fixer": null}\n```'
    assert extract_intake_finalized_intent(text) is None


def test_extract_none_on_empty():
    assert extract_intake_finalized_intent(None) is None
    assert extract_intake_finalized_intent("") is None
    assert extract_intake_finalized_intent("no json here") is None


# ─── 2. router routing ───────────────────────────────────────────────────────


@pytest.mark.parametrize("event_type,tags,expected", [
    ("issue.updated",     ["intent:intake"],                    Event.INTENT_INTAKE),
    # intent:intake 已接管 → None（避免重复触发）
    ("issue.updated",     ["intent:intake", "intake", "REQ-1"], None),
    ("session.completed", ["intake", "REQ-1", "result:pass"],   Event.INTAKE_PASS),
    ("session.completed", ["intake", "REQ-1", "result:fail"],   Event.INTAKE_FAIL),
    # 中间轮：仅 intake tag，无 result → None
    ("session.completed", ["intake", "REQ-1"],                  None),
])
def test_router_intake_routing(event_type, tags, expected):
    assert derive_event(event_type, tags) == expected


# ─── 3. state machine transitions ────────────────────────────────────────────

def test_state_intake_transitions():
    t = decide(ReqState.INIT, Event.INTENT_INTAKE)
    assert t is not None
    assert t.next_state == ReqState.INTAKING
    assert t.action == "start_intake"

    t = decide(ReqState.INTAKING, Event.INTAKE_PASS)
    assert t is not None
    assert t.next_state == ReqState.ANALYZING
    assert t.action == "start_analyze_with_finalized_intent"

    t = decide(ReqState.INTAKING, Event.INTAKE_FAIL)
    assert t is not None
    assert t.next_state == ReqState.ESCALATED
    assert t.action == "escalate"


def test_intaking_session_failed_routes_to_escalate_action():
    """新行为：transition self-loop + escalate action 自决是否真 ESCALATED（auto-resume 兼容）"""
    t = decide(ReqState.INTAKING, Event.SESSION_FAILED)
    assert t is not None
    assert t.action == "escalate"
    assert t.next_state == ReqState.INTAKING  # self-loop, action 内部决定


def test_intaking_state_in_enum():
    values = {s.value for s in ReqState}
    assert "intaking" in values


def test_intake_events_in_enum():
    values = {e.value for e in Event}
    assert "intent.intake" in values
    assert "intake.pass" in values
    assert "intake.fail" in values


# ─── 4. start_intake smoke test ──────────────────────────────────────────────

@dataclass
class FakeIssue:
    id: str
    tags: list = None
    def __post_init__(self):
        if self.tags is None:
            self.tags = []


def make_fake_bkd():
    bkd = AsyncMock()
    bkd.update_issue = AsyncMock(return_value=FakeIssue(id="i1"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    return bkd


def patch_bkd(monkeypatch, module_path: str, fake):
    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake
    monkeypatch.setattr(module_path, _ctx)


def make_body(issue_id="src-1", project_id="p"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": "issue.updated", "title": "T", "tags": [], "issueNumber": None,
    })()


@pytest.mark.asyncio
async def test_start_intake(monkeypatch):
    from orchestrator.actions import start_intake as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "orchestrator.actions.start_intake.BKDClient", fake)
    monkeypatch.setattr(mod, "check_admission",
                        AsyncMock(return_value=_admit()))
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())

    out = await mod.start_intake(body=make_body(issue_id="intent-1"), req_id="REQ-9", tags=[], ctx={})
    assert out == {"issue_id": "intent-1", "req_id": "REQ-9"}
    assert fake.update_issue.await_count == 2   # title/tags + working
    assert fake.follow_up_issue.await_count == 1

    # title に [INTAKE] が含まれること
    _, kwargs = fake.update_issue.call_args_list[0]
    assert "[INTAKE]" in kwargs["title"]
    assert "intake" in kwargs["tags"]
    assert "REQ-9" in kwargs["tags"]
    # SAL-S4: pipeline-identity tag is set explicitly here (intent issue isn't
    # opened by sisyphus, so create_issue's auto-inject can't reach it).
    assert "sisyphus" in kwargs["tags"]


def _admit():
    from orchestrator.admission import AdmissionDecision
    return AdmissionDecision(admit=True)


def _deny(reason="inflight-cap-exceeded:10/10"):
    from orchestrator.admission import AdmissionDecision
    return AdmissionDecision(admit=False, reason=reason)


@pytest.mark.asyncio
async def test_start_intake_forwards_user_hint_tags(monkeypatch):
    """REQ-ux-tags-injection: PATCH tags 含 ["sisyphus","intake",req_id] + 转发的 hint。"""
    from orchestrator.actions import start_intake as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "orchestrator.actions.start_intake.BKDClient", fake)
    monkeypatch.setattr(mod, "check_admission",
                        AsyncMock(return_value=_admit()))
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())

    out = await mod.start_intake(
        body=make_body(issue_id="intent-1"),
        req_id="REQ-9",
        tags=["intent:intake", "repo:phona/sisyphus", "ux:fast-track"],
        ctx={},
    )
    assert out == {"issue_id": "intent-1", "req_id": "REQ-9"}

    # 第一次 update_issue 是 rename + tags（第二次是 status_id=working）
    _, kwargs = fake.update_issue.call_args_list[0]
    tags = kwargs["tags"]
    assert tags == ["sisyphus", "intake", "REQ-9", "repo:phona/sisyphus", "ux:fast-track"]


@pytest.mark.asyncio
async def test_start_intake_strips_managed_tags_from_forwarded(monkeypatch):
    """REQ-ux-tags-injection: intent:* / REQ-* / role tag 不再次出现在转发段。"""
    from orchestrator.actions import start_intake as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "orchestrator.actions.start_intake.BKDClient", fake)
    monkeypatch.setattr(mod, "check_admission",
                        AsyncMock(return_value=_admit()))
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())

    await mod.start_intake(
        body=make_body(issue_id="intent-1"),
        req_id="REQ-9",
        tags=[
            "intent:intake", "REQ-9", "intake", "result:pass",
            "pr:phona/foo#1", "repo:phona/foo",
        ],
        ctx={},
    )
    _, kwargs = fake.update_issue.call_args_list[0]
    tags = kwargs["tags"]
    # 基础三件套 + 仅 repo: hint 转发
    assert tags == ["sisyphus", "intake", "REQ-9", "repo:phona/foo"]
    # 没有重复 / 不该出现的 sisyphus-managed
    assert tags.count("intake") == 1
    assert tags.count("REQ-9") == 1
    assert "intent:intake" not in tags
    assert "result:pass" not in tags
    assert "pr:phona/foo#1" not in tags


@pytest.mark.asyncio
async def test_start_intake_admission_denied_emits_escalate(monkeypatch):
    """admission deny → emit VERIFY_ESCALATE，不 dispatch BKD agent / 不建 runner。"""
    from orchestrator.actions import start_intake as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "orchestrator.actions.start_intake.BKDClient", fake)
    monkeypatch.setattr(mod, "check_admission",
                        AsyncMock(return_value=_deny()))
    update_ctx = AsyncMock()
    monkeypatch.setattr(mod.req_state, "update_context", update_ctx)
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())

    out = await mod.start_intake(body=make_body(issue_id="intent-1"),
                                 req_id="REQ-9", tags=[], ctx={})

    assert out["emit"] == Event.VERIFY_ESCALATE.value
    assert "admission denied" in out["reason"]
    assert "inflight-cap-exceeded" in out["reason"]
    # ctx.escalated_reason 必须落 ctx，让 escalate.py 派 reason tag
    update_ctx.assert_awaited_once()
    patch = update_ctx.await_args.args[2]
    assert patch["escalated_reason"].startswith("rate-limit:")
    # 不能调到 BKD（不浪费 agent token）
    fake.follow_up_issue.assert_not_awaited()


# ─── 5. start_analyze_with_finalized_intent smoke test ───────────────────────

@pytest.mark.asyncio
async def test_start_analyze_with_finalized_intent_creates_new_issue(monkeypatch):
    from orchestrator.actions import start_analyze_with_finalized_intent as mod

    fake = make_fake_bkd()
    fake.create_issue = AsyncMock(return_value=FakeIssue(id="analyze-new-1"))
    patch_bkd(monkeypatch, "orchestrator.actions.start_analyze_with_finalized_intent.BKDClient", fake)
    # REQ-issue-link-pr-quality-base-1777218242: success path stashes
    # analyze_issue_id via update_context.
    monkeypatch.setattr(mod.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())
    monkeypatch.setattr(mod.dispatch_slugs, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(mod.dispatch_slugs, "put", AsyncMock())

    ctx = {"intake_finalized_intent": _VALID_INTENT, "intent_title": "加 INTAKING stage"}
    out = await mod.start_analyze_with_finalized_intent(
        body=make_body(issue_id="intake-1"), req_id="REQ-9", tags=[], ctx=ctx,
    )
    assert out["analyze_issue_id"] == "analyze-new-1"
    # create_issue が呼ばれ（新 issue 作成）、元の intake issue は使わない
    fake.create_issue.assert_awaited_once()
    _, kwargs = fake.create_issue.await_args
    assert "analyze" in kwargs["tags"]
    assert "REQ-9" in kwargs["tags"]
    assert kwargs["use_worktree"] is True
    assert "[ANALYZE]" in kwargs["title"]

    # follow-up が呼ばれること
    fake.follow_up_issue.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_analyze_with_finalized_intent_forwards_hint_tags(monkeypatch):
    """REQ-ux-tags-injection: 创新 analyze issue 时 hint tag 跟着进 tags 数组。"""
    from orchestrator.actions import start_analyze_with_finalized_intent as mod

    fake = make_fake_bkd()
    fake.create_issue = AsyncMock(return_value=FakeIssue(id="analyze-new-1"))
    patch_bkd(monkeypatch, "orchestrator.actions.start_analyze_with_finalized_intent.BKDClient", fake)
    monkeypatch.setattr(mod.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())
    monkeypatch.setattr(mod.dispatch_slugs, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(mod.dispatch_slugs, "put", AsyncMock())

    ctx = {"intake_finalized_intent": _VALID_INTENT}
    # 模拟 intake completion webhook：tags 含 result:pass + intake role + 用户 hint
    body_tags = [
        "sisyphus", "intake", "REQ-9", "result:pass",
        "repo:phona/foo", "ux:fast-track", "spec_home_repo:phona/foo",
    ]
    await mod.start_analyze_with_finalized_intent(
        body=make_body(issue_id="intake-1"), req_id="REQ-9",
        tags=body_tags, ctx=ctx,
    )
    _, kwargs = fake.create_issue.await_args
    tags = kwargs["tags"]
    # 基础 + 转发段
    assert tags == [
        "analyze", "REQ-9",
        "repo:phona/foo", "ux:fast-track", "spec_home_repo:phona/foo",
    ]
    # sisyphus-managed 不重复转发
    assert "intake" not in tags
    assert "result:pass" not in tags
    assert "sisyphus" not in tags  # create_issue 自动注入，callsite 不传


@pytest.mark.asyncio
async def test_start_analyze_missing_finalized_intent_escalates(monkeypatch):
    """ctx に intake_finalized_intent がない → emit VERIFY_ESCALATE。"""
    from orchestrator.actions import start_analyze_with_finalized_intent as mod

    out = await mod.start_analyze_with_finalized_intent(
        body=make_body(), req_id="REQ-9", tags=[], ctx={},
    )
    assert out["emit"] == Event.VERIFY_ESCALATE.value
    assert "intake_finalized_intent" in out["reason"]


@pytest.mark.asyncio
async def test_start_analyze_none_ctx_escalates(monkeypatch):
    """ctx=None → emit VERIFY_ESCALATE（ctx が None の場合も安全に処理）。"""
    from orchestrator.actions import start_analyze_with_finalized_intent as mod

    out = await mod.start_analyze_with_finalized_intent(
        body=make_body(), req_id="REQ-9", tags=[], ctx=None,
    )
    assert out["emit"] == Event.VERIFY_ESCALATE.value


# ─── 6. intake prompt が render できること ────────────────────────────────────

def test_intake_prompt_renders():
    from orchestrator.prompts import render
    out = render(
        "intake.md.j2",
        req_id="REQ-42",
        project_id="proj-1",
        project_alias="proj-1",
        issue_id="issue-1",
        aissh_server_id="srv-123",
    )
    assert "REQ-42" in out
    assert "intake-agent" in out
    assert "finalized intent" in out
    assert "result:pass" in out
