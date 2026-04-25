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
    state: str = ReqState.SPEC_LINT_RUNNING.value
    history: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)


class FakePool:
    """模拟 asyncpg.Pool 的 fetchrow / execute，支持 req_state CAS + ctx patch。"""

    def __init__(self, initial: dict[str, FakeReq]):
        self.rows = initial

    async def fetchrow(self, sql: str, *args):
        sql_stripped = sql.strip()
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
async def test_chain_emit_spec_lint_pass(stub_actions):
    """spec-lint.pass 触发 start_challenger（M18：spec_lint → challenger → dev_cross_check）。"""
    calls, reg = stub_actions

    async def start_challenger(*, body, req_id, tags, ctx):
        calls.append(("start_challenger", {"req_id": req_id}))
        return {"challenger_issue_id": "ch-1"}

    reg["start_challenger"] = start_challenger

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})

    body = type("B", (), {"issueId": "spec-1", "projectId": "p", "event": "check.passed"})()
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["spec_lint", "REQ-1"], cur_state=ReqState.SPEC_LINT_RUNNING,
        ctx={}, event=Event.SPEC_LINT_PASS,
    )

    # start_challenger 被调用（M18 spec-lint.pass → CHALLENGER_RUNNING）
    assert [n for n, _ in calls] == ["start_challenger"]
    assert pool.rows["REQ-1"].state == ReqState.CHALLENGER_RUNNING.value
    assert result["action"] == "start_challenger"


@pytest.mark.asyncio
async def test_chain_emit_challenger_pass(stub_actions):
    """challenger.pass → create_dev_cross_check（M18：challenger 写完 contract 后接 dev_cross_check）。"""
    calls, reg = stub_actions

    async def create_dev_cross_check(*, body, req_id, tags, ctx):
        calls.append(("create_dev_cross_check", {"req_id": req_id}))
        return {"passed": True}

    reg["create_dev_cross_check"] = create_dev_cross_check

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.CHALLENGER_RUNNING.value)})
    body = type("B", (), {"issueId": "ch-1", "projectId": "p", "event": "session.completed"})()
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["challenger", "REQ-1", "result:pass"],
        cur_state=ReqState.CHALLENGER_RUNNING,
        ctx={}, event=Event.CHALLENGER_PASS,
    )

    assert [n for n, _ in calls] == ["create_dev_cross_check"]
    assert pool.rows["REQ-1"].state == ReqState.DEV_CROSS_CHECK_RUNNING.value
    assert result["action"] == "create_dev_cross_check"


@pytest.mark.asyncio
async def test_illegal_transition_skips(stub_actions):
    """terminal state DONE 不接受任何事件。"""
    pool = FakePool({"REQ-1": FakeReq(state=ReqState.DONE.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "check.passed"})()
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.DONE, ctx={}, event=Event.STAGING_TEST_PASS,
    )
    assert result["action"] == "skip"
    assert "no transition" in result["reason"]


@pytest.mark.asyncio
async def test_cas_failure_skips(stub_actions):
    """expected != actual → CAS 不推进 → skip（并发抢占）。"""
    pool = FakePool({"REQ-1": FakeReq(state=ReqState.STAGING_TEST_RUNNING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "check.passed"})()
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING,  # 与实际 STAGING_TEST_RUNNING 不一致
        ctx={}, event=Event.SPEC_LINT_PASS,
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
    """SESSION_FAILED → escalate action 决定真 escalate（mock 模拟 retry 用完）→ cleanup。

    新行为后：transition 是 self-loop，escalate action 内部根据 ctx.auto_retry_count 决定。
    本测试 mock escalate stub 模拟"action 完成真 ESCALATED + 触发 cleanup"。
    """
    calls, reg = stub_actions

    async def escalate(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id}))
        # 模拟真 escalate 路径：手动 CAS + cleanup
        from orchestrator import k8s_runner as krunner
        from orchestrator.store import req_state
        await req_state.cas_transition(
            None, req_id, ReqState.STAGING_TEST_RUNNING, ReqState.ESCALATED,
            Event.SESSION_FAILED, "escalate",
        )
        try:
            rc = krunner.get_controller()
            await rc.cleanup_runner(req_id, retain_pvc=True)
        except Exception:
            pass
        return {"escalated": True}

    reg["escalate"] = escalate

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.STAGING_TEST_RUNNING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.failed"})()
    # FakePool 的 cas_transition 会被 hit；req_state.cas_transition 调用 pool 上的方法
    # 我们直接 monkey-pool 让 cas 真改 state
    import orchestrator.store.req_state as rs_mod
    orig = rs_mod.cas_transition
    async def fake_cas(p, rid, expected, target, evt, action, context_patch=None):
        if rid in pool.rows and pool.rows[rid].state == expected.value:
            pool.rows[rid].state = target.value
            return True
        return False
    rs_mod.cas_transition = fake_cas
    try:
        await engine.step(
            pool, body=body, req_id="REQ-1", project_id="p", tags=[],
            cur_state=ReqState.STAGING_TEST_RUNNING, ctx={}, event=Event.SESSION_FAILED,
        )
        await _drain_tasks()
    finally:
        rs_mod.cas_transition = orig

    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )


