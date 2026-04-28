"""create_accept (v0.3-lite): per-repo accept-env-up + sleep + accept-smoke + accept-env-down.

不接 thanatos MCP（descope）：
1. 读 ctx.cloned_repos，对 /workspace/source/<name>/ 下每仓跑三段 make：
   env-up → smoke-delay → accept-smoke → env-down（best-effort）
2. accept-env-up / accept-smoke target 缺失 → fail-open skip 该仓 + warn
3. 任一仓 env-up / accept-smoke 失败 → emit accept.fail，ctx 写 accept_fail_repos
4. 全 pass → emit accept.pass
5. cloned_repos 为空 → accept.pass（vacuous true）

accept-env-down 在 script 里完成（best-effort）；teardown_accept_env 后续仍会
重跑一次（幂等，无害）。teardown 按 ctx.accept_result 分流，不再依赖 BKD agent tags。
"""
from __future__ import annotations

import structlog

from .. import k8s_runner
from ..config import settings
from ..state import Event
from ..store import db, req_state
from . import register
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)

_TIMEOUT_SEC = 1800  # 30 min：up × N repos + sleep + smoke × N + down × N


def _build_accept_script(req_id: str, delay_sec: int) -> str:
    """Shell script: per-repo env-up → sleep → accept-smoke → env-down。

    最后一行输出 PASS 或 FAIL:<repo1>,<repo2>；exit code 0/1 对应。
    target 缺失时 fail-open skip（不爆整体）。env-down 失败 || true 不计入 fail。
    """
    return (
        "set -o pipefail; "
        "fail=0; "
        'fail_list=""; '

        # ── Phase 1: env-up ──────────────────────────────────────────────
        "for repo in /workspace/source/*/; do "
        '  [ -d "$repo" ] || continue; '
        '  name=$(basename "$repo"); '
        # make -n でターゲット存在チェック（exit!=0 = No rule / other error → skip）
        '  if ! make -C "$repo" -n accept-env-up >/dev/null 2>&1; then '
        '    echo "[warn] accept-env-up target missing in $name, skipping" >&2; '
        "    continue; "
        "  fi; "
        '  echo "=== accept-env-up: $name ===" >&2; '
        '  if ! make -C "$repo" accept-env-up '
        '       SISYPHUS_REQ_ID="${SISYPHUS_REQ_ID}" '
        '       SISYPHUS_STAGE="accept-env-up" '
        '       SISYPHUS_NAMESPACE="accept-${SISYPHUS_REQ_ID}"; then '
        '    echo "=== FAIL accept-env-up: $name ===" >&2; '
        "    fail=1; "
        '    fail_list="${fail_list:+$fail_list,}$name"; '
        "  fi; "
        "done; "

        # ── Phase 2: smoke delay ─────────────────────────────────────────
        f"sleep {delay_sec}; "

        # ── Phase 3: accept-smoke ────────────────────────────────────────
        "for repo in /workspace/source/*/; do "
        '  [ -d "$repo" ] || continue; '
        '  name=$(basename "$repo"); '
        '  if ! make -C "$repo" -n accept-smoke >/dev/null 2>&1; then '
        '    echo "[warn] accept-smoke target missing in $name, skipping" >&2; '
        "    continue; "
        "  fi; "
        '  echo "=== accept-smoke: $name ===" >&2; '
        '  if ! make -C "$repo" accept-smoke '
        '       SISYPHUS_REQ_ID="${SISYPHUS_REQ_ID}" '
        '       SISYPHUS_STAGE="accept-smoke"; then '
        '    echo "=== FAIL accept-smoke: $name ===" >&2; '
        "    fail=1; "
        '    fail_list="${fail_list:+$fail_list,}$name"; '
        "  fi; "
        "done; "

        # ── Phase 4: env-down best-effort ───────────────────────────────
        "for repo in /workspace/source/*/; do "
        '  [ -d "$repo" ] || continue; '
        '  name=$(basename "$repo"); '
        '  if make -C "$repo" -n accept-env-down >/dev/null 2>&1; then '
        '    echo "=== accept-env-down: $name ===" >&2; '
        '    make -C "$repo" accept-env-down '
        '         SISYPHUS_REQ_ID="${SISYPHUS_REQ_ID}" '
        '         SISYPHUS_STAGE="accept-env-down" || true; '
        "  fi; "
        "done; "

        # ── Final status ────────────────────────────────────────────────
        'if [ "$fail" -ne 0 ]; then '
        '  echo "FAIL:${fail_list}"; '
        "  exit 1; "
        "fi; "
        'echo "PASS"; '
    )


@register("create_accept", idempotent=False)
async def create_accept(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("accept", Event.ACCEPT_PASS, req_id=req_id):
        pool = db.get_pool()
        await req_state.update_context(pool, req_id, {"accept_skipped": True})
        return rv

    cloned_repos = list((ctx or {}).get("cloned_repos") or [])
    if not cloned_repos:
        log.info("create_accept.no_repos", req_id=req_id)
        return {"emit": Event.ACCEPT_PASS.value, "note": "no cloned repos (vacuous pass)"}

    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.warning("create_accept.no_runner_controller", req_id=req_id, error=str(e))
        return {"emit": Event.ACCEPT_PASS.value, "note": "no runner controller, skipped env-up"}

    delay = settings.accept_smoke_delay_sec
    script = _build_accept_script(req_id, delay)

    try:
        result = await rc.exec_in_runner(
            req_id,
            command=script,
            env={"SISYPHUS_REQ_ID": req_id, "SISYPHUS_STAGE": "accept"},
            timeout_sec=_TIMEOUT_SEC,
        )
    except Exception as e:
        log.exception("create_accept.crashed", req_id=req_id, error=str(e))
        pool = db.get_pool()
        await req_state.update_context(pool, req_id, {
            "accept_result": "fail",
            "accept_error": str(e)[:200],
        })
        return {"emit": Event.ACCEPT_FAIL.value, "error": str(e)[:200]}

    # 从最后一行解 PASS / FAIL:<repos>
    fail_repos: list[str] = []
    last_line = ""
    for line in reversed((result.stdout or "").splitlines()):
        stripped = line.strip()
        if stripped:
            last_line = stripped
            break

    pool = db.get_pool()
    if result.exit_code != 0:
        if last_line.startswith("FAIL:"):
            raw = last_line[5:].strip()
            fail_repos = [r.strip() for r in raw.split(",") if r.strip()]
        log.warning(
            "create_accept.failed",
            req_id=req_id,
            exit_code=result.exit_code,
            fail_repos=fail_repos,
            stderr_tail=(result.stderr or "")[-500:],
        )
        await req_state.update_context(pool, req_id, {
            "accept_result": "fail",
            "accept_fail_repos": fail_repos,
        })
        return {
            "emit": Event.ACCEPT_FAIL.value,
            "fail_repos": fail_repos,
            "exit_code": result.exit_code,
        }

    log.info("create_accept.passed", req_id=req_id, duration_sec=result.duration_sec)
    await req_state.update_context(pool, req_id, {"accept_result": "pass"})
    return {"emit": Event.ACCEPT_PASS.value}
