"""webhook verifier decision parse retry 逻辑单测。

REQ-fix-verifier-json-parse-1777420690：schema invalid 时自动 retry（最多 2 次）。
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from orchestrator import webhook
from orchestrator.state import Event


class _FakeReqStateRow:
    def __init__(self, context=None):
        self.state = "review-running"
        self.context = context or {}


class _FakePool:
    pass


class _FakeBKD:
    """Capture follow_up_issue + update_issue，其他方法默认空实现。"""

    captured_follow_up: ClassVar[list[dict]] = []
    captured_update: ClassVar[list[tuple]] = []
    raise_on_follow_up: ClassVar[bool] = False

    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def follow_up_issue(self, *, project_id, issue_id, prompt):
        if self.raise_on_follow_up:
            raise RuntimeError("BKD down")
        self.captured_follow_up.append({
            "project_id": project_id,
            "issue_id": issue_id,
            "prompt": prompt,
        })

    async def update_issue(self, *, project_id, issue_id, status_id):
        self.captured_update.append((project_id, issue_id, status_id))

    async def get_issue(self, project_id, issue_id):
        from orchestrator.bkd import Issue
        return Issue(
            id=issue_id, project_id=project_id, issue_number=1,
            title="t", status_id="working",
            tags=["verifier", "REQ-1", "verify:staging_test", "trigger:fail"],
            session_status="completed",
        )

    async def get_last_assistant_message(self, project_id, issue_id):
        return self._last_msg

    def set_last_msg(self, msg):
        self._last_msg = msg


@pytest.fixture(autouse=True)
def _reset_fake_bkd():
    _FakeBKD.captured_follow_up = []
    _FakeBKD.captured_update = []
    _FakeBKD.raise_on_follow_up = False


@pytest.fixture
def fake_bkd(monkeypatch):
    bkd = _FakeBKD()
    monkeypatch.setattr(webhook, "BKDClient", lambda *a, **kw: bkd)
    return bkd


@pytest.fixture
def fake_req_state(monkeypatch):
    """Mock req_state.get / update_context。"""
    rows: dict[str, _FakeReqStateRow] = {}
    ctx_updates: list = []

    async def fake_get(pool, req_id):
        return rows.get(req_id)

    async def fake_update(pool, req_id, patch):
        ctx_updates.append((req_id, patch))
        if req_id in rows:
            rows[req_id].context.update(patch)
        else:
            rows[req_id] = _FakeReqStateRow(context=patch)

    monkeypatch.setattr("orchestrator.webhook.req_state.get", fake_get)
    monkeypatch.setattr("orchestrator.webhook.req_state.update_context", fake_update)
    return rows, ctx_updates


@pytest.fixture
def fake_obs(monkeypatch):
    events: list = []

    async def fake_record(kind, *, req_id=None, issue_id=None, extras=None, **kw):
        events.append({"kind": kind, "req_id": req_id, "issue_id": issue_id, "extras": extras})

    monkeypatch.setattr("orchestrator.webhook.obs.record_event", fake_record)
    return events


@pytest.fixture
def fake_dedup(monkeypatch):
    async def fake_check(pool, eid):
        return "new"
    monkeypatch.setattr("orchestrator.webhook.dedup.check_and_record", fake_check)


class FakeBody:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


async def _call_webhook(body, monkeypatch):
    """绕过 auth / WebhookBody 校验直接调 webhook 核心逻辑。"""
    # 只跑 derive event + retry 逻辑，不跑完整 handler
    from orchestrator import router as router_lib
    tags = body.tags or []
    event = router_lib.derive_event(body.event, tags)

    decision_payload = None
    retry_worthy = False
    if event is None and body.event == "session.completed" and "verifier" in set(tags):
        decision_source = None
        async with webhook.BKDClient("", "") as bkd:
            if hasattr(bkd, "get_last_assistant_message"):
                decision_source = await bkd.get_last_assistant_message(body.projectId, body.issueId)
        event, decision_payload, why, retry_worthy = (
            router_lib.derive_verifier_event_with_retry_info(decision_source, tags)
        )

        if event == Event.VERIFY_ESCALATE and retry_worthy:
            retry_req_id = router_lib.extract_req_id(tags, body.issueNumber)
            if retry_req_id:
                try:
                    retry_row = await webhook.req_state.get(None, retry_req_id)
                    retry_ctx = retry_row.context or {} if retry_row else {}
                    retry_count = int(retry_ctx.get("verifier_parse_retry_count", 0))
                    if retry_count < 2:
                        async with webhook.BKDClient("", "") as bkd:
                            await bkd.follow_up_issue(
                                project_id=body.projectId,
                                issue_id=body.issueId,
                                prompt=webhook._VERIFIER_RETRY_PROMPT,
                            )
                        await webhook.req_state.update_context(
                            None, retry_req_id,
                            {"verifier_parse_retry_count": retry_count + 1},
                        )
                        await webhook.obs.record_event(
                            "verifier.decision.parse_retry",
                            req_id=retry_req_id,
                            issue_id=body.issueId,
                            extras={"retry_count": retry_count + 1, "reason": why},
                        )
                        return {"action": "skip", "reason": f"verifier_parse_retry_{retry_count + 1}"}
                except Exception:
                    pass
    return {"event": event, "decision": decision_payload, "retry_worthy": retry_worthy}


@pytest.mark.asyncio
async def test_retry_on_schema_invalid_first_time(fake_bkd, fake_req_state, fake_obs, fake_dedup, monkeypatch):
    """第一次 schema invalid → follow-up retry，返回 skip。"""
    rows, ctx_updates = fake_req_state
    rows["REQ-1"] = _FakeReqStateRow(context={})
    fake_bkd.set_last_msg("```json\n{\"action\": \"nope\"}\n```")

    body = FakeBody(
        event="session.completed", issueId="vfy-1", issueNumber=1,
        projectId="proj-1", tags=["verifier", "REQ-1", "verify:staging_test"],
    )
    result = await _call_webhook(body, monkeypatch)

    assert result["action"] == "skip"
    assert result["reason"] == "verifier_parse_retry_1"
    assert len(fake_bkd.captured_follow_up) == 1
    assert "valid JSON" in fake_bkd.captured_follow_up[0]["prompt"]
    # ctx 写了 retry_count=1
    assert any("verifier_parse_retry_count" in u[1] for u in ctx_updates)


@pytest.mark.asyncio
async def test_retry_on_schema_invalid_second_time(fake_bkd, fake_req_state, fake_obs, fake_dedup, monkeypatch):
    """第二次 schema invalid（retry_count=1）→ follow-up retry，返回 skip。"""
    rows, _ctx_updates = fake_req_state
    rows["REQ-1"] = _FakeReqStateRow(context={"verifier_parse_retry_count": 1})
    fake_bkd.set_last_msg("```json\n{\"action\": \"nope\"}\n```")

    body = FakeBody(
        event="session.completed", issueId="vfy-1", issueNumber=1,
        projectId="proj-1", tags=["verifier", "REQ-1", "verify:staging_test"],
    )
    result = await _call_webhook(body, monkeypatch)

    assert result["action"] == "skip"
    assert result["reason"] == "verifier_parse_retry_2"


@pytest.mark.asyncio
async def test_escalate_when_retry_exhausted(fake_bkd, fake_req_state, fake_obs, fake_dedup, monkeypatch):
    """第三次 schema invalid（retry_count=2）→ escalate，不再 retry。"""
    rows, _ctx_updates = fake_req_state
    rows["REQ-1"] = _FakeReqStateRow(context={"verifier_parse_retry_count": 2})
    fake_bkd.set_last_msg("```json\n{\"action\": \"nope\"}\n```")

    body = FakeBody(
        event="session.completed", issueId="vfy-1", issueNumber=1,
        projectId="proj-1", tags=["verifier", "REQ-1", "verify:staging_test"],
    )
    result = await _call_webhook(body, monkeypatch)

    assert result["event"] == Event.VERIFY_ESCALATE
    assert result["retry_worthy"] is True
    # 没有 follow-up
    assert len(fake_bkd.captured_follow_up) == 0


@pytest.mark.asyncio
async def test_no_retry_when_no_decision_found(fake_bkd, fake_req_state, fake_obs, fake_dedup, monkeypatch):
    """完全找不到 decision → 直接 escalate，不 retry。"""
    rows, _ctx_updates = fake_req_state
    rows["REQ-1"] = _FakeReqStateRow(context={})
    fake_bkd.set_last_msg("no json here at all")

    body = FakeBody(
        event="session.completed", issueId="vfy-1", issueNumber=1,
        projectId="proj-1", tags=["verifier", "REQ-1", "verify:staging_test"],
    )
    result = await _call_webhook(body, monkeypatch)

    assert result["event"] == Event.VERIFY_ESCALATE
    assert result["retry_worthy"] is False
    assert len(fake_bkd.captured_follow_up) == 0


@pytest.mark.asyncio
async def test_no_retry_when_valid_decision(fake_bkd, fake_req_state, fake_obs, fake_dedup, monkeypatch):
    """decision 合规 → 正常通过，不 retry。"""
    rows, _ctx_updates = fake_req_state
    rows["REQ-1"] = _FakeReqStateRow(context={})
    fake_bkd.set_last_msg('```json\n{"action": "pass", "fixer": null, "reason": "ok", "confidence": "high"}\n```')

    body = FakeBody(
        event="session.completed", issueId="vfy-1", issueNumber=1,
        projectId="proj-1", tags=["verifier", "REQ-1", "verify:staging_test"],
    )
    result = await _call_webhook(body, monkeypatch)

    assert result["event"] == Event.VERIFY_PASS
    assert result["retry_worthy"] is False
    assert len(fake_bkd.captured_follow_up) == 0
