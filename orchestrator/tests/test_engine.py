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
    """模拟 asyncpg.Pool 的 fetchrow / execute，支持 req_state CAS + ctx patch。

    `stage_runs_calls` 记录 (op, sql, args)，op ∈ {"insert", "close", "update"}，
    用于断言 `engine._record_stage_transitions` 的副作用。
    """

    def __init__(self, initial: dict[str, FakeReq]):
        self.rows = initial
        self.stage_runs_calls: list[tuple[str, str, tuple]] = []
        self._next_stage_run_id = 1

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
        if sql_stripped.startswith("INSERT INTO stage_runs"):
            self.stage_runs_calls.append(("insert", sql_stripped, args))
            row_id = self._next_stage_run_id
            self._next_stage_run_id += 1
            return {"id": row_id}
        if sql_stripped.startswith("UPDATE stage_runs"):
            # close_latest_stage_run uses subquery with RETURNING id; pretend a row matched.
            self.stage_runs_calls.append(("close", sql_stripped, args))
            row_id = self._next_stage_run_id
            self._next_stage_run_id += 1
            return {"id": row_id}
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
        if sql_stripped.startswith("UPDATE stage_runs"):
            # update_stage_run path (no RETURNING). Recorded for completeness.
            self.stage_runs_calls.append(("update", sql_stripped, args))
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


# ═══════════════════════════════════════════════════════════════════════
# REQ-verifier-stagerun-close: VERIFY_PASS self-loop must close verifier stage_run
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_verify_pass_closes_orphan_verifier_stage_run(stub_actions):
    """(REVIEW_RUNNING, VERIFY_PASS) 是 transition 表的 self-loop，但
    apply_verify_pass 内部手 CAS，绕过 _record_stage_transitions。fix 后 engine 必须
    显式 close verifier 那条 stage_run（outcome='pass'），否则 ended_at 永远 NULL。
    """
    calls, reg = stub_actions

    async def apply_verify_pass(*, body, req_id, tags, ctx):
        calls.append(("apply_verify_pass", {"req_id": req_id}))
        return {"ok": True}

    reg["apply_verify_pass"] = apply_verify_pass

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.REVIEW_RUNNING.value)})
    body = type("B", (), {"issueId": "v-1", "projectId": "p", "event": "session.completed"})()
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1", "verify:spec_lint"],
        cur_state=ReqState.REVIEW_RUNNING,
        ctx={"verifier_stage": "spec_lint"}, event=Event.VERIFY_PASS,
    )

    # transition 表声明的 self-loop：state 不变
    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value

    # close_latest_stage_run 必须被调一次：req_id, stage='verifier', outcome='pass', fail_reason=None
    closes = [c for c in pool.stage_runs_calls if c[0] == "close"]
    assert len(closes) == 1, f"expected exactly 1 close, got {pool.stage_runs_calls!r}"
    _, _, args = closes[0]
    assert args[0] == "REQ-1"
    assert args[1] == "verifier"
    assert args[2] == "pass"
    assert args[3] is None

    # 不应该 open 任何新 stage_run（self-loop 不进入新 *_RUNNING）
    inserts = [c for c in pool.stage_runs_calls if c[0] == "insert"]
    assert inserts == []


@pytest.mark.asyncio
async def test_verify_fix_needed_still_closes_verifier_via_normal_path(stub_actions):
    """REGRESSION: VERIFY_FIX_NEEDED → REVIEW_RUNNING → FIXER_RUNNING 是不同 state，应仍走
    通用 close-on-leave + open-on-enter 路径：verifier outcome='fix' + 新开 fixer。
    """
    calls, reg = stub_actions

    async def start_fixer(*, body, req_id, tags, ctx):
        calls.append(("start_fixer", {"req_id": req_id}))
        return {"ok": True}

    reg["start_fixer"] = start_fixer

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.REVIEW_RUNNING.value)})
    body = type("B", (), {"issueId": "v-1", "projectId": "p", "event": "session.completed"})()
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1", "verify:dev_cross_check"],
        cur_state=ReqState.REVIEW_RUNNING,
        ctx={"verifier_stage": "dev_cross_check"}, event=Event.VERIFY_FIX_NEEDED,
    )

    assert pool.rows["REQ-1"].state == ReqState.FIXER_RUNNING.value

    closes = [c for c in pool.stage_runs_calls if c[0] == "close"]
    inserts = [c for c in pool.stage_runs_calls if c[0] == "insert"]
    # 通用 close: verifier outcome='fix'（不是 'pass'）
    assert len(closes) == 1, pool.stage_runs_calls
    args = closes[0][2]
    assert args[1] == "verifier"
    assert args[2] == "fix"
    # 通用 open: fixer
    assert len(inserts) == 1
    insert_args = inserts[0][2]
    assert insert_args[1] == "fixer"


@pytest.mark.asyncio
async def test_review_running_self_loop_other_event_does_not_close_verifier(stub_actions):
    """fix 必须只在 event == VERIFY_PASS 触发；其他 REVIEW_RUNNING self-loop 事件
    （如 SESSION_FAILED 经状态机 self-loop 给 escalate action 自决）不动 verifier stage_run。
    """
    calls, reg = stub_actions

    async def escalate(*, body, req_id, tags, ctx):
        calls.append(("escalate", {"req_id": req_id}))
        return {"ok": True}

    reg["escalate"] = escalate

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.REVIEW_RUNNING.value)})
    body = type("B", (), {"issueId": "v-1", "projectId": "p", "event": "session.failed"})()
    await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1"],
        cur_state=ReqState.REVIEW_RUNNING, ctx={}, event=Event.SESSION_FAILED,
    )

    # 没 close 任何 stage_run（escalate action 自己决定真 escalate 后由后续 transition close）
    closes = [c for c in pool.stage_runs_calls if c[0] == "close"]
    assert closes == [], f"unexpected close on SESSION_FAILED self-loop: {closes!r}"
