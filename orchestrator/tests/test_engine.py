"""engine.step + emit chain：用 fake pool 验链路推进。

不打 BKD，不打 Postgres。把 actions REGISTRY 临时替换成 stub 以隔离副作用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from orchestrator import engine
from orchestrator.actions import REGISTRY
from orchestrator.state import Event, ReqState


# ─── In-memory pool stub ─────────────────────────────────────────────────
@dataclass
class FakeReq:
    state: str = ReqState.SPECS_RUNNING.value
    history: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)


class FakePool:
    """模拟 asyncpg.Pool 的 fetchrow / execute，仅支持 req_state 的 CAS UPDATE 和 SELECT。"""

    def __init__(self, initial: dict[str, FakeReq]):
        self.rows = initial

    async def fetchrow(self, sql: str, *args):
        sql = sql.strip()
        if sql.startswith("SELECT"):
            req_id = args[0]
            r = self.rows.get(req_id)
            if r is None:
                return None
            return {
                "req_id": req_id, "project_id": "p", "state": r.state,
                "history": json.dumps(r.history), "context": json.dumps(r.context),
                "created_at": None, "updated_at": None,
            }
        if sql.startswith("UPDATE req_state"):
            req_id, expected, next_state, history_json, ctx_param = args
            r = self.rows.get(req_id)
            if r is None or r.state != expected:
                return None
            r.state = next_state
            r.history.extend(json.loads(history_json))
            try:
                patch = json.loads(ctx_param)
                if isinstance(patch, dict):
                    r.context.update(patch)
            except (json.JSONDecodeError, TypeError):
                pass
            return {"req_id": req_id}
        raise NotImplementedError(sql[:60])

    async def execute(self, sql: str, *args):
        sql = sql.strip()
        if sql.startswith("UPDATE req_state SET context"):
            req_id, patch_json = args
            patch = json.loads(patch_json)
            r = self.rows.get(req_id)
            if r:
                r.context.update(patch)
            return
        raise NotImplementedError(sql[:60])


# ─── stub action：记录调用 + 可选 emit ───────────────────────────────────
@pytest.fixture
def stub_actions(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def make_stub(name, emit=None):
        async def _stub(*, body, req_id, tags, ctx):
            calls.append((name, {"req_id": req_id, "ctx": dict(ctx)}))
            return {"emit": emit} if emit else {"ok": True}
        return _stub

    saved = dict(REGISTRY)
    REGISTRY.clear()
    yield calls, REGISTRY
    REGISTRY.clear()
    REGISTRY.update(saved)


@pytest.mark.asyncio
async def test_chain_emit_spec_to_dev(stub_actions):
    calls, reg = stub_actions

    async def mark_spec(*, body, req_id, tags, ctx):
        calls.append(("mark_spec_reviewed_and_check", {"req_id": req_id}))
        return {"emit": Event.SPEC_ALL_PASSED.value}

    async def create_dev(*, body, req_id, tags, ctx):
        calls.append(("create_dev", {"req_id": req_id}))
        return {"dev_issue_id": "dev-1"}

    reg["mark_spec_reviewed_and_check"] = mark_spec
    reg["create_dev"] = create_dev

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPECS_RUNNING.value)})

    body = type("B", (), {"issueId": "spec-1", "projectId": "p", "event": "session.completed"})()
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["contract-test", "REQ-1"], cur_state=ReqState.SPECS_RUNNING,
        ctx={}, event=Event.SPEC_DONE,
    )

    # 1. mark_spec ran, emitted SPEC_ALL_PASSED
    # 2. engine.step recursed → create_dev ran
    assert [n for n, _ in calls] == ["mark_spec_reviewed_and_check", "create_dev"]
    assert pool.rows["REQ-1"].state == ReqState.DEV_RUNNING.value
    assert result["chained"]["action"] == "create_dev"


@pytest.mark.asyncio
async def test_illegal_transition_skips(stub_actions):
    pool = FakePool({"REQ-1": FakeReq(state=ReqState.DONE.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.completed"})()
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.DONE, ctx={}, event=Event.DEV_DONE,
    )
    assert result["action"] == "skip"
    assert "no transition" in result["reason"]


@pytest.mark.asyncio
async def test_cas_failure_skips(stub_actions):
    """expected != actual → CAS 不推进 → skip。"""
    pool = FakePool({"REQ-1": FakeReq(state=ReqState.DEV_RUNNING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.completed"})()
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPECS_RUNNING,  # 与实际 DEV_RUNNING 不一致
        ctx={}, event=Event.SPEC_ALL_PASSED,
    )
    assert result["action"] == "skip"
    assert "concurrent" in result["reason"]


@pytest.mark.asyncio
async def test_recursion_depth_guard(stub_actions, monkeypatch):
    """防 emit 死循环。"""
    calls, reg = stub_actions

    # 自指环 emit（拿正常的 transition 起手，每次都 emit 一个会再 emit 的事件）
    async def loopy(*, body, req_id, tags, ctx):
        calls.append(("loopy", {}))
        return {"emit": Event.SPEC_ALL_PASSED.value}

    # 让 SPEC_ALL_PASSED 也走 loopy（覆盖 create_dev）
    reg["create_dev"] = loopy
    reg["mark_spec_reviewed_and_check"] = loopy
    # mock state.decide 让 DEV_RUNNING + SPEC_ALL_PASSED 也合法走 create_dev（自指环）
    from orchestrator import state as state_mod
    monkeypatch.setitem(
        state_mod.TRANSITIONS,
        (ReqState.DEV_RUNNING, Event.SPEC_ALL_PASSED),
        state_mod.Transition(ReqState.DEV_RUNNING, "create_dev"),
    )

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPECS_RUNNING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.completed"})()
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPECS_RUNNING, ctx={}, event=Event.SPEC_DONE,
    )
    # depth 限制 4 次，加上首次共 5 次以内
    assert len(calls) <= 6