@pytest.mark.asyncio
async def test_non_terminal_does_not_trigger_cleanup(
    stub_actions, mock_runner_controller,
):
    """非 terminal 转移不该 cleanup（如 STAGING_TEST_RUNNING + staging-test.pass → PR_CI_RUNNING）。"""
    calls, reg = stub_actions

    async def create_pr_ci_watch(*, body, req_id, tags, ctx):
        calls.append(("create_pr_ci_watch", {"req_id": req_id}))
        return {"ok": True}

    reg["create_pr_ci_watch"] = create_pr_ci_watch

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.STAGING_TEST_RUNNING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "check.passed"})()
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.STAGING_TEST_RUNNING, ctx={}, event=Event.STAGING_TEST_PASS,
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
# M14c: action handler 抛异常 → SESSION_FAILED → ESCALATED（无自动重试）
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_action_fail_escalates_no_retry(stub_actions):
    """任何 action（不分 idempotent / 异常类型）抛错 → 链式 emit SESSION_FAILED → ESCALATED。"""
    calls, reg = stub_actions
    from orchestrator.actions import ACTION_META

    attempts = {"n": 0}

    async def broken(*, body, req_id, tags, ctx):
        attempts["n"] += 1
        raise TimeoutError("pod not ready")

    async def escalate_stub(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id}))
        return {"escalated": True, "reason": "session-failed"}

    reg["start_analyze"] = broken
    reg["escalate"] = escalate_stub
    ACTION_META["start_analyze"] = {"idempotent": True}
    ACTION_META["escalate"] = {"idempotent": True}

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INIT.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "intent.analyze"})()

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.INIT, ctx={}, event=Event.INTENT_ANALYZE,
    )

    assert attempts["n"] == 1, "M14c: 不再自动重试，一次失败就 escalate (action 内自决)"
    assert result["action"] == "error"
    assert result["escalated"] is True
    assert any(n == "escalate" for n, _ in calls)
    # 新行为：escalate action stub 没真 CAS 推 ESCALATED（stub 太简单），
    # 真生产代码里 escalate 会自己 CAS。这里只验 action 被调用 + 链式 SESSION_FAILED 起。
    # state 仍 'analyzing'（self-loop 后 stub escalate 没改）—— 这是 stub 的局限
    assert pool.rows["REQ-1"].state == ReqState.ANALYZING.value


# ═══════════════════════════════════════════════════════════════════════
# REQ-escalate-reason-audit: engine 在 dispatch escalate action 前
# 按状态机 Event 预填 ctx.escalated_reason 为 canonical slug
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cur_state,event,expected_reason",
    [
        (ReqState.INTAKING, Event.INTAKE_FAIL, "intake-fail"),
        (ReqState.PR_CI_RUNNING, Event.PR_CI_TIMEOUT, "pr-ci-timeout"),
        (ReqState.ACCEPT_RUNNING, Event.ACCEPT_ENV_UP_FAIL, "accept-env-up-fail"),
        (ReqState.REVIEW_RUNNING, Event.VERIFY_ESCALATE, "verifier-decision-escalate"),
    ],
)
async def test_engine_prepopulates_escalated_reason(
    stub_actions, cur_state, event, expected_reason,
):
    """非 SESSION_FAILED escalate 路径：engine 在 dispatch 前按 Event 预填 ctx.escalated_reason
    （body.event 是 BKD webhook 类型，slug fallback 给的是 misleading 值如 'session-completed'）。
    """
    calls, reg = stub_actions

    async def escalate_stub(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id, "ctx": dict(ctx)}))
        return {"escalated": True, "reason": ctx.get("escalated_reason")}

    reg["escalate"] = escalate_stub

    pool = FakePool({"REQ-1": FakeReq(state=cur_state.value, context={})})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.completed"})()

    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=cur_state, ctx={}, event=event,
    )

    # action 接到的 ctx 已含 canonical reason
    escalate_call_ctx = next(c for n, c in calls if n == "escalate")["ctx"]
    assert escalate_call_ctx["escalated_reason"] == expected_reason
    # DB 已持久化（FakePool.execute 模拟 update_context jsonb merge）
    assert pool.rows["REQ-1"].context["escalated_reason"] == expected_reason


