"""Mock tests for ESCALATED resume + remaining engine.step transition gaps
(REQ-test-coverage-escalated-resume-1777281969).

This file closes the engine.step mock-test coverage gap to 47/47 of
`state.TRANSITIONS`:

- ERT-S1..ERT-S6: the 6 transitions that no other engine.step mock test
  covered (intake-phase routing + pr_ci timeout + verify_escalate from
  intake/analyze).
- ERT-S7..ERT-S8: deeper coverage of the **ESCALATED resume** paths
  beyond the dispatch-level smoke checks in test_engine_verifier_loop
  VLT-S12/VLT-S13 — proving the chain emit re-enters main chain after
  apply_verify_pass internally CAS-advances and that start_fixer
  receives the verify:<stage> tag from the verifier issue.
- ERT-S9: defense-in-depth parametrized sweep over the entire
  `state.TRANSITIONS` table — every (state, event) round-trips through
  engine.step.

Stubs replace every action via REGISTRY shim; the engine's own
`_record_stage_transitions` and terminal-cleanup branches still run
because they only depend on FakePool / k8s_runner.set_controller.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# Reuse FakePool / FakeReq / _drain_tasks from test_engine.py — same pattern as
# test_engine_main_chain.py / test_engine_accept_phase.py / test_engine_verifier_loop.py.
from test_engine import FakePool, FakeReq, _drain_tasks  # type: ignore[import-not-found]

from orchestrator import engine, k8s_runner
from orchestrator import state as state_mod
from orchestrator.actions import ACTION_META, REGISTRY
from orchestrator.state import Event, ReqState

# ─── 共享 fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def stub_actions():
    """Save+clear REGISTRY / ACTION_META; restore on teardown."""
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
    """Inject fake k8s controller; assert cleanup_runner calls."""
    fake = MagicMock()
    fake.cleanup_runner = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


def _body(**attrs):
    return type("B", (), attrs)()


def _make_recorder(name: str, calls: list):
    async def _rec(*, body, req_id, tags, ctx):
        calls.append({"action": name, "tags": list(tags or []), "ctx": dict(ctx or {})})
        return {"ok": True}
    return _rec


# ───────────────────────────────────────────────────────────────────────
# ERT-S1: (INIT, INTENT_INTAKE) → INTAKING + start_intake
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ert_s1_init_intent_intake_enters_intaking(stub_actions):
    """Spec ERT-S1: 物理隔离的 intake 入口（intent:intake tag → INTAKING）。"""
    calls: list = []
    stub_actions["start_intake"] = _make_recorder("start_intake", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INIT.value)})
    body = _body(issueId="src-1", projectId="p", event="intent.intake")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["intent:intake", "REQ-1"],
        cur_state=ReqState.INIT, ctx={}, event=Event.INTENT_INTAKE,
    )

    assert result["action"] == "start_intake"
    assert result["next_state"] == ReqState.INTAKING.value
    assert pool.rows["REQ-1"].state == ReqState.INTAKING.value
    assert len(calls) == 1


# ───────────────────────────────────────────────────────────────────────
# ERT-S2: (INTAKING, INTAKE_PASS) → ANALYZING + start_analyze_with_finalized_intent
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ert_s2_intaking_intake_pass_enters_analyzing(stub_actions):
    """Spec ERT-S2: intake 完成 + finalized intent → 接力到 analyze。"""
    calls: list = []
    stub_actions["start_analyze_with_finalized_intent"] = _make_recorder(
        "start_analyze_with_finalized_intent", calls,
    )

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INTAKING.value)})
    body = _body(issueId="intake-1", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["intake", "REQ-1"],
        cur_state=ReqState.INTAKING, ctx={}, event=Event.INTAKE_PASS,
    )

    assert result["action"] == "start_analyze_with_finalized_intent"
    assert result["next_state"] == ReqState.ANALYZING.value
    assert pool.rows["REQ-1"].state == ReqState.ANALYZING.value
    assert len(calls) == 1


# ───────────────────────────────────────────────────────────────────────
# ERT-S3: (INTAKING, INTAKE_FAIL) → ESCALATED + escalate (with cleanup)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ert_s3_intaking_intake_fail_escalates_with_cleanup(
    stub_actions, mock_runner_controller,
):
    """Spec ERT-S3: intake 异常 / 用户放弃 → ESCALATED + cleanup_runner(retain_pvc=True)。"""
    calls: list = []
    stub_actions["escalate"] = _make_recorder("escalate", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INTAKING.value)})
    body = _body(issueId="intake-1", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["intake", "REQ-1"],
        cur_state=ReqState.INTAKING, ctx={}, event=Event.INTAKE_FAIL,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    assert len(calls) == 1
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )


# ───────────────────────────────────────────────────────────────────────
# ERT-S4: (INTAKING, VERIFY_ESCALATE) → ESCALATED + escalate (with cleanup)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ert_s4_intaking_verify_escalate_escalates_with_cleanup(
    stub_actions, mock_runner_controller,
):
    """Spec ERT-S4: start_analyze_with_finalized_intent 内部判 escalate（intent 缺字段
    / clone failed 等）→ ESCALATED + cleanup。"""
    calls: list = []
    stub_actions["escalate"] = _make_recorder("escalate", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INTAKING.value)})
    body = _body(issueId="intake-1", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["intake", "REQ-1"],
        cur_state=ReqState.INTAKING, ctx={}, event=Event.VERIFY_ESCALATE,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    assert len(calls) == 1
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )


# ───────────────────────────────────────────────────────────────────────
# ERT-S5: (ANALYZING, VERIFY_ESCALATE) → ESCALATED + escalate (with cleanup)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ert_s5_analyzing_verify_escalate_escalates_with_cleanup(
    stub_actions, mock_runner_controller,
):
    """Spec ERT-S5: start_analyze 内部判 escalate（clone_involved_repos 失败等）→
    ESCALATED + cleanup。实证背景：REQ-ttpos-pat-validate (2026-04-26) 漏挂这条
    transition 时 REQ 卡 ANALYZING 60min，靠 watchdog auto_resume 才推进。"""
    calls: list = []
    stub_actions["escalate"] = _make_recorder("escalate", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ANALYZING.value)})
    body = _body(issueId="analyze-1", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["analyze", "REQ-1"],
        cur_state=ReqState.ANALYZING, ctx={}, event=Event.VERIFY_ESCALATE,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    assert len(calls) == 1
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )


# ───────────────────────────────────────────────────────────────────────
# ERT-S6: (PR_CI_RUNNING, PR_CI_TIMEOUT) → ESCALATED + escalate (with cleanup)
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ert_s6_pr_ci_timeout_escalates_with_cleanup(
    stub_actions, mock_runner_controller,
):
    """Spec ERT-S6: pr_ci_watch 轮 GitHub 长时间没 check-run → ESCALATED + cleanup。
    可能 repo 没配 GHA 模板；sisyphus 不机制性兜 retry，escalate 给人。"""
    calls: list = []
    stub_actions["escalate"] = _make_recorder("escalate", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.PR_CI_RUNNING.value)})
    body = _body(issueId="prci-1", projectId="p", event="pr-ci.timeout")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["pr-ci", "REQ-1"],
        cur_state=ReqState.PR_CI_RUNNING, ctx={}, event=Event.PR_CI_TIMEOUT,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    assert len(calls) == 1
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )


# ───────────────────────────────────────────────────────────────────────
# ERT-S7: ESCALATED + VERIFY_PASS — end-to-end resume to next stage via chain emit
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ert_s7_escalated_verify_pass_chains_to_next_stage(
    stub_actions, mock_runner_controller,
):
    """Spec ERT-S7: 人工 resume from ESCALATED 端到端。
    apply_verify_pass stub 模拟生产 action 行为：内部 CAS ESCALATED→STAGING_TEST_RUNNING
    + emit STAGING_TEST_PASS。engine 必须 chain-dispatch create_pr_ci_watch，最终
    pool row state = PR_CI_RUNNING。

    这是 sisyphus 唯一让人手把"卡死"REQ 续起来的路径，dispatch-level 测（VLT-S12）
    没法证明 chain emit 真把 REQ 推过 ESCALATED 边界 —— 必须有专门的端到端断言。
    """
    apply_calls: list = []
    pr_ci_calls: list = []

    async def apply_verify_pass(*, body, req_id, tags, ctx):
        # 模拟生产 apply_verify_pass：手 CAS ESCALATED → 下游 stage_running。
        # FakePool.fetchrow 在 UPDATE req_state 分支按 expected==actual 做 CAS。
        from orchestrator.store import req_state as rs
        ok = await rs.cas_transition(
            pool, req_id, ReqState.ESCALATED, ReqState.STAGING_TEST_RUNNING,
            Event.VERIFY_PASS, "apply_verify_pass",
        )
        assert ok, "stub apply_verify_pass: ESCALATED→STAGING_TEST_RUNNING CAS must succeed"
        apply_calls.append({"tags": list(tags or []), "ctx": dict(ctx or {})})
        return {"emit": Event.STAGING_TEST_PASS.value}

    async def create_pr_ci_watch(*, body, req_id, tags, ctx):
        pr_ci_calls.append({"tags": list(tags or [])})
        return {"ok": True}

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ESCALATED.value)})
    stub_actions["apply_verify_pass"] = apply_verify_pass
    stub_actions["create_pr_ci_watch"] = create_pr_ci_watch

    body = _body(issueId="vfy-resume", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1", "verify:staging_test"],
        cur_state=ReqState.ESCALATED,
        ctx={"verifier_stage": "staging_test"},
        event=Event.VERIFY_PASS,
    )
    await _drain_tasks()

    # 顶层 step：apply_verify_pass dispatched, transition 表声明 self-loop
    assert result["action"] == "apply_verify_pass"
    assert result["next_state"] == ReqState.ESCALATED.value

    # chain：staging-test.pass → create_pr_ci_watch → PR_CI_RUNNING
    assert "chained" in result, f"chain emit lost: {result!r}"
    assert result["chained"]["action"] == "create_pr_ci_watch"
    assert result["chained"]["next_state"] == ReqState.PR_CI_RUNNING.value

    # 端到端：行真被推到 PR_CI_RUNNING
    assert pool.rows["REQ-1"].state == ReqState.PR_CI_RUNNING.value
    assert len(apply_calls) == 1
    assert len(pr_ci_calls) == 1

    # cur 已是 terminal (ESCALATED) → engine 跳过 cleanup（防误删 resume 路径拉的 pod）
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ───────────────────────────────────────────────────────────────────────
# ERT-S8: ESCALATED + VERIFY_FIX_NEEDED — verify:<stage> tag forwarded to start_fixer
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ert_s8_escalated_verify_fix_needed_forwards_stage_tag(stub_actions):
    """Spec ERT-S8: start_fixer 必须能从触发本次 transition 的 verifier issue tag 读
    `verify:<stage>` —— 多 verifier 并发时 ctx.verifier_stage 会被后来者覆盖，issue
    tag 是无歧义真相。本 case 同时穿 ctx.verifier_stage 和 verify:staging_test tag，
    断言两者都能从 engine 传给 handler。"""
    calls: list = []

    async def start_fixer(*, body, req_id, tags, ctx):
        calls.append({"tags": list(tags or []), "ctx": dict(ctx or {})})
        return {"ok": True}

    stub_actions["start_fixer"] = start_fixer

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.ESCALATED.value)})
    body = _body(issueId="vfy-resume", projectId="p", event="session.completed")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["verifier", "REQ-1", "verify:staging_test"],
        cur_state=ReqState.ESCALATED,
        ctx={"verifier_stage": "staging_test", "verifier_fixer": "dev"},
        event=Event.VERIFY_FIX_NEEDED,
    )

    assert result["action"] == "start_fixer"
    assert result["next_state"] == ReqState.FIXER_RUNNING.value
    assert pool.rows["REQ-1"].state == ReqState.FIXER_RUNNING.value
    assert len(calls) == 1
    assert "verify:staging_test" in calls[0]["tags"], (
        f"engine must forward verify:<stage> tag, got {calls[0]['tags']!r}"
    )
    assert calls[0]["ctx"].get("verifier_stage") == "staging_test"
    assert calls[0]["ctx"].get("verifier_fixer") == "dev"


# ───────────────────────────────────────────────────────────────────────
# ERT-S9: 47/47 sweep — every TRANSITIONS entry round-trips through engine.step
# ───────────────────────────────────────────────────────────────────────


def _all_transitions_params():
    """Build pytest params for every (state, event) → Transition row."""
    return [
        pytest.param(
            st, ev, t.action, t.next_state.value,
            id=f"{st.value}+{ev.value}",
        )
        for (st, ev), t in sorted(
            state_mod.TRANSITIONS.items(),
            key=lambda kv: (kv[0][0].value, kv[0][1].value),
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cur_state,event,expected_action,expected_next_state",
    _all_transitions_params(),
)
async def test_ert_s9_full_transition_sweep(
    stub_actions, mock_runner_controller,
    cur_state, event, expected_action, expected_next_state,
):
    """Spec ERT-S9: 参数化遍历 state.TRANSITIONS 全 47 项。每项构造对应 stub action，
    engine.step 必须返 transition.action（或 no-op 若 None）+ row state advance 到
    transition.next_state.value。

    Defense-in-depth: 即便 ERT-S1..S8 / VLT / MCT / APT 漏了某条 case，sweep 也会
    fail。新增 transition 自动入参（pytest 生成对应 sub-case），无需手维护。"""
    calls: list = []

    if expected_action is not None:
        stub_actions[expected_action] = _make_recorder(expected_action, calls)

    pool = FakePool({"REQ-1": FakeReq(state=cur_state.value)})
    body = _body(issueId="sweep-x", projectId="p", event="sweep")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p", tags=[],
        cur_state=cur_state, ctx={}, event=event,
    )
    await _drain_tasks()

    if expected_action is None:
        assert result["action"] == "no-op", (
            f"{cur_state.value}+{event.value}: expected no-op got {result!r}"
        )
        assert calls == [], (
            f"{cur_state.value}+{event.value}: no action means no dispatch, got {calls!r}"
        )
    else:
        assert result["action"] == expected_action, (
            f"{cur_state.value}+{event.value}: expected action={expected_action} got {result!r}"
        )
        assert len(calls) == 1, (
            f"{cur_state.value}+{event.value}: stub call count {calls!r}"
        )
        assert calls[0]["action"] == expected_action

    assert result["next_state"] == expected_next_state, (
        f"{cur_state.value}+{event.value}: next_state {result!r}"
    )
    assert pool.rows["REQ-1"].state == expected_next_state, (
        f"{cur_state.value}+{event.value}: pool row state "
        f"{pool.rows['REQ-1'].state!r}"
    )


def test_ert_s9_sweep_covers_exactly_53():
    """Sanity: TRANSITIONS 必须正好 50 条 —— 加 / 减 transition 时这条 fail 提醒
    review 是否同步加 spec scenario / 文档（state-machine.md / dump_transitions）。

    REQ-bkd-acceptance-feedback-loop-1777278984 起新增 PENDING_USER_REVIEW 入/出
    transitions（pr.opened 入站 + acceptance approve/request_changes 出站），从 47
    增至 49；后续 REQ 再增 1 条至 50。"""
    assert len(state_mod.TRANSITIONS) == 53, (
        f"expected 53 transitions, got {len(state_mod.TRANSITIONS)}; "
        "if you intentionally added/removed a transition, update this assertion "
        "AND add granular ERT/MCT/APT/VLT scenario coverage for it."
    )
