"""create_accept: env-up → thanatos MCP dispatch (with v0.3-lite fallback).

Two paths:
1. thanatos MCP (preferred): run `make accept-env-up`, parse endpoint JSON for
   `thanatos` block, dispatch accept-agent BKD issue with thanatos params.
2. v0.3-lite fallback: per-repo shell script env-up → sleep → accept-smoke →
   env-down (used when `thanatos` block is absent).
"""
from __future__ import annotations

import json

import structlog

from .. import k8s_runner, pr_links
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db, req_state
from . import register, short_title
from ._clone import ensure_runner_with_clone
from ._integration_resolver import resolve_integration_dir
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)

_TIMEOUT_ENV_UP_SEC = 1800  # 30 min: helm install + wait ready + APK GHA build poll (5-10min) + download + adb install
_TIMEOUT_LITE_SEC = 1800   # 30 min for up × N + sleep + smoke × N + down × N


def _build_lite_script(req_id: str, delay_sec: int) -> str:
    """Shell script: per-repo env-up → sleep → accept-smoke → env-down (v0.3-lite).

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


async def _run_lite_fallback(*, req_id: str, ctx: dict | None) -> dict:
    """v0.3-lite fallback: shell script per-repo accept env-up/smoke/down."""
    cloned_repos = list((ctx or {}).get("cloned_repos") or [])
    if not cloned_repos:
        log.info("create_accept.lite_no_repos", req_id=req_id)
        return {"emit": Event.ACCEPT_PASS.value, "note": "no cloned repos (vacuous pass)"}

    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.warning("create_accept.lite_no_runner", req_id=req_id, error=str(e))
        return {"emit": Event.ACCEPT_PASS.value, "note": "no runner controller, skipped env-up"}

    delay = settings.accept_smoke_delay_sec
    script = _build_lite_script(req_id, delay)

    try:
        result = await rc.exec_in_runner(
            req_id,
            command=script,
            env={"SISYPHUS_REQ_ID": req_id, "SISYPHUS_STAGE": "accept"},
            timeout_sec=_TIMEOUT_LITE_SEC,
        )
    except Exception as e:
        log.exception("create_accept.lite_crashed", req_id=req_id, error=str(e))
        pool = db.get_pool()
        await req_state.update_context(pool, req_id, {
            "accept_result": "fail",
            "accept_error": str(e)[:200],
        })
        return {"emit": Event.ACCEPT_FAIL.value, "error": str(e)[:200]}

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
            "create_accept.lite_failed",
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

    log.info("create_accept.lite_passed", req_id=req_id, duration_sec=result.duration_sec)
    await req_state.update_context(pool, req_id, {"accept_result": "pass"})
    return {"emit": Event.ACCEPT_PASS.value}


@register("create_accept", idempotent=False)
async def create_accept(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("accept", Event.ACCEPT_PASS, req_id=req_id):
        pool = db.get_pool()
        await req_state.update_context(pool, req_id, {"accept_skipped": True})
        return rv

    proj = body.projectId
    source_issue_id = body.issueId
    namespace = f"accept-{req_id.lower()}"

    # Phase 1: env-up via sisyphus (runner exec)
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.warning("create_accept.no_runner_controller", req_id=req_id, error=str(e))
        return {"emit": Event.ACCEPT_PASS.value, "note": "no runner controller, skipped env-up"}

    # Ensure runner pod exists: admin/resume may have skipped staging_test,
    # leaving the pod never created.  ensure_runner is idempotent (409 = skip).
    status = await rc.get_runner_status(req_id)
    if status is None or status.pod_phase == "NotFound":
        log.info("create_accept.runner_pod_missing", req_id=req_id,
                 pod_phase=status.pod_phase if status else "NotFound")
        branch = (ctx or {}).get("branch") or f"feat/{req_id}"
        _cloned, clone_exit = await ensure_runner_with_clone(
            req_id, ctx,
            tags=tags,
            default_repos=settings.default_involved_repos or [],
            branch=branch,
        )
        if clone_exit is not None:
            log.error("create_accept.ensure_runner_clone_failed",
                      req_id=req_id, exit_code=clone_exit)
            pool = db.get_pool()
            await req_state.update_context(pool, req_id, {
                "accept_result": "fail",
                "accept_error": f"ensure runner+clone failed: exit_code={clone_exit}",
            })
            return {
                "emit": Event.ACCEPT_ENV_UP_FAIL.value,
                "reason": f"runner pod clone failed: exit_code={clone_exit}",
            }

    resolved = await resolve_integration_dir(rc, req_id)
    if resolved.dir is None:
        log.warning("create_accept.no_integration_dir", req_id=req_id, reason=resolved.reason)
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": resolved.reason,
        }
    integration_dir = resolved.dir

    exec_env = {
        "SISYPHUS_REQ_ID": req_id,
        "SISYPHUS_STAGE": "accept-env-up",
        "SISYPHUS_NAMESPACE": namespace,
    }
    try:
        result = await rc.exec_in_runner(
            req_id,
            command=f"cd {integration_dir} && make accept-env-up",
            env=exec_env,
            timeout_sec=_TIMEOUT_ENV_UP_SEC,
        )
    except Exception as e:
        log.exception("create_accept.env_up_crashed", req_id=req_id, error=str(e))
        return {"emit": Event.ACCEPT_ENV_UP_FAIL.value, "error": str(e)[:200]}

    if result.exit_code != 0:
        log.warning("create_accept.env_up_failed", req_id=req_id,
                    exit_code=result.exit_code, stderr_tail=result.stderr[-500:])
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "exit_code": result.exit_code,
            "stderr_tail": result.stderr[-500:],
        }

    # Parse endpoint JSON from stdout tail
    last_line = ""
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line:
            last_line = line
            break
    try:
        accept_env = json.loads(last_line)
    except json.JSONDecodeError:
        log.warning("create_accept.env_up_bad_json", req_id=req_id,
                    last_line_preview=last_line[:200])
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": "env-up stdout tail is not JSON",
        }

    endpoint = accept_env.get("endpoint") if isinstance(accept_env, dict) else None
    if not endpoint:
        log.warning("create_accept.env_up_no_endpoint", req_id=req_id, accept_env=accept_env)
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": "env-up JSON missing endpoint",
        }

    # M1: optional thanatos block
    thanatos_block = accept_env.get("thanatos") or {} if isinstance(accept_env, dict) else {}
    thanatos_pod = thanatos_block.get("pod") or None
    thanatos_namespace = thanatos_block.get("namespace") or namespace
    thanatos_skill_repo = thanatos_block.get("skill_repo") or None

    # Fallback to v0.3-lite if thanatos block absent
    if not thanatos_pod:
        log.info("create_accept.thanatos_block_missing", req_id=req_id,
                 endpoint=endpoint, fallback="v0.3-lite")
        return await _run_lite_fallback(req_id=req_id, ctx=ctx)

    # Phase 2: dispatch accept-agent (thanatos MCP path)
    branch_for_links = (ctx or {}).get("branch") or f"feat/{req_id}"
    links = await pr_links.ensure_pr_links_in_ctx(
        req_id=req_id, branch=branch_for_links, ctx=ctx, project_id=proj,
    )
    extra_tags = pr_links.pr_link_tags(links)

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [ACCEPT] AI-QA{short_title(ctx)}",
            tags=["accept", req_id, f"parent-id:{source_issue_id}", *extra_tags],
            status_id="todo",
            model=settings.agent_model,
        )
        prompt = render(
            "accept.md.j2",
            req_id=req_id,
            endpoint=endpoint,
            namespace=namespace,
            source_issue_id=source_issue_id,
            accept_env=accept_env,
            project_id=proj,
            project_alias=proj,
            branch=branch_for_links,
            thanatos_pod=thanatos_pod,
            thanatos_namespace=thanatos_namespace,
            thanatos_skill_repo=thanatos_skill_repo,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "accept_issue_id": issue.id,
        "accept_endpoint": endpoint,
        "accept_namespace": namespace,
    })

    log.info("create_accept.done", req_id=req_id, accept_issue=issue.id,
             endpoint=endpoint, namespace=namespace, thanatos_pod=thanatos_pod)
    return {
        "accept_issue_id": issue.id,
        "endpoint": endpoint,
        "namespace": namespace,
    }
