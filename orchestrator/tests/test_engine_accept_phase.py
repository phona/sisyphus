"""Mock tests for the 7 accept-phase transitions (REQ-test-accept-phase-1777267654).

Goal: cover every transition whose source state is ACCEPT_RUNNING or
ACCEPT_TEARING_DOWN — accept 阶段是 happy-path 链路从 PR-CI 绿到 ARCHIVING 的最后
一公里，之前一条单测都没有。所有 case 用 in-process FakePool + stub action，
**不打 BKD / Postgres / K8s**。

APT-S1..APT-S7 一一对应 spec scenario，见
`openspec/changes/REQ-test-accept-phase-1777267654/specs/accept-phase-tests/spec.md`。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# 复用 test_engine.py 里的 FakePool / FakeReq / _drain_tasks —— 它们就是 engine
# 状态机层 mock 测的标准 stub，跟 test_engine_adversarial.py 同一接入方式。
from test_engine import FakePool, FakeReq, _drain_tasks  # type: ignore[import-not-found]

from orchestrator import engine, k8s_runner
from orchestrator.actions import ACTION_META, REGISTRY
from orchestrator.state import Event, ReqState


@pytest.fixture
def stub_actions():
    """clear REGISTRY / ACTION_META，测后还原。"""
    saved_reg = dict(REGISTRY)
    saved_meta = dict(ACTION_META)
    REGISTRY.clear()
    ACTION_META.clear()
    yield REGISTRY
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(saved_reg)
    ACTION_META.update(saved_meta)


@pytest.fixture
def mock_runner_controller():
    """注入 fake k8s_runner controller，断言 cleanup_runner 是否被调。"""
    fake = MagicMock()
    fake.cleanup_runner = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


def _body(**attrs):
    """构造一个 minimal body 对象（webhook payload stub）。"""
    return type("B", (), attrs)()


# ───────────────────────────────────────────────────────────────────────
# APT-S1：(ACCEPT_RUNNING, ACCEPT_PASS) + emit teardown-done.pass → ARCHIVING
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apt_s1_accept_pass_advances_through_teardown_to_archiving(
    stub_actions, mock_runner_controller,
):
    """Spec APT-S1: accept.pass → teardown_accept_env → 链式 teardown-done.pass → done_archive。"""
    teardown_calls = {"n": 0}
    archive_calls = {"n": 0}

    async def teardown_accept_env(*, body, req_id, tags, ctx):
        teardown_calls["n"] += 1
        return {"emit": Event.TEARDOWN_DONE_PASS.value, "accept_result": "pass"}

    async def done_archive(*, body, req_id, tags, ctx):
        archive_calls["n"] += 1
        return {"ok": True}

    stub_actions["teardown_accept_env"] = teardown_accept_env
    stub_actions["done_archive"] = done_archive

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ACCEPT_RUNNING.value)})
    body = _body(
        issueId="accept-1", projectId="p", event="session.completed",
    )
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["accept", "REQ-1", "result:pass"],
        cur_state=ReqState.ACCEPT_RUNNING,
        ctx={}, event=Event.ACCEPT_PASS,
    )
    await _drain_tasks()

    assert result["action"] == "teardown_accept_env"
    assert result["next_state"] == ReqState.ACCEPT_TEARING_DOWN.value
    assert "chained" in result, "teardown emit teardown-done.pass 应触发链式 done_archive"
    assert result["chained"]["action"] == "done_archive"
    assert result["chained"]["next_state"] == ReqState.ARCHIVING.value
    assert pool.rows["REQ-1"].state == ReqState.ARCHIVING.value
    assert teardown_calls["n"] == 1
    assert archive_calls["n"] == 1
    # ACCEPT_TEARING_DOWN / ARCHIVING 都不是 terminal → engine 不应清 runner
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# APT-S2：(ACCEPT_RUNNING, ACCEPT_FAIL) + emit teardown-done.fail → REVIEW_RUNNING
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apt_s2_accept_fail_advances_through_teardown_to_verifier(
    stub_actions, mock_runner_controller,
):
    """Spec APT-S2: accept.fail → teardown_accept_env → 链式 teardown-done.fail → invoke_verifier_for_accept_fail。"""
    teardown_calls = {"n": 0}
    verifier_calls = {"n": 0}

    async def teardown_accept_env(*, body, req_id, tags, ctx):
        teardown_calls["n"] += 1
        return {"emit": Event.TEARDOWN_DONE_FAIL.value, "accept_result": "fail"}

    async def invoke_verifier_for_accept_fail(*, body, req_id, tags, ctx):
        verifier_calls["n"] += 1
        return {"ok": True}

    stub_actions["teardown_accept_env"] = teardown_accept_env
    stub_actions["invoke_verifier_for_accept_fail"] = invoke_verifier_for_accept_fail

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ACCEPT_RUNNING.value)})
    body = _body(
        issueId="accept-1", projectId="p", event="session.completed",
    )
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["accept", "REQ-1", "result:fail"],
        cur_state=ReqState.ACCEPT_RUNNING,
        ctx={}, event=Event.ACCEPT_FAIL,
    )
    await _drain_tasks()

    assert result["action"] == "teardown_accept_env"
    assert result["next_state"] == ReqState.ACCEPT_TEARING_DOWN.value
    assert "chained" in result
    assert result["chained"]["action"] == "invoke_verifier_for_accept_fail"
    assert result["chained"]["next_state"] == ReqState.REVIEW_RUNNING.value
    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value
    assert teardown_calls["n"] == 1
    assert verifier_calls["n"] == 1
    # REVIEW_RUNNING 非 terminal
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# APT-S3：(ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL) → ESCALATED + cleanup_runner(retain_pvc=True)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apt_s3_accept_env_up_fail_escalates_and_cleans_up(
    stub_actions, mock_runner_controller,
):
    """Spec APT-S3: lab 起不来 → ESCALATED 终态；engine 必须调一次 cleanup_runner(retain_pvc=True)。"""
    escalate_calls = {"n": 0}

    async def escalate(*, body, req_id, tags, ctx):
        escalate_calls["n"] += 1
        return {"escalated": True}

    stub_actions["escalate"] = escalate

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ACCEPT_RUNNING.value)})
    body = _body(
        issueId="accept-1", projectId="p", event="accept-env-up.fail",
    )
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["accept", "REQ-1"],
        cur_state=ReqState.ACCEPT_RUNNING,
        ctx={}, event=Event.ACCEPT_ENV_UP_FAIL,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    assert escalate_calls["n"] == 1
    # ESCALATED 是 terminal，且 cur (ACCEPT_RUNNING) 非 terminal → engine 必须清 runner
    # 且 retain_pvc=True（escalate 路径保 PVC 给人工 debug）
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )


# ───────────────────────────────────────────────────────────────────────
# APT-S4：(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) → ARCHIVING
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apt_s4_teardown_done_pass_advances_to_archiving(
    stub_actions, mock_runner_controller,
):
    """Spec APT-S4: teardown-done.pass 直接入口（不经 teardown_accept_env emit）→ ARCHIVING。"""
    archive_calls = {"n": 0}

    async def done_archive(*, body, req_id, tags, ctx):
        archive_calls["n"] += 1
        return {"ok": True}

    stub_actions["done_archive"] = done_archive

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ACCEPT_TEARING_DOWN.value)})
    body = _body(
        issueId="accept-1", projectId="p", event="teardown-done.pass",
    )
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["accept", "REQ-1"],
        cur_state=ReqState.ACCEPT_TEARING_DOWN,
        ctx={}, event=Event.TEARDOWN_DONE_PASS,
    )
    await _drain_tasks()

    assert result["action"] == "done_archive"
    assert result["next_state"] == ReqState.ARCHIVING.value
    assert pool.rows["REQ-1"].state == ReqState.ARCHIVING.value
    assert archive_calls["n"] == 1
    # ARCHIVING 不是 terminal（DONE 才是）→ 不应清 runner
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# APT-S5：(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) → REVIEW_RUNNING
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apt_s5_teardown_done_fail_routes_to_verifier(
    stub_actions, mock_runner_controller,
):
    """Spec APT-S5: teardown-done.fail 直接入口 → REVIEW_RUNNING（接 verifier）。"""
    verifier_calls = {"n": 0}

    async def invoke_verifier_for_accept_fail(*, body, req_id, tags, ctx):
        verifier_calls["n"] += 1
        return {"ok": True}

    stub_actions["invoke_verifier_for_accept_fail"] = invoke_verifier_for_accept_fail

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ACCEPT_TEARING_DOWN.value)})
    body = _body(
        issueId="accept-1", projectId="p", event="teardown-done.fail",
    )
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["accept", "REQ-1"],
        cur_state=ReqState.ACCEPT_TEARING_DOWN,
        ctx={}, event=Event.TEARDOWN_DONE_FAIL,
    )
    await _drain_tasks()

    assert result["action"] == "invoke_verifier_for_accept_fail"
    assert result["next_state"] == ReqState.REVIEW_RUNNING.value
    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value
    assert verifier_calls["n"] == 1
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# APT-S6：(ACCEPT_RUNNING, SESSION_FAILED) self-loop
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apt_s6_accept_running_session_failed_self_loop(
    stub_actions, mock_runner_controller,
):
    """Spec APT-S6: BKD session crash → 转移表 self-loop；escalate action 自决是否真 escalate。"""
    escalate_calls = {"n": 0}

    async def escalate(*, body, req_id, tags, ctx):
        escalate_calls["n"] += 1
        # 真生产 escalate 可能 follow-up "continue"（auto-resume）或手 CAS 推 ESCALATED；
        # mock 里只验 transition 表声明的 self-loop 行为，escalate stub 不动 state。
        return {"ok": True}

    stub_actions["escalate"] = escalate

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ACCEPT_RUNNING.value)})
    body = _body(
        issueId="accept-1", projectId="p", event="session.failed",
    )
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["accept", "REQ-1"],
        cur_state=ReqState.ACCEPT_RUNNING,
        ctx={}, event=Event.SESSION_FAILED,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ACCEPT_RUNNING.value
    # transition 声明的 self-loop：state 保持
    assert pool.rows["REQ-1"].state == ReqState.ACCEPT_RUNNING.value
    assert escalate_calls["n"] == 1
    # cur 非 terminal + next 非 terminal → 不清 runner（auto-resume 路径不能误删 pod）
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# APT-S7：(ACCEPT_TEARING_DOWN, SESSION_FAILED) self-loop
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apt_s7_accept_tearing_down_session_failed_self_loop(
    stub_actions, mock_runner_controller,
):
    """Spec APT-S7: env-down 中 session crash → self-loop；escalate action 自决。"""
    escalate_calls = {"n": 0}

    async def escalate(*, body, req_id, tags, ctx):
        escalate_calls["n"] += 1
        return {"ok": True}

    stub_actions["escalate"] = escalate

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ACCEPT_TEARING_DOWN.value)})
    body = _body(
        issueId="accept-1", projectId="p", event="session.failed",
    )
    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["accept", "REQ-1"],
        cur_state=ReqState.ACCEPT_TEARING_DOWN,
        ctx={}, event=Event.SESSION_FAILED,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ACCEPT_TEARING_DOWN.value
    assert pool.rows["REQ-1"].state == ReqState.ACCEPT_TEARING_DOWN.value
    assert escalate_calls["n"] == 1
    mock_runner_controller.cleanup_runner.assert_not_awaited()
