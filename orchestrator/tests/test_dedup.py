"""dedup store 单元测试 + webhook dedup 行为测试。

dedup.py 单元测试不打真 DB，用 FakePool 模拟 fetchrow/execute 序列。
webhook 级别的 dedup 测试用 monkeypatch 替换 dedup 模块函数。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from orchestrator.store import dedup

# ─── FakePool ───────────────────────────────────────────────────────────────

class FakePool:
    """轻量级 pool mock：依次返回预设的 fetchrow 值；记录 execute 调用。"""

    def __init__(self, fetchrow_seq=()):
        self._seq = list(fetchrow_seq)
        self._idx = 0
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        if self._idx < len(self._seq):
            val = self._seq[self._idx]
            self._idx += 1
            return val
        return None

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))


# ─── check_and_record ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dedup_check_and_record_new():
    """全新 event：INSERT 返行 → 'new'。"""
    pool = FakePool(fetchrow_seq=[{"event_id": "evt-1"}])
    result = await dedup.check_and_record(pool, "evt-1")
    assert result == "new"


@pytest.mark.asyncio
async def test_dedup_check_and_record_skip_processed():
    """event 已存在且 processed_at IS NOT NULL → 'skip'。"""
    pool = FakePool(fetchrow_seq=[
        None,  # INSERT ON CONFLICT DO NOTHING → no row returned
        {"processed_at": datetime.now(UTC)},  # SELECT
    ])
    result = await dedup.check_and_record(pool, "evt-2")
    assert result == "skip"


@pytest.mark.asyncio
async def test_dedup_check_and_record_retry_on_crash():
    """event 已存在但 processed_at IS NULL（上次崩溃）→ 'retry'。"""
    pool = FakePool(fetchrow_seq=[
        None,  # INSERT conflict
        {"processed_at": None},  # SELECT
    ])
    result = await dedup.check_and_record(pool, "evt-3")
    assert result == "retry"


# ─── mark_processed ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dedup_mark_processed():
    """mark_processed 执行 UPDATE event_seen SET processed_at = NOW()。"""
    pool = FakePool()
    await dedup.mark_processed(pool, "evt-4")

    assert len(pool.executed) == 1
    sql, args = pool.executed[0]
    assert "UPDATE event_seen" in sql
    assert "processed_at" in sql
    assert args == ("evt-4",)


# ─── webhook 级别 dedup 行为测试 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_webhook_dedup_skip_after_processed(monkeypatch):
    """check_and_record 返 'skip' → webhook 立即返 skip，不调 mark_processed。"""
    from orchestrator import webhook
    from orchestrator.store import db

    mark_called = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="skip"))
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock(side_effect=lambda *a: mark_called.append(a)))
    monkeypatch.setattr(db, "get_pool", lambda: FakePool())

    # 构造一个最简 Request mock
    class MockReq:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}
        async def json(self):
            return {
                "event": "session.completed",
                "issueId": "issue-abc",
                "projectId": "proj-1",
                "executionId": "exec-1",
            }

    import orchestrator.observability as obs
    monkeypatch.setattr(obs, "record_event", AsyncMock())

    resp = await webhook.webhook(MockReq())
    body = resp if isinstance(resp, dict) else resp.body

    # mark_processed 不应被调用（已成功处理的 skip 不再需要标记）
    assert not mark_called
    # 返回 skip action
    import json
    data = json.loads(body) if isinstance(body, bytes) else resp
    assert data.get("action") == "skip"


@pytest.mark.asyncio
async def test_webhook_dedup_retry_after_crash(monkeypatch):
    """check_and_record 返 'retry'（上次崩溃）→ handler 继续跑 + mark_processed 调用。"""
    import orchestrator.observability as obs
    from orchestrator import engine, webhook
    from orchestrator import router as router_lib
    from orchestrator.state import Event, ReqState
    from orchestrator.store import db
    from orchestrator.store import req_state as req_state_mod

    mark_called = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="retry"))
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock(side_effect=lambda *a: mark_called.append(a)))
    monkeypatch.setattr(db, "get_pool", lambda: FakePool())
    monkeypatch.setattr(obs, "record_event", AsyncMock())

    # BKD fetch
    class FakeBKD:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get_issue(self, *a, **kw):
            class R:
                tags: ClassVar = ["REQ-1", "analyze"]
            return R()
        async def update_issue(self, *a, **kw): pass

    monkeypatch.setattr(webhook, "BKDClient", FakeBKD)

    # router
    monkeypatch.setattr(router_lib, "extract_req_id", lambda tags, num=None: "REQ-1")
    monkeypatch.setattr(router_lib, "derive_event", lambda evt, tags: Event.INTENT_ANALYZE)

    # req_state
    class FakeRow:
        state = ReqState.INIT
        context: ClassVar = {}

    monkeypatch.setattr(req_state_mod, "get", AsyncMock(return_value=FakeRow()))
    monkeypatch.setattr(req_state_mod, "insert_init", AsyncMock())
    monkeypatch.setattr(req_state_mod, "update_context", AsyncMock())

    # engine.step → return ok（simulate 状态机推进成功）
    monkeypatch.setattr(engine, "step", AsyncMock(return_value={"action": "start_analyze"}))

    class MockReq:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}
        async def json(self):
            return {
                "event": "session.completed",
                "issueId": "issue-abc",
                "projectId": "proj-1",
                "executionId": "exec-1",
                "tags": ["REQ-1", "analyze"],
            }

    result = await webhook.webhook(MockReq())

    # mark_processed 必须被调
    assert mark_called, "mark_processed should be called after successful handler"
    assert result["action"] == "start_analyze"


@pytest.mark.asyncio
async def test_webhook_dedup_no_mark_on_crash(monkeypatch):
    """engine.step 抛异常 → mark_processed 不调（下次 BKD 重发走 retry 路径）。"""
    import orchestrator.observability as obs
    from orchestrator import engine, webhook
    from orchestrator import router as router_lib
    from orchestrator.state import Event, ReqState
    from orchestrator.store import db
    from orchestrator.store import req_state as req_state_mod

    mark_called = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="new"))
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock(side_effect=lambda *a: mark_called.append(a)))
    monkeypatch.setattr(db, "get_pool", lambda: FakePool())
    monkeypatch.setattr(obs, "record_event", AsyncMock())

    class FakeBKD:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get_issue(self, *a, **kw):
            class R:
                tags: ClassVar = ["REQ-1", "analyze"]
            return R()
        async def update_issue(self, *a, **kw): pass

    monkeypatch.setattr(webhook, "BKDClient", FakeBKD)
    monkeypatch.setattr(router_lib, "extract_req_id", lambda tags, num=None: "REQ-1")
    monkeypatch.setattr(router_lib, "derive_event", lambda evt, tags: Event.INTENT_ANALYZE)

    class FakeRow:
        state = ReqState.INIT
        context: ClassVar = {}

    monkeypatch.setattr(req_state_mod, "get", AsyncMock(return_value=FakeRow()))
    monkeypatch.setattr(req_state_mod, "insert_init", AsyncMock())
    monkeypatch.setattr(req_state_mod, "update_context", AsyncMock())

    # engine.step 抛异常模拟 handler crash
    monkeypatch.setattr(engine, "step", AsyncMock(side_effect=RuntimeError("handler crash")))

    class MockReq:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}
        async def json(self):
            return {
                "event": "session.completed",
                "issueId": "issue-abc",
                "projectId": "proj-1",
                "executionId": "exec-1",
                "tags": ["REQ-1", "analyze"],
            }

    with pytest.raises(RuntimeError, match="handler crash"):
        await webhook.webhook(MockReq())

    # mark_processed は呼ばれてはいけない
    assert not mark_called, "mark_processed must NOT be called when handler crashes"
