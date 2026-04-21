"""admin endpoints 烟测：emit / escalate / get-req。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from orchestrator.admin import EmitBody, _FakeBody, force_escalate, get_req


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
