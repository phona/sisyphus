"""Main-chain happy-path mock tests for engine.step (REQ-test-main-chain-1777267689).

11 个 mock 用例（MCT-S1..MCT-S11）一对一覆盖主链 11 条 happy-path transition。
+1 条 end-to-end chain 用例（MCT-CHAIN）验 emit 链能从 INIT 一路推到 DONE。

复用 ``test_engine.py`` 已有的 FakePool / FakeReq —— 跟 test_engine_adversarial.py
同模式（``from test_engine import ...``），不抄一份避免漂移。

每条 case 4 个断言：
  1. ``engine.step`` 返回 ``action="<expected_action>"``
  2. ``next_state="<expected_next_state>"``
  3. ``pool.rows[req_id].state`` 真被 CAS 推到目标
  4. stub action 被精确调用一次

不打 BKD / Postgres / K8s。
"""
from __future__ import annotations

import pytest

# 跟 test_engine_adversarial.py 同模式：直接 import test_engine 的私有 FakePool/FakeReq
from test_engine import FakePool, FakeReq  # type: ignore[import-not-found]

from orchestrator import engine
from orchestrator.actions import ACTION_META, REGISTRY
from orchestrator.state import Event, ReqState


# ─── 局部 stub_actions fixture（与 test_engine_adversarial.py 同设计） ──────
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


def _body(**attrs):
    return type("B", (), attrs)()


async def _run_single_transition(
    *,
    stub_actions: dict,
    cur_state: ReqState,
    event: Event,
    expected_next_state: ReqState,
    expected_action: str | None,
):
    """通用单 transition 验：配 stub action（如 expected_action 非 None）+ engine.step + 4 断言。

    返回 (result_dict, calls_list) 给调用方做额外断言（一般用不上）。
    """
    calls: list[str] = []

    if expected_action is not None:
        async def stub(*, body, req_id, tags, ctx):
            calls.append(expected_action)
            return {"ok": True}

        stub_actions[expected_action] = stub

    pool = FakePool({"REQ-1": FakeReq(state=cur_state.value)})
    result = await engine.step(
        pool,
        body=_body(issueId="x", projectId="p", event="check.passed"),
        req_id="REQ-1",
        project_id="p",
        tags=["main-chain", "REQ-1"],
        cur_state=cur_state,
        ctx={},
        event=event,
    )

    if expected_action is None:
        # ARCHIVE_DONE 这条 transition.action 是 None，engine 返 action="no-op"
        assert result["action"] == "no-op", (
            f"{cur_state.value}+{event.value}: expected action=no-op, got {result!r}"
        )
        assert calls == [], "no-op transition shouldn't dispatch any handler"
    else:
        assert result["action"] == expected_action, (
            f"{cur_state.value}+{event.value}: expected action={expected_action}, got {result!r}"
        )
        assert calls == [expected_action], (
            f"{cur_state.value}+{event.value}: stub call mismatch, got {calls!r}"
        )

    assert result["next_state"] == expected_next_state.value, (
        f"{cur_state.value}+{event.value}: next_state mismatch ({result!r})"
    )
    assert pool.rows["REQ-1"].state == expected_next_state.value, (
        f"{cur_state.value}+{event.value}: row state not advanced ({pool.rows['REQ-1'].state!r})"
    )
    return result, calls


# ───────────────────────────────────────────────────────────────────────
# MCT-S1：(INIT, INTENT_ANALYZE) → ANALYZING + start_analyze
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s1_init_intent_analyze_to_analyzing(stub_actions):
    """Spec MCT-S1: 主链入口，REQ 离开 INIT 必经此路。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.INIT,
        event=Event.INTENT_ANALYZE,
        expected_next_state=ReqState.ANALYZING,
        expected_action="start_analyze",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S2：(ANALYZING, ANALYZE_DONE) → ANALYZE_ARTIFACT_CHECKING + create_analyze_artifact_check
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s2_analyzing_done_to_artifact_checking(stub_actions):
    """Spec MCT-S2: REQ-analyze-artifact-check 引入的产物结构性检查关口。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.ANALYZING,
        event=Event.ANALYZE_DONE,
        expected_next_state=ReqState.ANALYZE_ARTIFACT_CHECKING,
        expected_action="create_analyze_artifact_check",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S3：(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_PASS)
