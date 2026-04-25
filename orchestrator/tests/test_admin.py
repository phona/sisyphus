"""admin endpoints 烟测：emit / escalate / complete / get-req。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from orchestrator.admin import (
    CompleteBody,
    EmitBody,
    _FakeBody,
    complete_req,
    force_escalate,
    get_req,
)
from orchestrator.state import ReqState


def test_fakebody_shape():
    fb = _FakeBody("REQ-1", "p")
    assert fb.issueId.startswith("admin-REQ-1")
    assert fb.projectId == "p"
    assert fb.event == "admin.inject"


def test_emit_body_validates():
    from pydantic import ValidationError
    EmitBody(event="ci-int.pass")
    with pytest.raises(ValidationError):
        EmitBody()  # missing event


@pytest.mark.asyncio
async def test_emit_unknown_event_400(monkeypatch):
    """未知 event 应返 400."""
    from orchestrator import admin as admin_mod
    # token 校验跳过
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)
    with pytest.raises(HTTPException) as ei:
        await admin_mod.emit_event("REQ-1", EmitBody(event="bogus"), authorization="Bearer x")
    assert ei.value.status_code == 400
    assert "valid" in ei.value.detail


@pytest.mark.asyncio
async def test_force_escalate_404_when_not_found(monkeypatch):
    from orchestrator import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    async def _no(*a, **kw):
        return None
    monkeypatch.setattr("orchestrator.admin.req_state.get", _no)

    class FakePool:
        pass
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: FakePool())

    with pytest.raises(HTTPException) as ei:
        await force_escalate("REQ-X", authorization="Bearer x")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_get_req_404(monkeypatch):
    from orchestrator import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    async def _no(*a, **kw):
        return None
    monkeypatch.setattr("orchestrator.admin.req_state.get", _no)

    class FakePool:
        pass
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: FakePool())

    with pytest.raises(HTTPException) as ei:
        await get_req("REQ-X", authorization="Bearer x")
    assert ei.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# /admin/req/{req_id}/complete tests (REQ-admin-complete-endpoint-1777117709)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class _FakeRow:
    req_id: str
    project_id: str
    state: ReqState
    history: list = None
    context: dict = None
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        if self.history is None:
            self.history = []
        if self.context is None:
            self.context = {}
        if self.created_at is None:
            self.created_at = datetime.now(UTC)
        if self.updated_at is None:
            self.updated_at = datetime.now(UTC)


class _FakePool:
    """记录 execute 调用 + 返回 fake 'UPDATE 1' 默认。"""

    def __init__(self, execute_return: str = "UPDATE 1"):
        self.executed: list[tuple] = []
        self._execute_return = execute_return

    async def execute(self, sql: str, *args) -> str:
        self.executed.append((sql, args))
        return self._execute_return


def _bypass_auth_and_pool(monkeypatch, pool: _FakePool, row: _FakeRow | None):
    from orchestrator import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    async def _get(_pool, _req_id):
        return row
    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)


@pytest.mark.asyncio
async def test_complete_404_when_not_found(monkeypatch):
    """ACE-S4: REQ 不存在 → 404."""
    pool = _FakePool()
    _bypass_auth_and_pool(monkeypatch, pool, row=None)

    with pytest.raises(HTTPException) as ei:
        await complete_req("REQ-MISSING", body=None, authorization="Bearer x")
    assert ei.value.status_code == 404
    assert "not found" in ei.value.detail
    assert pool.executed == []  # 没改 DB


@pytest.mark.asyncio
async def test_complete_noop_when_already_done(monkeypatch):
    """ACE-S2: state=done → 200 noop, 没 SQL UPDATE 没 cleanup task."""
    pool = _FakePool()
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.DONE)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    cleanup_calls = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append((req_id, terminal_state))

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    result = await complete_req("REQ-X", body=None, authorization="Bearer x")
    assert result == {"action": "noop", "state": "already done"}
    assert pool.executed == []
    # 让 event loop 跑一帧确认没起 task
    await asyncio.sleep(0)
    assert cleanup_calls == []


@pytest.mark.asyncio
async def test_complete_409_when_not_escalated(monkeypatch):
    """ACE-S3: state=analyzing → 409 with hint about /escalate first."""
    pool = _FakePool()
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ANALYZING)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    with pytest.raises(HTTPException) as ei:
        await complete_req("REQ-X", body=None, authorization="Bearer x")
    assert ei.value.status_code == 409
    assert "analyzing" in ei.value.detail
    assert "/escalate" in ei.value.detail
    assert pool.executed == []


@pytest.mark.asyncio
async def test_complete_marks_done_and_triggers_cleanup(monkeypatch):
    """ACE-S1: 成功路径——SQL UPDATE 写 done + cleanup task scheduled."""
    pool = _FakePool(execute_return="UPDATE 1")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    cleanup_calls = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append((req_id, terminal_state))

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    result = await complete_req("REQ-X", body=None, authorization="Bearer x")

    assert result["action"] == "completed"
    assert result["from_state"] == "escalated"
    # SQL 调一次
    assert len(pool.executed) == 1
    sql, args = pool.executed[0]
    assert "UPDATE req_state" in sql
    assert "state='done'" in sql
    assert "WHERE req_id = $1 AND state = $4" in sql
    assert args[0] == "REQ-X"
    assert args[3] == ReqState.ESCALATED.value
    # cleanup task 已 schedule，跑一帧让它执行
    await asyncio.sleep(0)
    assert cleanup_calls == [("REQ-X", ReqState.DONE)]


@pytest.mark.asyncio
async def test_complete_writes_reason_in_context(monkeypatch):
    """ACE-S5: body.reason → ctx 里写 completed_reason_detail."""
    import json as _json

    pool = _FakePool(execute_return="UPDATE 1")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    async def _fake_cleanup(req_id, terminal_state):
        pass

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    body = CompleteBody(reason="superseded by REQ-Y")
    result = await complete_req("REQ-X", body=body, authorization="Bearer x")

    assert result["reason"] == "superseded by REQ-Y"
    _sql, args = pool.executed[0]
    # args[2] 是 ctx_patch JSON
    ctx = _json.loads(args[2])
    assert ctx["completed_reason"] == "admin"
    assert ctx["completed_reason_detail"] == "superseded by REQ-Y"
    assert ctx["completed_from_state"] == "escalated"


@pytest.mark.asyncio
async def test_complete_no_reason_omits_detail_field(monkeypatch):
    """ACE-S6: 没 reason → ctx 不写 completed_reason_detail（不留 null 字段）."""
    import json as _json

    pool = _FakePool(execute_return="UPDATE 1")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    async def _fake_cleanup(req_id, terminal_state):
        pass

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    await complete_req("REQ-X", body=None, authorization="Bearer x")
    _sql, args = pool.executed[0]
    ctx = _json.loads(args[2])
    assert ctx["completed_reason"] == "admin"
    assert "completed_reason_detail" not in ctx


@pytest.mark.asyncio
async def test_complete_history_entry_appended(monkeypatch):
    """ACE-S1: history 行追加 admin.complete event marker."""
    import json as _json

    pool = _FakePool(execute_return="UPDATE 1")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    async def _fake_cleanup(req_id, terminal_state):
        pass

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    await complete_req("REQ-X", body=None, authorization="Bearer x")
    _sql, args = pool.executed[0]
    history = _json.loads(args[1])
    assert isinstance(history, list) and len(history) == 1
    entry = history[0]
    assert entry["from"] == "escalated"
    assert entry["to"] == "done"
    assert entry["event"] == "admin.complete"
    assert entry["action"] is None
    assert "ts" in entry


@pytest.mark.asyncio
async def test_complete_concurrent_update_lost_returns_409(monkeypatch):
    """failure_modes.concurrent_complete: SQL UPDATE 影响 0 行（已被另一 caller 改） → 409 或 noop."""
    pool = _FakePool(execute_return="UPDATE 0")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)

    from orchestrator import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    # 第一次 get 返 escalated（precondition pass），UPDATE 后 reload 返 done（被并发改了）
    call_count = {"n": 0}

    async def _get(_pool, _req_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return row
        return _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.DONE)

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)

    # 并发对手已经把它推到 done → 我们应回 noop（200）
    result = await complete_req("REQ-X", body=None, authorization="Bearer x")
    assert result["action"] == "noop"


@pytest.mark.asyncio
async def test_complete_auth_check_before_db(monkeypatch):
    """ACE-S7: _verify_token 是第一步，失败时不调 req_state.get / pool."""
    from orchestrator import admin as admin_mod

    def _bad_token(_):
        raise HTTPException(status_code=401, detail="bad token")

    monkeypatch.setattr(admin_mod, "_verify_token", _bad_token)

    get_called = []

    async def _get(*a, **kw):
        get_called.append(1)
        return None

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: _FakePool())

    with pytest.raises(HTTPException) as ei:
        await complete_req("REQ-X", body=None, authorization=None)
    assert ei.value.status_code == 401
    assert get_called == []


def test_complete_body_optional_reason():
    """CompleteBody 的 reason 可省略."""
    assert CompleteBody().reason is None
    assert CompleteBody(reason="x").reason == "x"
