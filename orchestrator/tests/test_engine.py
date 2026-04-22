"""engine.step + emit chain：用 fake pool 验链路推进。

不打 BKD，不打 Postgres。把 actions REGISTRY 临时替换成 stub 以隔离副作用。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator import engine, k8s_runner
from orchestrator.actions import ACTION_META, REGISTRY
from orchestrator.state import Event, ReqState


# ─── In-memory pool stub ─────────────────────────────────────────────────
@dataclass
class FakeReq:
    state: str = ReqState.SPECS_RUNNING.value
    history: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)


class FakePool:
    """模拟 asyncpg.Pool 的 fetchrow / execute，支持 req_state CAS + ctx patch + M9 action_retries。"""

    def __init__(self, initial: dict[str, FakeReq]):
        self.rows = initial

    async def fetchrow(self, sql: str, *args):
        sql_stripped = sql.strip()
        # M9：action_retries bump — `UPDATE req_state SET context = jsonb_set(...) RETURNING new_round`
        if "action_retries" in sql and "RETURNING" in sql:
            req_id, action_name = args
            r = self.rows.get(req_id)
            if r is None:
                return None
            retries = r.context.setdefault("action_retries", {})
            retries[action_name] = int(retries.get(action_name, 0)) + 1
            return {"new_round": retries[action_name]}
        if sql_stripped.startswith("SELECT"):
            req_id = args[0]
            r = self.rows.get(req_id)
            if r is None:
                return None
            return {
                "req_id": req_id, "project_id": "p", "state": r.state,
                "history": json.dumps(r.history), "context": json.dumps(r.context),
                "created_at": None, "updated_at": None,
            }
        if sql_stripped.startswith("UPDATE req_state"):
            # 4 个参数（无 ctx_patch）或 5 个参数（带 ctx_patch）— 跟真实 cas_transition 对齐
            req_id, expected, next_state, history_json, *rest = args
            r = self.rows.get(req_id)
            if r is None or r.state != expected:
                return None
            r.state = next_state
            r.history.extend(json.loads(history_json))
            if rest:
                try:
                    patch = json.loads(rest[0])
                    if isinstance(patch, dict):
                        r.context.update(patch)
                except (json.JSONDecodeError, TypeError):
                    pass
            return {"req_id": req_id}
        raise NotImplementedError(sql[:60])

    async def execute(self, sql: str, *args):
        sql_stripped = sql.strip()
        # M9：reset_action_retry — `UPDATE req_state SET context = jsonb_set(context, '{action_retries}', ... - $2)`
        if "action_retries" in sql and "jsonb_set" in sql:
            req_id, action_name = args
            r = self.rows.get(req_id)
            if r:
                retries = r.context.get("action_retries", {})
                if isinstance(retries, dict):
                    retries.pop(action_name, None)
            return
        # update_context：`UPDATE req_state SET context = context || $2::jsonb`
        if sql_stripped.startswith("UPDATE req_state SET context"):
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

    saved_reg = dict(REGISTRY)
    saved_meta = dict(ACTION_META)
    REGISTRY.clear()
    ACTION_META.clear()
    yield calls, REGISTRY
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(saved_reg)
    ACTION_META.update(saved_meta)


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
        tags=["contract-spec", "REQ-1"], cur_state=ReqState.SPECS_RUNNING,
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


# ─── M10: terminal state 即时 cleanup ─────────────────────────────────


@pytest.fixture
def mock_runner_controller():
    """注入 fake controller，断言 cleanup_runner 调用参数。"""
    fake = MagicMock()
    fake.cleanup_runner = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


async def _drain_tasks() -> None:
    """让 fire-and-forget 的 asyncio.create_task 跑完。"""
    # 收集除当前外所有 task；engine.step 用 create_task 起 cleanup
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_terminal_done_triggers_cleanup_no_retain(stub_actions, mock_runner_controller):
    """ARCHIVING + ARCHIVE_DONE → DONE 应触发 cleanup_runner(retain_pvc=False)。"""
    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ARCHIVING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.completed"})()
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.ARCHIVING, ctx={}, event=Event.ARCHIVE_DONE,
    )
    await _drain_tasks()

    assert pool.rows["REQ-1"].state == ReqState.DONE.value
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=False,
    )


@pytest.mark.asyncio
async def test_terminal_escalated_triggers_cleanup_retain_pvc(
    stub_actions, mock_runner_controller,
):
    """SESSION_FAILED → ESCALATED 应触发 cleanup_runner(retain_pvc=True)。"""
    calls, reg = stub_actions

    async def escalate(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id}))
        return {"escalated": True}

    reg["escalate"] = escalate

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.DEV_RUNNING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.failed"})()
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.DEV_RUNNING, ctx={}, event=Event.SESSION_FAILED,
    )
    await _drain_tasks()

    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )


@pytest.mark.asyncio
async def test_non_terminal_does_not_trigger_cleanup(
    stub_actions, mock_runner_controller,
):
    """非 terminal 转移不该 cleanup（如 DEV_RUNNING + DEV_DONE → STAGING_TEST_RUNNING）。"""
    calls, reg = stub_actions

    async def create_staging_test(*, body, req_id, tags, ctx):
        calls.append(("create_staging_test", {"req_id": req_id}))
        return {"ok": True}

    reg["create_staging_test"] = create_staging_test

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.DEV_RUNNING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.completed"})()
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.DEV_RUNNING, ctx={}, event=Event.DEV_DONE,
    )
    await _drain_tasks()

    mock_runner_controller.cleanup_runner.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_no_controller_safe(stub_actions, monkeypatch):
    """没 K8s controller（dev / 测试）→ 静默跳过，不报错。"""
    k8s_runner.set_controller(None)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ARCHIVING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.completed"})()
    # 不抛 = ok
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.ARCHIVING, ctx={}, event=Event.ARCHIVE_DONE,
    )
    await _drain_tasks()
    assert pool.rows["REQ-1"].state == ReqState.DONE.value


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_block_engine(stub_actions, mock_runner_controller):
    """cleanup_runner 抛错 → engine 不受影响（fire-and-forget）。"""
    mock_runner_controller.cleanup_runner = AsyncMock(side_effect=RuntimeError("kapow"))

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ARCHIVING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.completed"})()
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.ARCHIVING, ctx={}, event=Event.ARCHIVE_DONE,
    )
    await _drain_tasks()

    assert pool.rows["REQ-1"].state == ReqState.DONE.value
    assert result["next_state"] == ReqState.DONE.value


# ═══════════════════════════════════════════════════════════════════════
# M9: engine action-fail retry / escalate
# ═══════════════════════════════════════════════════════════════════════


@pytest.fixture
def enable_retry(monkeypatch):
    """临时打开 retry_enabled，缩短测试 backoff。"""
    from orchestrator import engine as eng
    from orchestrator.config import settings as cfg
    monkeypatch.setattr(cfg, "retry_enabled", True)
    monkeypatch.setattr(cfg, "retry_action_max_rounds", 3)
    # 用 0 backoff 加速测试（policy 会根据 round 计出 backoff，我们 patch asyncio.sleep）
    orig_sleep = eng.asyncio.sleep

    async def _fast_sleep(_sec):
        await orig_sleep(0)

    monkeypatch.setattr(eng.asyncio, "sleep", _fast_sleep)


@pytest.mark.asyncio
async def test_action_fail_retries_transient_then_succeeds(stub_actions, enable_retry):
    """idempotent action 抛 TimeoutError → policy retry → 第 2 次成功 → 状态推进。"""
    _calls, reg = stub_actions
    from orchestrator.actions import ACTION_META

    attempts = {"n": 0}

    async def flaky(*, body, req_id, tags, ctx):
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise TimeoutError("pod not ready in 120s")
        return {"issue_id": "i-1"}

    # 注册 start_analyze stub + 标 idempotent=True
    reg["start_analyze"] = flaky
    ACTION_META["start_analyze"] = {"idempotent": True}

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INIT.value)})
    body = type("B", (), {"issueId": "i-intent", "projectId": "p", "event": "intent.analyze"})()

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=["intent:analyze", "REQ-1"],
        cur_state=ReqState.INIT, ctx={}, event=Event.INTENT_ANALYZE,
    )

    assert attempts["n"] == 2, "handler should be retried once after first TimeoutError"
    # 成功路径：action 字段 = 注册的 transition.action
    assert result["action"] == "start_analyze"
    # 状态已推进到 ANALYZING（cas 在重试前就做过，不会回滚）
    assert pool.rows["REQ-1"].state == ReqState.ANALYZING.value
    # counter 已清零（成功后 reset）
    assert pool.rows["REQ-1"].context.get("action_retries", {}).get("start_analyze", 0) == 0


@pytest.mark.asyncio
async def test_action_fail_non_idempotent_escalates_immediately(stub_actions, enable_retry):
    """非幂等 action（create_dev）抛任何异常 → 不重试，直接 emit SESSION_FAILED。"""
    calls, reg = stub_actions
    from orchestrator.actions import ACTION_META

    attempts = {"n": 0}

    async def broken_create_dev(*, body, req_id, tags, ctx):
        attempts["n"] += 1
        raise TimeoutError("BKD API timeout")

    async def escalate_stub(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id}))
        return {"escalated": True, "reason": "action_fail"}

    reg["create_dev"] = broken_create_dev
    reg["escalate"] = escalate_stub
    ACTION_META["create_dev"] = {"idempotent": False}
    ACTION_META["escalate"] = {"idempotent": True}

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPECS_RUNNING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "spec.all-passed"})()

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPECS_RUNNING, ctx={}, event=Event.SPEC_ALL_PASSED,
    )

    assert attempts["n"] == 1, "non-idempotent should not retry"
    assert result["action"] == "error"
    assert result["escalated"] is True
    # 链式 SESSION_FAILED → ESCALATED + escalate action 被调用
    assert any(n == "escalate" for n, _ in calls)
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value


@pytest.mark.asyncio
async def test_action_fail_exceeds_max_rounds_escalates(stub_actions, enable_retry, monkeypatch):
    """idempotent action + 一直 fail → 超 max_rounds → escalate。"""
    calls, reg = stub_actions
    from orchestrator.actions import ACTION_META
    from orchestrator.config import settings as cfg

    # 把 max_rounds 缩到 2，加速测试
    monkeypatch.setattr(cfg, "retry_action_max_rounds", 2)

    attempts = {"n": 0}

    async def always_fails(*, body, req_id, tags, ctx):
        attempts["n"] += 1
        raise TimeoutError(f"still not ready attempt={attempts['n']}")

    async def escalate_stub(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id}))
        return {"escalated": True}

    reg["start_analyze"] = always_fails
    reg["escalate"] = escalate_stub
    ACTION_META["start_analyze"] = {"idempotent": True}
    ACTION_META["escalate"] = {"idempotent": True}

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INIT.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "intent.analyze"})()

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.INIT, ctx={}, event=Event.INTENT_ANALYZE,
    )

    # max_rounds=2 → round 0 retry, round 1 retry, round 2 escalate = 3 次调用
    assert attempts["n"] == 3, f"expected 3 attempts (2 retries + 1 final), got {attempts['n']}"
    assert result["action"] == "error"
    assert result["escalated"] is True
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value


@pytest.mark.asyncio
async def test_action_fail_non_transient_escalates_no_retry(stub_actions, enable_retry):
    """idempotent action 抛非 transient 异常（ValueError）→ 不重试，直接 escalate。"""
    calls, reg = stub_actions
    from orchestrator.actions import ACTION_META

    attempts = {"n": 0}

    async def bad_handler(*, body, req_id, tags, ctx):
        attempts["n"] += 1
        raise ValueError("bad ctx field")

    async def escalate_stub(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id}))
        return {"escalated": True}

    reg["start_analyze"] = bad_handler
    reg["escalate"] = escalate_stub
    ACTION_META["start_analyze"] = {"idempotent": True}
    ACTION_META["escalate"] = {"idempotent": True}

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INIT.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "intent.analyze"})()

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.INIT, ctx={}, event=Event.INTENT_ANALYZE,
    )

    assert attempts["n"] == 1, "non-transient should not retry"
    assert result["escalated"] is True


@pytest.mark.asyncio
async def test_action_fail_retry_disabled_keeps_old_behavior(stub_actions):
    """retry_enabled=False（默认）→ 旧行为：一次 fail 就 error，不重试，不 escalate。"""
    _calls, reg = stub_actions
    from orchestrator.actions import ACTION_META

    async def flaky(*, body, req_id, tags, ctx):
        raise TimeoutError("pod not ready")

    reg["start_analyze"] = flaky
    ACTION_META["start_analyze"] = {"idempotent": True}

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INIT.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "intent.analyze"})()

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.INIT, ctx={}, event=Event.INTENT_ANALYZE,
    )

    # 旧行为：action=error 但 escalated 未设（没链式 emit）
    assert result["action"] == "error"
    assert "escalated" not in result
    # 状态保持 ANALYZING（cas 已推进但 escalate 未触发）
    assert pool.rows["REQ-1"].state == ReqState.ANALYZING.value


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
    # depth 限制 12 次，加上首次共 13 次以内（比例足以容纳 test_mode 全跳 7 emit）
    assert len(calls) <= 14