#         → SPEC_LINT_RUNNING + create_spec_lint
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s3_artifact_check_pass_to_spec_lint(stub_actions):
    """Spec MCT-S3: 产物齐 → 进入机械 checker 阶段。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.ANALYZE_ARTIFACT_CHECKING,
        event=Event.ANALYZE_ARTIFACT_CHECK_PASS,
        expected_next_state=ReqState.SPEC_LINT_RUNNING,
        expected_action="create_spec_lint",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S4：(SPEC_LINT_RUNNING, SPEC_LINT_PASS) → CHALLENGER_RUNNING + start_challenger
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s4_spec_lint_pass_to_challenger(stub_actions):
    """Spec MCT-S4: M18 challenger 写 contract test（在 spec_lint 与 dev_cross_check 之间）。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.SPEC_LINT_RUNNING,
        event=Event.SPEC_LINT_PASS,
        expected_next_state=ReqState.CHALLENGER_RUNNING,
        expected_action="start_challenger",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S5：(CHALLENGER_RUNNING, CHALLENGER_PASS)
#         → DEV_CROSS_CHECK_RUNNING + create_dev_cross_check
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s5_challenger_pass_to_dev_cross_check(stub_actions):
    """Spec MCT-S5: challenger 推完 contract test → 跑 ci-lint 交叉验证。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.CHALLENGER_RUNNING,
        event=Event.CHALLENGER_PASS,
        expected_next_state=ReqState.DEV_CROSS_CHECK_RUNNING,
        expected_action="create_dev_cross_check",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S6：(DEV_CROSS_CHECK_RUNNING, DEV_CROSS_CHECK_PASS)
#         → STAGING_TEST_RUNNING + create_staging_test
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s6_dev_cross_check_pass_to_staging_test(stub_actions):
    """Spec MCT-S6: ci-lint 通过 → 跑 ci-unit-test + ci-integration-test。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.DEV_CROSS_CHECK_RUNNING,
        event=Event.DEV_CROSS_CHECK_PASS,
        expected_next_state=ReqState.STAGING_TEST_RUNNING,
        expected_action="create_staging_test",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S7：(STAGING_TEST_RUNNING, STAGING_TEST_PASS)
#         → PR_CI_RUNNING + create_pr_ci_watch
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s7_staging_test_pass_to_pr_ci(stub_actions):
    """Spec MCT-S7: 内部 staging 绿 → 跨过 GitHub-side PR CI 网关。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.STAGING_TEST_RUNNING,
        event=Event.STAGING_TEST_PASS,
        expected_next_state=ReqState.PR_CI_RUNNING,
        expected_action="create_pr_ci_watch",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S8：(PR_CI_RUNNING, PR_CI_PASS) → ACCEPT_RUNNING + create_accept
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s8_pr_ci_pass_to_accept(stub_actions):
    """Spec MCT-S8: GHA 全套绿 → 进 lab + accept-agent 跑 FEATURE-A*。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.PR_CI_RUNNING,
        event=Event.PR_CI_PASS,
        expected_next_state=ReqState.ACCEPT_RUNNING,
        expected_action="create_accept",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S9：(ACCEPT_RUNNING, ACCEPT_PASS) → ACCEPT_TEARING_DOWN + teardown_accept_env
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s9_accept_pass_to_tearing_down(stub_actions):
    """Spec MCT-S9: accept 全过 → 强制 teardown lab（accept-env-down 非可选）。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.ACCEPT_RUNNING,
        event=Event.ACCEPT_PASS,
        expected_next_state=ReqState.ACCEPT_TEARING_DOWN,
        expected_action="teardown_accept_env",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S10：(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) → ARCHIVING + done_archive
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s10_teardown_done_pass_to_archiving(stub_actions):
    """Spec MCT-S10: lab 清完 + 上一步 accept.pass → done-archive 起。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.ACCEPT_TEARING_DOWN,
        event=Event.TEARDOWN_DONE_PASS,
        expected_next_state=ReqState.ARCHIVING,
        expected_action="done_archive",
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-S11：(ARCHIVING, ARCHIVE_DONE) → DONE + None (no-op terminal)
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_s11_archive_done_to_done(stub_actions):
    """Spec MCT-S11: 唯一 transition.action=None 的主链 transition；engine 应返 no-op。"""
    await _run_single_transition(
        stub_actions=stub_actions,
        cur_state=ReqState.ARCHIVING,
        event=Event.ARCHIVE_DONE,
        expected_next_state=ReqState.DONE,
        expected_action=None,
    )


