"""teardown_accept_env（v0.2）：accept.pass / accept.fail 后必跑 env-down。

设计要点：
- 纯 infra，不经 BKD agent（直接 sisyphus k8s_runner.exec_in_runner 调 Makefile）
- 幂等：make accept-env-down 自己要是幂等的（repo 契约）
- 失败只 warning，不阻塞状态机（防泄漏资源属于重要，但挂一个 helm uninstall 不该拖垮整个 REQ）
- 按 ctx.accept_result 分流 emit：TEARDOWN_DONE_PASS / TEARDOWN_DONE_FAIL
- 工作目录由 _integration_resolver 决策，与 create_accept 保持同源（self-host 回退）
"""
from __future__ import annotations

import structlog

from .. import k8s_runner
from ..state import Event
from ..store import db, req_state
from . import register
from ._integration_resolver import resolve_integration_dir
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


@register("teardown_accept_env", idempotent=True)
async def teardown_accept_env(*, body, req_id, tags, ctx):
    """跑 accept-env-down 清 lab，然后按 accept_result emit 下一步事件。"""
    # accept 被 skip 时（skip_accept=true，ttpos-arch-lab 没接前的常态），
    # teardown 也跳：没真 env 可拆，也没 result:pass tag 可读 — 强行读会默认 fail
    # 误推 bugfix 链。复用 skip_accept flag，emit TEARDOWN_DONE_PASS（accept 既然跳过被
    # 视为通过，teardown 也应通过）。
    if rv := skip_if_enabled("accept", Event.TEARDOWN_DONE_PASS, req_id=req_id):
        return rv

    # 1. accept_result 优先从 ctx 读（v0.3-lite create_accept 在 emit 前已写入）；
    #    回退到 tags（旧 BKD-agent 路径：agent 在 accept issue 上打 result:pass/fail tag）
    accept_result = (ctx or {}).get("accept_result")
    if not accept_result:
        tagset = set(tags or [])
        accept_result = "pass" if "result:pass" in tagset else "fail"

    # 2. 把 accept_result 写 ctx（provenance）
    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {"accept_result": accept_result})

    # 3. 跑 env-down（best-effort，失败只 warning）
    env_down_ok = False
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        # runner controller 没初始化（本地 dev / kubeconfig 缺）—— 跳过清理
        log.warning("teardown.no_controller", req_id=req_id, error=str(e))
    else:
        # integration 优先 / 单仓 source self-host 回退；与 create_accept 同源
        resolved = await resolve_integration_dir(rc, req_id)
        if resolved.dir is None:
            log.warning("teardown.no_integration_dir", req_id=req_id, reason=resolved.reason)
        else:
            try:
                result = await rc.exec_in_runner(
                    req_id,
                    command=f"cd {resolved.dir} && make accept-env-down",
                    env={
                        "SISYPHUS_REQ_ID": req_id,
                        "SISYPHUS_STAGE": "accept-teardown",
                        "SISYPHUS_NAMESPACE": f"accept-{req_id.lower()}",
                    },
                    timeout_sec=300,
                )
                env_down_ok = result.exit_code == 0
                log.info(
                    "teardown.done", req_id=req_id, integration_dir=resolved.dir,
                    exit_code=result.exit_code, duration_sec=result.duration_sec,
                    stderr_tail=result.stderr[-500:] if result.stderr else "",
                )
            except Exception as e:
                log.warning("teardown.failed", req_id=req_id, error=str(e))

    # 4. emit 下一步 event（不管 teardown 成不成，都按原 accept_result 分流）
    next_event = (
        Event.TEARDOWN_DONE_PASS.value if accept_result == "pass"
        else Event.TEARDOWN_DONE_FAIL.value
    )
    return {
        "emit": next_event,
        "accept_result": accept_result,
        "env_down_ok": env_down_ok,
    }
