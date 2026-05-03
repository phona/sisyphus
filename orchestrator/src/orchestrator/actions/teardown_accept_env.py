"""teardown_accept_env（v0.2 + cross-repo R7）：accept.pass / accept.fail 后必跑 env-down。

设计要点：
- 纯 infra，不经 BKD agent（直接 sisyphus k8s_runner.exec_in_runner 调 Makefile）
- 幂等：make accept-env-down 自己要是幂等的（repo 契约）
- 失败只 warning，不阻塞状态机（防泄漏资源属于重要，但挂一个 helm uninstall 不该拖垮整个 REQ）
- 按 ctx.accept_result 分流 emit：TEARDOWN_DONE_PASS / TEARDOWN_DONE_FAIL
- 多层场景（feat-cross-repo-env-orchestration spec R7）按 `ctx.accept_layers`
  反序 best-effort 跑 `make accept-env-down`；任一层失败不阻塞剩余层
- 单层（无 manifest）走 `_integration_resolver` —— 与 create_accept 同源
"""
from __future__ import annotations

import shlex

import structlog

from .. import cross_repo_env, k8s_runner
from ..state import Event
from ..store import db, req_state
from . import register
from ._integration_resolver import resolve_integration_dir
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)

_TIMEOUT_TEARDOWN_SEC = 300


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
        accept_layers = list((ctx or {}).get("accept_layers") or [])
        if len(accept_layers) > 1:
            env_down_ok = await _run_multi_layer_teardown(
                rc, req_id=req_id, layers=accept_layers,
            )
        else:
            env_down_ok = await _run_single_layer_teardown(rc, req_id=req_id)

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


async def _run_single_layer_teardown(rc, *, req_id: str) -> bool:
    """既有 single-layer 路径：integration_resolver → cd → make accept-env-down。"""
    resolved = await resolve_integration_dir(rc, req_id)
    if resolved.dir is None:
        log.warning("teardown.no_integration_dir", req_id=req_id, reason=resolved.reason)
        return False
    return await _exec_env_down(rc, req_id=req_id, layer_dir=resolved.dir, layer="<single>")


async def _run_multi_layer_teardown(rc, *, req_id: str, layers: list[str]) -> bool:
    """R7 best-effort reverse-order teardown。

    `layers` 是 create_accept 写入 `ctx.accept_layers` 的拓扑序（leaves first）。
    按 R7 反序（source repo 先拆，叶子最后拆 —— wait spec 说反序拓扑 = 反过来 = source first;
    但 spec R7-S26: "topology [server-go, flutter] (server-go is leaf); 反过来 →
    flutter teardown 先跑，server-go 后跑"。所以 reverse(topo) 就是反序 = 倒着遍历）。
    """
    # spec 写法：topology 列表是 leaves-first（[server-go, flutter] 中 server-go 是叶
    # = 先 up；flutter 是 source = 后 up）。teardown reverse → flutter first, server-go
    # second → 等价于 reversed(layers)。
    layer_dir_map = cross_repo_env.workspace_dir_map(layers)
    all_ok = True
    for repo in reversed(layers):
        basename = layer_dir_map[repo]
        layer_dir = f"/workspace/source/{basename}"
        ok = await _exec_env_down(rc, req_id=req_id, layer_dir=layer_dir, layer=repo)
        if not ok:
            all_ok = False
    return all_ok


async def _exec_env_down(rc, *, req_id: str, layer_dir: str, layer: str) -> bool:
    """Single layer best-effort `make accept-env-down`。失败只 warning，不抛。"""
    try:
        result = await rc.exec_in_runner(
            req_id,
            command=f"cd {shlex.quote(layer_dir)} && make accept-env-down",
            env={
                "SISYPHUS_REQ_ID": req_id,
                "SISYPHUS_STAGE": "accept-teardown",
                "SISYPHUS_NAMESPACE": f"accept-{req_id.lower()}",
            },
            timeout_sec=_TIMEOUT_TEARDOWN_SEC,
        )
    except Exception as e:
        log.warning("teardown.failed", req_id=req_id, layer=layer, error=str(e))
        return False
    log.info(
        "teardown.done", req_id=req_id, layer=layer, layer_dir=layer_dir,
        exit_code=result.exit_code, duration_sec=result.duration_sec,
        stderr_tail=result.stderr[-500:] if result.stderr else "",
    )
    return result.exit_code == 0