# ───────────────────────────────────────────────────────────────────────
# MCT-CHAIN：从 INIT 一路 emit 链推到 DONE
# ───────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_mct_chain_full_main_chain_via_emit(stub_actions):
    """Spec MCT-CHAIN: 10 个 stub action 各 emit 下一事件，单次 engine.step 推完整条主链。

    主链：
      INIT --INTENT_ANALYZE--> ANALYZING --ANALYZE_DONE-->
      ANALYZE_ARTIFACT_CHECKING --ANALYZE_ARTIFACT_CHECK_PASS-->
      SPEC_LINT_RUNNING --SPEC_LINT_PASS-->
      CHALLENGER_RUNNING --CHALLENGER_PASS-->
      DEV_CROSS_CHECK_RUNNING --DEV_CROSS_CHECK_PASS-->
      STAGING_TEST_RUNNING --STAGING_TEST_PASS-->
      PR_CI_RUNNING --PR_CI_PASS-->
      ACCEPT_RUNNING --ACCEPT_PASS-->
      ACCEPT_TEARING_DOWN --TEARDOWN_DONE_PASS-->
      ARCHIVING --ARCHIVE_DONE--> DONE

    10 个 emit 链，加上 ARCHIVING→DONE 这条无 action 的 terminal 跳转 = 11 条
    transition 全过。
    """
    calls: list[str] = []

    # action_name → next event 串成完整主链
    action_to_next_event: dict[str, Event] = {
        "start_analyze":                 Event.ANALYZE_DONE,
        "create_analyze_artifact_check": Event.ANALYZE_ARTIFACT_CHECK_PASS,
        "create_spec_lint":              Event.SPEC_LINT_PASS,
        "start_challenger":              Event.CHALLENGER_PASS,
        "create_dev_cross_check":        Event.DEV_CROSS_CHECK_PASS,
        "create_staging_test":           Event.STAGING_TEST_PASS,
        "create_pr_ci_watch":            Event.PR_CI_PASS,
        "create_accept":                 Event.ACCEPT_PASS,
        "teardown_accept_env":           Event.TEARDOWN_DONE_PASS,
        "done_archive":                  Event.ARCHIVE_DONE,
    }

    def _make_stub(name: str, emit_event: Event):
        async def _stub(*, body, req_id, tags, ctx):
            calls.append(name)
            return {"emit": emit_event.value}
        return _stub

    for name, ev in action_to_next_event.items():
        stub_actions[name] = _make_stub(name, ev)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.INIT.value)})
    result = await engine.step(
        pool,
        body=_body(issueId="x", projectId="p", event="intent.analyze"),
        req_id="REQ-1",
        project_id="p",
        tags=["main-chain", "REQ-1"],
        cur_state=ReqState.INIT,
        ctx={},
        event=Event.INTENT_ANALYZE,
    )

    # 1) 全 10 个 stub action 各调一次（顺序按主链）
    assert calls == list(action_to_next_event.keys()), (
        f"action call sequence wrong: {calls!r}"
    )

    # 2) row 终态为 DONE
    assert pool.rows["REQ-1"].state == ReqState.DONE.value, (
        f"row not at DONE: {pool.rows['REQ-1'].state!r}"
    )

    # 3) 整条 chained 链里不应该出现 recursion guard error
    cur = result
    for _ in range(20):  # 超过 11 步说明跑飞了
        if cur.get("action") == "error" and "recursion" in str(cur.get("reason", "")):
            pytest.fail(f"recursion guard fired during main chain: {cur!r}")
        cur = cur.get("chained")
        if not cur:
            break

    # 4) 顶层 step 是 start_analyze（INIT 入口）
    assert result["action"] == "start_analyze"
    assert result["next_state"] == ReqState.ANALYZING.value
