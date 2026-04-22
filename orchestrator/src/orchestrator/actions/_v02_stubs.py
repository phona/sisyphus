"""v0.2 stage 的 action stub（S2 占位，S4 填实现）。

当前 state.py 新加 3 个 transition 指向还没实现的 action：
- create_staging_test：dev.done 后创 BKD staging-test issue
- create_pr_ci_watch：staging pass 后创 BKD pr-ci-watch issue
- teardown_accept_env：accept.pass/fail 后 sisyphus 直调 ci-accept-env-down

S2 只要让 test_no_orphan_actions 通过；真正的逻辑挪到 S4 替换。
目前 stub 无副作用，收到 event 直接 emit 下一步伪造完成。

test_mode 下 skip 行为跟 v0.1 _skip.py 一致：直接发 pass/fail 事件短路。
"""
from __future__ import annotations

import structlog

from ..state import Event
from . import register
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


@register("create_staging_test")
async def create_staging_test(*, body, req_id, tags, ctx):
    """STUB：dev.done → 发 staging-test.pass 伪造通过（S4 实际会创 BKD issue）。"""
    if rv := skip_if_enabled("staging-test", Event.STAGING_TEST_PASS, req_id=req_id):
        return rv
    # S2 阶段没真实现，不抛 —— 返 stub 标识
    log.warning("v0.2.stub.create_staging_test", req_id=req_id,
                note="action not implemented; S4 will replace")
    return {"stub": "create_staging_test"}


@register("create_pr_ci_watch")
async def create_pr_ci_watch(*, body, req_id, tags, ctx):
    """STUB：staging pass → 创 BKD pr-ci-watch agent issue 轮询 GHA commit statuses。"""
    if rv := skip_if_enabled("pr-ci", Event.PR_CI_PASS, req_id=req_id):
        return rv
    log.warning("v0.2.stub.create_pr_ci_watch", req_id=req_id,
                note="action not implemented; S4 will replace")
    return {"stub": "create_pr_ci_watch"}


@register("teardown_accept_env")
async def teardown_accept_env(*, body, req_id, tags, ctx):
    """STUB：accept.pass/fail → 跑 ci-accept-env-down 再按 accept_result 分流。

    S4 会做：
    1. aissh kubectl exec runner make ci-accept-env-down
    2. 读 ctx.accept_result
    3. emit TEARDOWN_DONE_PASS 或 TEARDOWN_DONE_FAIL
    """
    # 暂时按 ctx.accept_result 发对应事件，让状态机能继续走下去（S4 会加真 env-down 调用）
    result = (ctx.get("accept_result") if isinstance(ctx, dict) else None) or "pass"
    next_event = (
        Event.TEARDOWN_DONE_PASS.value if result == "pass"
        else Event.TEARDOWN_DONE_FAIL.value
    )
    log.warning("v0.2.stub.teardown_accept_env", req_id=req_id, accept_result=result,
                note="action not implemented; S4 will replace with real env-down call")
    return {"stub": "teardown_accept_env", "emit": next_event}