@pytest.mark.asyncio
async def test_engine_session_failed_does_not_prepopulate(stub_actions):
    """SESSION_FAILED 路径：engine 不预填 ctx.escalated_reason。escalate.py 用 body.event
    canonical（session.failed / watchdog.stuck）自行处理。"""
    calls, reg = stub_actions

    async def escalate_stub(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id, "ctx": dict(ctx)}))
        return {"escalated": True}

    reg["escalate"] = escalate_stub

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.STAGING_TEST_RUNNING.value, context={})})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "session.failed"})()

    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.STAGING_TEST_RUNNING, ctx={}, event=Event.SESSION_FAILED,
    )

    # engine 没向 ctx 写 escalated_reason
    escalate_call_ctx = next(c for n, c in calls if n == "escalate")["ctx"]
    assert "escalated_reason" not in escalate_call_ctx
    # DB 也没被 engine 改（escalate.py 真 escalate 时才会写 final_reason，这里 stub 没写）
    assert "escalated_reason" not in pool.rows["REQ-1"].context


@pytest.mark.asyncio
async def test_engine_does_not_overwrite_action_error_reason(stub_actions):
    """engine._emit_escalate 注 'action-error:...' 后，再次 step（event=SESSION_FAILED）走
    escalate transition 时 engine 不可覆盖原 reason —— action 异常的具体信息比 Event slug
    更有诊断价值。"""
    calls, reg = stub_actions

    async def escalate_stub(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id, "ctx": dict(ctx)}))
        return {"escalated": True}

    reg["escalate"] = escalate_stub

    initial_ctx = {
        "escalated_reason": "action-error:RuntimeError: pod not ready in 120s after 3 attempts",
    }
    pool = FakePool({
        "REQ-1": FakeReq(state=ReqState.ANALYZING.value, context=dict(initial_ctx)),
    })
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "issue.updated"})()

    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.ANALYZING, ctx=dict(initial_ctx),
        event=Event.SESSION_FAILED,
    )

    escalate_call_ctx = next(c for n, c in calls if n == "escalate")["ctx"]
    assert escalate_call_ctx["escalated_reason"] == initial_ctx["escalated_reason"]
    assert pool.rows["REQ-1"].context["escalated_reason"] == initial_ctx["escalated_reason"]


@pytest.mark.asyncio
async def test_recursion_depth_guard(stub_actions, monkeypatch):
    """防 emit 死循环（depth > 12 返回 error）。"""
    calls, reg = stub_actions

    # 自指环 emit（每个 action 都 emit 同一事件导致无限递归）
    async def loopy(*, body, req_id, tags, ctx):
        calls.append(("loopy", {}))
        return {"emit": Event.SPEC_LINT_PASS.value}

    reg["create_spec_lint"] = loopy
    reg["start_challenger"] = loopy
    # 让 CHALLENGER_RUNNING + SPEC_LINT_PASS 也能走 start_challenger（模拟死循环）
    from orchestrator import state as state_mod
    monkeypatch.setitem(
        state_mod.TRANSITIONS,
        (ReqState.CHALLENGER_RUNNING, Event.SPEC_LINT_PASS),
        state_mod.Transition(ReqState.CHALLENGER_RUNNING, "start_challenger"),
    )

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.SPEC_LINT_RUNNING.value)})
    body = type("B", (), {"issueId": "x", "projectId": "p", "event": "check.passed"})()
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=ReqState.SPEC_LINT_RUNNING, ctx={}, event=Event.SPEC_LINT_PASS,
    )
    # depth 限制 12 次，应该在 ~13 步内被击中
    assert len(calls) >= 13
    # 最终应触发 depth guard 返回 error（error 在嵌套 chained 中）
    current = result
    found_error = False
    for _ in range(15):  # safety limit on chain traversal
        if current.get("action") == "error" and "recursion" in current.get("reason", ""):
            found_error = True
            break
        current = current.get("chained")
        if not current:
            break
    assert found_error, "Expected recursion guard error in chained results"
