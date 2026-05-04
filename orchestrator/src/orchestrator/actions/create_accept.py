"""create_accept: env-up → thanatos MCP dispatch (with v0.3-lite fallback).

Two top-level paths, chosen by the source repo's `.sisyphus/env.yaml`:

1. Multi-layer cross-repo (feat-cross-repo-env-orchestration spec): when source
   repo declares `needs:` in its manifest, sisyphus runs `make accept-env-up`
   across the full topology in order, injecting upstream `emits` into downstream
   `inputs` env vars, then dispatches the accept-agent with the merged endpoint
   bundle.
2. Legacy single-layer (preserved for repos without manifest, R8 backward
   compat): the original behavior — single `make accept-env-up`, parse JSON,
   either dispatch thanatos accept-agent or fall through to v0.3-lite.

Within each path the accept-agent dispatch / lite fallback split is identical:

- thanatos MCP (preferred): the `endpoint` JSON contains a `thanatos` block,
  agent runs scenarios via thanatos MCP.
- v0.3-lite fallback: per-repo shell script env-up → sleep → accept-smoke →
  env-down (used when `thanatos` block is absent).

R10 attribution: any accept-env-up failure during the multi-layer path writes
`failed_layer` / `failed_field` / `layers[]` to stage_runs.context before
emitting ACCEPT_ENV_UP_FAIL.
"""
from __future__ import annotations

import json
import shlex
import time

import structlog

from .. import cross_repo_env, k8s_runner, links, pr_links
from ..bkd import BKDClient
from ..config import settings
from ..cross_repo_env import (
    Manifest,
    ManifestError,
    PreResolveError,
    TopologyError,
)
from ..intent_tags import extract_pr_tag
from ..prompts import render
from ..state import Event
from ..store import db, req_state, stage_runs
from . import register, short_title
from ._clone import ensure_runner_with_clone
from ._integration_resolver import resolve_integration_dir
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)

_TIMEOUT_ENV_UP_SEC = 1800  # 30 min: helm install + wait ready + APK GHA build poll (5-10min) + download + adb install
_TIMEOUT_LITE_SEC = 1800   # 30 min for up × N + sleep + smoke × N + down × N
_TIMEOUT_MANIFEST_READ_SEC = 30  # cat .sisyphus/env.yaml on runner
_TIMEOUT_BRANCH_CHECK_SEC = 60   # git ls-remote per needs repo

# #247 Phase 1: 收 BKD stage agent issue id（人可续 follow-up 触发 PENDING_USER_REVIEW
# 反向通道的入口）。**机械 checker** issue（spec_lint / dev_cross_check / staging_test
# / pr_ci_watch）故意不列 —— 用户在那些 issue 续聊不会有 BKD agent 响应，给入口
# 反而误导。fixer 列出来：fixer 跑过且产生 dev/spec 改动后用户也可能想再 follow-up。
_RESUMABLE_STAGE_ISSUE_KEYS: tuple[tuple[str, str], ...] = (
    ("intake", "intake_issue_id"),
    ("analyze", "analyze_issue_id"),
    ("challenger", "challenger_issue_id"),
    ("fixer", "fixer_issue_id"),
)


def _build_bkd_entry_links(*, project_id: str, ctx: dict | None,
                           accept_issue_id: str) -> list[dict]:
    """收当前 ctx 里能让用户 follow-up 触发 PENDING_USER_REVIEW resume 的 BKD issue。

    返回 [{"label": "analyze", "url": "https://..."}]，accept agent 渲染进 PR
    管理 comment，告诉用户"想调整就在这些 issue 续聊"。

    缺 ctx 字段 / 渲不出 url 的条目静默跳过（None 不出现在用户视野里）。
    accept_issue_id 总是放最后，作为本轮 accept agent 自身的入口。intent issue 单独
    放最前 —— 它是整条 REQ 的总览卡片，PENDING_USER_REVIEW resume 的最自然入口
    （statusId 表态 + chat 续聊都从这）。
    """
    ctx = ctx or {}
    entries: list[dict] = []

    intent_id = ctx.get("intent_issue_id")
    if intent_id:
        url = links.bkd_issue_url(project_id, intent_id)
        if url:
            entries.append({"label": "intent", "url": url})

    for label, key in _RESUMABLE_STAGE_ISSUE_KEYS:
        iid = ctx.get(key)
        if not iid:
            continue
        url = links.bkd_issue_url(project_id, iid)
        if url:
            entries.append({"label": label, "url": url})

    if accept_issue_id:
        url = links.bkd_issue_url(project_id, accept_issue_id)
        if url:
            entries.append({"label": "accept", "url": url})

    return entries


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


def _parse_json_tail(stdout: str) -> tuple[dict | None, str]:
    """Extract last non-blank stdout line and JSON-decode it.

    Returns (parsed_dict_or_None, raw_last_line). None means tail wasn't a JSON
    object (caller surfaces a friendly env-up.fail).
    """
    last_line = ""
    for line in reversed(stdout.splitlines()):
        s = line.strip()
        if s:
            last_line = s
            break
    if not last_line:
        return None, ""
    try:
        parsed = json.loads(last_line)
    except json.JSONDecodeError:
        return None, last_line
    if not isinstance(parsed, dict):
        return None, last_line
    return parsed, last_line


async def _read_source_manifest(rc, req_id: str, source_basename: str) -> Manifest | None:
    """Read /workspace/source/<source_basename>/.sisyphus/env.yaml from runner pod.

    Returns None when the file is absent (R8 backward-compat trigger). Raises
    ManifestError when the file exists but fails schema validation.
    """
    path = f"/workspace/source/{source_basename}/.sisyphus/env.yaml"
    cmd = (
        f"if [ -f {shlex.quote(path)} ]; then "
        f"  echo __MANIFEST_FOUND__; "
        f"  cat {shlex.quote(path)}; "
        "else "
        "  echo __MANIFEST_MISSING__; "
        "fi"
    )
    result = await rc.exec_in_runner(req_id, command=cmd, timeout_sec=_TIMEOUT_MANIFEST_READ_SEC)
    if result.exit_code != 0:
        # probe failure (kubectl exec hiccup, broken shell, etc.) — fall back
        # to legacy single-layer path rather than escalating: an existing
        # single-layer REQ never had a manifest, and treating a probe error
        # the same as "no manifest" preserves R8 backward-compat. Schema
        # errors still surface (parse_manifest raises) since we only get there
        # when the file actually exists and is readable.
        log.warning(
            "create_accept.manifest_probe_failed",
            req_id=req_id, source=source_basename,
            exit_code=result.exit_code, stderr_tail=result.stderr[-200:],
        )
        return None
    out = result.stdout or ""
    if "__MANIFEST_MISSING__" in out:
        return None
    if "__MANIFEST_FOUND__" not in out:
        # unexpected — neither sentinel printed; treat as missing fail-open
        log.warning("create_accept.manifest_sentinel_missing", req_id=req_id, source=source_basename)
        return None
    body = out.split("__MANIFEST_FOUND__", 1)[1].lstrip("\n")
    return cross_repo_env.parse_manifest(body)


async def _read_cloned_manifest(
    rc, req_id: str, basename: str,
) -> Manifest | None:
    """Read manifest from an already-cloned /workspace/source/<basename>/."""
    path = f"/workspace/source/{basename}/.sisyphus/env.yaml"
    cmd = (
        f"if [ -f {shlex.quote(path)} ]; then "
        f"  cat {shlex.quote(path)}; "
        "else "
        "  printf '__MANIFEST_MISSING__'; "
        "fi"
    )
    result = await rc.exec_in_runner(req_id, command=cmd, timeout_sec=_TIMEOUT_MANIFEST_READ_SEC)
    if result.exit_code != 0:
        raise ManifestError(
            f"failed to read manifest at {path}: exit={result.exit_code}"
        )
    out = result.stdout or ""
    if out.strip() == "__MANIFEST_MISSING__":
        return None
    return cross_repo_env.parse_manifest(out)


async def _branch_exists_on_remote(
    rc, req_id: str, repo_full_name: str, branch: str,
) -> bool:
    """`git ls-remote --heads <https-with-token-url> <branch>` returns non-empty?

    Uses runner pod's $GH_TOKEN. Empty stdout = branch absent. Non-zero exit
    propagates as False (fail-closed; caller will fall back through the R6
    chain).
    """
    url = f"https://x-access-token:${{GH_TOKEN}}/github.com/{repo_full_name}.git"
    cmd = f"git ls-remote --heads {url} {shlex.quote(branch)} | head -n1"
    try:
        result = await rc.exec_in_runner(req_id, command=cmd, timeout_sec=_TIMEOUT_BRANCH_CHECK_SEC)
    except Exception as e:
        log.warning("create_accept.branch_check_crashed",
                    req_id=req_id, repo=repo_full_name, branch=branch, error=str(e))
        return False
    if result.exit_code != 0:
        return False
    return bool((result.stdout or "").strip())


async def _clone_needs_repo(
    rc, req_id: str, repo_full_name: str, branch: str,
) -> int:
    """Clone (or update) a needs repo into /workspace/source/<basename>/ on `branch`.

    Reuses /opt/sisyphus/scripts/sisyphus-clone-repos.sh — idempotent and
    handles auth via the pod's $GH_TOKEN. Returns helper exit code (0 = OK).
    """
    cmd = (
        f"/opt/sisyphus/scripts/sisyphus-clone-repos.sh "
        f"--base {shlex.quote(branch)} {shlex.quote(repo_full_name)}"
    )
    result = await rc.exec_in_runner(req_id, command=cmd, timeout_sec=600)
    if result.exit_code != 0:
        log.warning(
            "create_accept.clone_needs_failed",
            req_id=req_id, repo=repo_full_name, branch=branch,
            exit_code=result.exit_code, stderr_tail=result.stderr[-300:],
        )
    return result.exit_code


def _build_layer_env(
    repo: str,
    manifest: Manifest,
    bundle: dict[str, dict],
    base_env: dict[str, str],
) -> tuple[dict[str, str], str | None]:
    """Compose env vars for `make accept-env-up` of `repo`.

    `bundle` = {<upstream_repo>: {<emit_field>: <value>}} accumulated so far.
    Returns (env_dict, missing_ref) — missing_ref names the first
    `inputs[X] = <repo>.<field>` that can't be resolved (treated as fatal by
    caller).
    """
    env = dict(base_env)
    for var, (upstream_repo, fld) in manifest.inputs.items():
        upstream = bundle.get(upstream_repo)
        if upstream is None or fld not in upstream:
            return env, f"{var}={upstream_repo}.{fld}"
        env[var] = _coerce_env_value(upstream[fld])
    return env, None


def _coerce_env_value(v) -> str:
    """Convert a JSON-decoded value into a shell-safe string for env var."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


def _select_primary_endpoint(
    bundle: dict[str, dict], topo_order: list[str], source_repo: str,
) -> str | None:
    """Pick the user-facing endpoint string out of the merged bundle.

    Preference: source repo's emitted `endpoint` (last layer = user-facing
    interface like mobile lab). Fallback: first layer in topo order that emits
    an `endpoint` field. None when no layer emitted one.
    """
    src_emits = bundle.get(source_repo) or {}
    if "endpoint" in src_emits:
        return _coerce_env_value(src_emits["endpoint"])
    for repo in topo_order:
        emits = bundle.get(repo) or {}
        if "endpoint" in emits:
            return _coerce_env_value(emits["endpoint"])
    return None


async def _record_accept_attribution(
    req_id: str, *,
    failed_layer: str | None,
    failed_field: str | None,
    layers: list[dict],
) -> None:
    """Persist R10 attribution onto the open accept stage_runs row + req_state.context."""
    pool = db.get_pool()
    ctx_payload: dict = {"layers": layers}
    if failed_layer is not None:
        ctx_payload["failed_layer"] = failed_layer
    if failed_field is not None:
        ctx_payload["failed_field"] = failed_field
    try:
        await stage_runs.update_latest_stage_run_context(
            pool, req_id, "accept", ctx_payload,
        )
    except Exception as e:
        log.warning("create_accept.stage_runs_context_write_failed",
                    req_id=req_id, error=str(e))
    # also mirror the attribution onto req_state.context so admin / verifier can
    # read it without joining stage_runs
    await req_state.update_context(pool, req_id, {
        "accept_layers_attribution": ctx_payload,
    })


async def _dispatch_accept_agent(
    *, req_id: str, ctx: dict | None, body, source_issue_id: str,
    accept_env: dict, endpoint: str, namespace: str,
    accept_layers_topo: list[str] | None = None,
) -> dict:
    """Common path: open BKD accept-agent issue with prompt + bundle + thanatos block.

    `accept_layers_topo` records the topo ordering for downstream teardown
    (R7). When None this came through the legacy single-layer path and
    teardown_accept_env will fall back to its single-dir resolver.
    """
    proj = body.projectId
    namespace_for_pr = namespace
    branch_for_links = (ctx or {}).get("branch") or f"feat/{req_id}"
    pl = await pr_links.ensure_pr_links_in_ctx(
        req_id=req_id, branch=branch_for_links, ctx=ctx, project_id=proj,
    )
    extra_tags = pr_links.pr_link_tags(pl)

    thanatos_block = accept_env.get("thanatos") or {} if isinstance(accept_env, dict) else {}
    thanatos_pod = thanatos_block.get("pod") or None
    thanatos_namespace = thanatos_block.get("namespace") or namespace_for_pr
    thanatos_skill_repo = thanatos_block.get("skill_repo") or None

    # No thanatos block → fall back to v0.3-lite (existing behavior, both paths)
    if not thanatos_pod:
        log.info("create_accept.thanatos_block_missing", req_id=req_id,
                 endpoint=endpoint, fallback="v0.3-lite")
        return await _run_lite_fallback(req_id=req_id, ctx=ctx)

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [ACCEPT] AI-QA{short_title(ctx)}",
            tags=["accept", req_id, f"parent-id:{source_issue_id}", *extra_tags],
            status_id="todo",
            model=settings.agent_model,
        )
        bkd_entry_links = _build_bkd_entry_links(
            project_id=proj, ctx=ctx, accept_issue_id=issue.id,
        )
        pr_urls_dict = (ctx or {}).get("pr_urls") or {}
        prompt = render(
            "accept.md.j2",
            req_id=req_id,
            endpoint=endpoint,
            namespace=namespace_for_pr,
            source_issue_id=source_issue_id,
            accept_env=accept_env,
            project_id=proj,
            project_alias=proj,
            branch=branch_for_links,
            thanatos_pod=thanatos_pod,
            thanatos_namespace=thanatos_namespace,
            thanatos_skill_repo=thanatos_skill_repo,
            bkd_entry_links=bkd_entry_links,
            pr_urls=pr_urls_dict,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    pool = db.get_pool()
    ctx_patch = {
        "accept_issue_id": issue.id,
        "accept_endpoint": endpoint,
        "accept_namespace": namespace_for_pr,
    }
    if accept_layers_topo is not None:
        # record topo order so teardown_accept_env can iterate in reverse (R7)
        ctx_patch["accept_layers"] = accept_layers_topo
    await req_state.update_context(pool, req_id, ctx_patch)

    log.info("create_accept.done", req_id=req_id, accept_issue=issue.id,
             endpoint=endpoint, namespace=namespace_for_pr, thanatos_pod=thanatos_pod,
             multi_layer=accept_layers_topo is not None)
    return {
        "accept_issue_id": issue.id,
        "endpoint": endpoint,
        "namespace": namespace_for_pr,
    }


async def _ensure_runner_pod_ready(req_id: str, ctx: dict | None, tags) -> tuple[object | None, dict | None]:
    """Common preamble: clear stale accept_issue_id and ensure runner pod exists.

    Returns (runner_controller, error_response). On success returns
    (rc, None); on failure (None, response_dict).
    """
    pool = db.get_pool()
    if ctx and ctx.get("accept_issue_id"):
        await req_state.update_context(pool, req_id, {"accept_issue_id": None})

    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.warning("create_accept.no_runner_controller", req_id=req_id, error=str(e))
        return None, {
            "emit": Event.ACCEPT_PASS.value,
            "note": "no runner controller, skipped env-up",
        }

    status = await rc.get_runner_status(req_id)
    if status is None or status.pod_phase == "NotFound":
        log.info("create_accept.runner_pod_missing", req_id=req_id,
                 pod_phase=status.pod_phase if status else "NotFound")
        # intent:accept entry-point: REQ-id 不是真分支名（PR head 在 pr: tag）
        # 解析 pr:owner/repo#N → gh API 拿 PR headRefName 用做 clone branch
        branch = (ctx or {}).get("branch")
        if branch is None and "intent:accept" in (tags or []):
            pr_tag = extract_pr_tag(tags)
            if pr_tag is not None:
                pr_repo, pr_num = pr_tag
                try:
                    import asyncio
                    proc = await asyncio.create_subprocess_exec(
                        "gh", "pr", "view", str(pr_num),
                        "--repo", pr_repo, "--json", "headRefName", "-q", ".headRefName",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                    if proc.returncode == 0:
                        branch = stdout.decode().strip()
                        log.info("create_accept.intent_accept.pr_head_resolved",
                                 req_id=req_id, pr_repo=pr_repo, pr_num=pr_num, branch=branch)
                except Exception as e:
                    log.warning("create_accept.intent_accept.pr_head_resolve_failed",
                                req_id=req_id, error=str(e))
        if branch is None:
            branch = f"feat/{req_id}"
        _cloned, clone_exit = await ensure_runner_with_clone(
            req_id, ctx,
            tags=tags,
            default_repos=settings.default_involved_repos or [],
            branch=branch,
        )
        if clone_exit is not None:
            log.error("create_accept.ensure_runner_clone_failed",
                      req_id=req_id, exit_code=clone_exit)
            await req_state.update_context(pool, req_id, {
                "accept_result": "fail",
                "accept_error": f"ensure runner+clone failed: exit_code={clone_exit}",
            })
            return None, {
                "emit": Event.ACCEPT_ENV_UP_FAIL.value,
                "reason": f"runner pod clone failed: exit_code={clone_exit}",
            }
    return rc, None


async def _run_legacy_single_layer(*, req_id: str, ctx, body, tags, rc, namespace: str, source_issue_id: str) -> dict:
    """Pre-cross-repo single-layer accept path (R8 backward compat).

    Behavior is byte-identical to what create_accept did before
    feat-cross-repo-env-orchestration: scan /workspace for the integration dir,
    run `make accept-env-up` once, parse the JSON tail, dispatch.
    """
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

    accept_env, last_line = _parse_json_tail(result.stdout)
    if accept_env is None:
        log.warning("create_accept.env_up_bad_json", req_id=req_id,
                    last_line_preview=last_line[:200])
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": "env-up stdout tail is not JSON",
        }

    endpoint = accept_env.get("endpoint")
    if not endpoint:
        log.warning("create_accept.env_up_no_endpoint", req_id=req_id, accept_env=accept_env)
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": "env-up JSON missing endpoint",
        }

    return await _dispatch_accept_agent(
        req_id=req_id, ctx=ctx, body=body, source_issue_id=source_issue_id,
        accept_env=accept_env, endpoint=endpoint, namespace=namespace,
        accept_layers_topo=None,
    )


def _build_layers_skeleton(topo: list[str], source_repo: str, *, fail_index: int) -> list[dict]:
    """Pre-fill layers[] entries for early-failure paths (branch resolution, clone)."""
    out: list[dict] = []
    for i, repo in enumerate(topo):
        if i < fail_index:
            status = "success" if repo != source_repo else "skipped"
        elif i == fail_index:
            status = "failed"
        else:
            status = "skipped"
        out.append({"repo": repo, "status": status, "duration_ms": 0})
    return out


async def _walk_and_load_manifests(
    rc, req_id: str, source_repo: str, source_branch: str, source_manifest: Manifest,
) -> dict[str, Manifest | None]:
    """BFS the needs graph from source, fetching manifests for every reachable repo.

    Returns a dict mapping every reachable `OWNER/REPO` (including source) to
    its parsed Manifest, or None for a leaf repo with no `.sisyphus/env.yaml`
    (R2-S10 — leaf with no emits). Raises ManifestError on any clone or parse
    failure during the walk.
    """
    manifests: dict[str, Manifest | None] = {source_repo: source_manifest}
    queue: list[str] = list(source_manifest.needs)
    while queue:
        repo = queue.pop(0)
        if repo in manifests:
            continue
        if "/" not in repo:
            raise ManifestError(f"repo {repo!r} is not OWNER/REPO")
        basename = repo.split("/", 1)[1]
        # bootstrap branch: same-name first, else develop class default. We
        # don't know yet whether the same-name branch exists in this repo —
        # check now so we don't waste a clone.
        same_name = await _branch_exists_on_remote(rc, req_id, repo, source_branch)
        bootstrap_branch = (
            source_branch if same_name
            else (source_manifest.branches.get("develop") or "develop")
        )
        clone_exit = await _clone_needs_repo(rc, req_id, repo, bootstrap_branch)
        if clone_exit != 0:
            raise ManifestError(
                f"clone of {repo} on {bootstrap_branch} failed: exit_code={clone_exit}"
            )
        m = await _read_cloned_manifest(rc, req_id, basename)
        manifests[repo] = m
        if m is not None:
            queue.extend(m.needs)
    return manifests


@register("create_accept", idempotent=False)
async def create_accept(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("accept", Event.ACCEPT_PASS, req_id=req_id):
        pool = db.get_pool()
        await req_state.update_context(pool, req_id, {"accept_skipped": True})
        return rv

    # intent:accept entry-point（closes #400）: validate pr: tag
    if "intent:accept" in (tags or []):
        if extract_pr_tag(tags) is None:
            log.error("create_accept.intent_accept.missing_pr_tag", req_id=req_id)
            return {
                "emit": Event.ACCEPT_ENV_UP_FAIL.value,
                "reason": "intent:accept requires a pr:owner/repo#N tag to specify the PR",
            }

    source_issue_id = body.issueId
    namespace = f"accept-{req_id.lower()}"

    # Clear stale accept_issue_id from prior round (#315): create_accept env-up
    # 阶段 5-15min；watchdog 看 ctx.accept_issue_id 检 stuck，stale id 指向上轮
    # 早 escalate 关闭的 issue，没新事件 → 误判 watchdog_stuck → 强制 session.failed
    # 把当前 in-flight create_accept 打断。入口先清 stale id，让 watchdog 知道
    # 当前没 active acceptance agent，跳过 stuck 检查。
    rc, err = await _ensure_runner_pod_ready(req_id, ctx, tags)
    if err is not None:
        return err

    # Determine source repo basename from cloned_repos / default. Single-repo
    # REQs typically have one entry; multi-repo REQs declare the source first
    # by convention (sisyphus-clone-repos.sh order). Fall back to integration
    # resolver scan when ctx is empty.
    source_repo, source_basename = _resolve_source_repo(ctx)

    # If we can't identify a source repo from ctx, take the legacy single-layer
    # path which uses the integration resolver to pick a directory.
    if source_basename is None:
        log.info("create_accept.source_repo_unknown_legacy", req_id=req_id)
        return await _run_legacy_single_layer(
            req_id=req_id, ctx=ctx, body=body, tags=tags, rc=rc,
            namespace=namespace, source_issue_id=source_issue_id,
        )

    # Read source manifest (R8 backward-compat trigger when absent)
    try:
        source_manifest = await _read_source_manifest(rc, req_id, source_basename)
    except ManifestError as e:
        log.warning("create_accept.source_manifest_invalid", req_id=req_id, error=str(e))
        await _record_accept_attribution(
            req_id, failed_layer=source_repo, failed_field=None, layers=[],
        )
        return {"emit": Event.ACCEPT_ENV_UP_FAIL.value, "reason": str(e)}

    if source_manifest is None or not source_manifest.needs:
        # R8: no manifest, or manifest declares no needs (single-layer self-host)
        log.info("create_accept.single_layer", req_id=req_id,
                 reason="no manifest" if source_manifest is None else "manifest.needs empty")
        return await _run_legacy_single_layer(
            req_id=req_id, ctx=ctx, body=body, tags=tags, rc=rc,
            namespace=namespace, source_issue_id=source_issue_id,
        )

    # ── Multi-layer path ─────────────────────────────────────────────────
    log.info("create_accept.multi_layer_start", req_id=req_id,
             source=source_repo, needs=list(source_manifest.needs))
    try:
        manifests = await _walk_and_load_manifests(
            rc, req_id, source_repo,
            (ctx or {}).get("branch") or f"feat/{req_id}",
            source_manifest,
        )
    except ManifestError as e:
        log.warning("create_accept.manifest_walk_failed", req_id=req_id, error=str(e))
        await _record_accept_attribution(
            req_id, failed_layer=source_repo, failed_field=None, layers=[],
        )
        return {"emit": Event.ACCEPT_ENV_UP_FAIL.value, "reason": str(e)}

    try:
        topo = cross_repo_env.resolve_topology(
            source_repo, lambda r: manifests.get(r),
        )
    except TopologyError as e:
        log.warning("create_accept.topology_cycle", req_id=req_id, error=str(e))
        await _record_accept_attribution(
            req_id, failed_layer=source_repo, failed_field=None, layers=[],
        )
        return {"emit": Event.ACCEPT_ENV_UP_FAIL.value, "reason": str(e)}

    # Replace the in-place loader with the pre-built cache so _run_multi_layer
    # doesn't re-walk. We pass the pre-built manifests dict.
    return await _run_multi_layer_with_cache(
        req_id=req_id, ctx=ctx, body=body, tags=tags, rc=rc,
        namespace=namespace, source_issue_id=source_issue_id,
        source_repo=source_repo, source_basename=source_basename,
        source_manifest=source_manifest, topo=topo, manifests=manifests,
    )


def _resolve_source_repo(ctx: dict | None) -> tuple[str, str | None]:
    """Best-effort identify the source repo full name + basename from ctx.

    Returns (full_name, basename); when ctx lacks the info, returns (`unknown`,
    None) and caller falls through to the legacy resolver.
    """
    ctx = ctx or {}
    cloned = ctx.get("cloned_repos") or []
    if cloned:
        first = cloned[0]
        if isinstance(first, str) and "/" in first:
            return first, first.split("/", 1)[1]
    finalized = ctx.get("intake_finalized_intent") or {}
    involved = finalized.get("involved_repos") or ctx.get("involved_repos") or []
    if involved:
        first = involved[0]
        if isinstance(first, str) and "/" in first:
            return first, first.split("/", 1)[1]
    defaults = settings.default_involved_repos or []
    if defaults:
        first = defaults[0]
        if isinstance(first, str) and "/" in first:
            return first, first.split("/", 1)[1]
    return "unknown/unknown", None


async def _run_multi_layer_with_cache(
    *, req_id: str, ctx, body, tags, rc,
    namespace: str, source_issue_id: str,
    source_repo: str, source_basename: str, source_manifest: Manifest,
    topo: list[str], manifests: dict[str, Manifest | None],
) -> dict:
    """Drive the per-layer accept-env-up with a pre-resolved topology + manifest cache."""
    base_env = {
        "SISYPHUS_REQ_ID": req_id,
        "SISYPHUS_STAGE": "accept-env-up",
        "SISYPHUS_NAMESPACE": namespace,
    }
    source_branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    dir_map = cross_repo_env.workspace_dir_map(topo)

    # ── R12 pre-resolve ──────────────────────────────────────────────────
    # before any per-layer accept-env-up runs, resolve every pattern-form emit
    # against (manifests, REQ context). this seeds the bundle so consumers see
    # pattern-form values without parsing layer accept-env-up JSON output, and
    # makes the bundle observable at stage_runs.context.endpoint_bundle_pre_resolved
    # before any layer side-effect.
    req_context = {
        "SISYPHUS_NAMESPACE": namespace,
        "SISYPHUS_REQ_ID": req_id,
        "SISYPHUS_REQ_BRANCH": source_branch,
        "SISYPHUS_SOURCE_REPO_SHA": (ctx or {}).get("source_sha") or "",
    }
    pool = db.get_pool()
    try:
        pre_resolved = cross_repo_env.pre_resolve_endpoint_bundle(
            topo, lambda r: manifests.get(r), req_context,
        )
    except PreResolveError as e:
        log.warning(
            "create_accept.pre_resolve_failed",
            req_id=req_id, failed_layer=e.failed_layer, error=str(e),
        )
        layers_record = _build_layers_skeleton(
            topo, source_repo, fail_index=topo.index(e.failed_layer),
        )
        await _record_accept_attribution(
            req_id, failed_layer=e.failed_layer, failed_field=None, layers=layers_record,
        )
        try:
            await stage_runs.update_latest_stage_run_context(
                pool, req_id, "accept", {"failed_phase": e.failed_phase},
            )
        except Exception as ctx_exc:
            log.warning(
                "create_accept.pre_resolve_attribution_write_failed",
                req_id=req_id, error=str(ctx_exc),
            )
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": str(e),
            "failed_phase": e.failed_phase,
            "failed_layer": e.failed_layer,
        }
    if pre_resolved:
        try:
            await stage_runs.update_latest_stage_run_context(
                pool, req_id, "accept",
                {"endpoint_bundle_pre_resolved": pre_resolved},
            )
        except Exception as ctx_exc:
            log.warning(
                "create_accept.pre_resolve_persist_failed",
                req_id=req_id, error=str(ctx_exc),
            )

    # ── Branch resolution + idempotent re-clone on resolved branch ────────
    for repo in topo:
        if repo == source_repo:
            continue
        repo_manifest = manifests.get(repo) or Manifest()
        same_name_exists = await _branch_exists_on_remote(rc, req_id, repo, source_branch)
        cls = cross_repo_env.infer_branch_class(source_branch, source_manifest)
        candidate = repo_manifest.branches.get(cls)
        candidate_exists = (
            await _branch_exists_on_remote(rc, req_id, repo, candidate)
            if candidate else False
        )
        cache: dict[tuple[str, str], bool] = {(repo, source_branch): same_name_exists}
        if candidate:
            cache[(repo, candidate)] = candidate_exists

        def _exists(r: str, b: str, *, _cache=cache) -> bool:
            return _cache.get((r, b), False)

        resolution = cross_repo_env.resolve_branch(
            source_branch, source_manifest, repo, repo_manifest, _exists,
        )
        if resolution.branch is None:
            log.warning("create_accept.branch_resolution_failed",
                        req_id=req_id, repo=repo, failed_class=resolution.failed_class)
            layers_record = _build_layers_skeleton(topo, source_repo, fail_index=topo.index(repo))
            await _record_accept_attribution(
                req_id, failed_layer=repo, failed_field=None, layers=layers_record,
            )
            return {
                "emit": Event.ACCEPT_ENV_UP_FAIL.value,
                "reason": f"branch_resolution_failed for {repo} class={resolution.failed_class}",
            }
        clone_exit = await _clone_needs_repo(rc, req_id, repo, resolution.branch)
        if clone_exit != 0:
            layers_record = _build_layers_skeleton(topo, source_repo, fail_index=topo.index(repo))
            await _record_accept_attribution(
                req_id, failed_layer=repo, failed_field=None, layers=layers_record,
            )
            return {
                "emit": Event.ACCEPT_ENV_UP_FAIL.value,
                "reason": f"clone {repo} on {resolution.branch} failed: exit={clone_exit}",
            }

    # ── Sequential per-layer env-up ──────────────────────────────────────
    # bundle starts seeded with pattern-form emits (R12); bare-string emits get merged
    # in by the per-layer JSON parse below (R4).
    bundle: dict[str, dict] = {repo: dict(fields) for repo, fields in pre_resolved.items()}
    layers_record: list[dict] = [
        {"repo": r, "status": "skipped", "duration_ms": 0} for r in topo
    ]
    final_endpoint_json: dict | None = None

    for idx, repo in enumerate(topo):
        manifest = manifests.get(repo) or Manifest()
        layer_env, missing_ref = _build_layer_env(repo, manifest, bundle, base_env)
        if missing_ref is not None:
            log.warning("create_accept.layer_inputs_unresolved",
                        req_id=req_id, repo=repo, missing=missing_ref)
            layers_record[idx]["status"] = "failed"
            failed_field = missing_ref.split("=", 1)[-1].split(".", 1)[-1]
            await _record_accept_attribution(
                req_id, failed_layer=repo, failed_field=failed_field, layers=layers_record,
            )
            return {
                "emit": Event.ACCEPT_ENV_UP_FAIL.value,
                "reason": f"layer {repo} missing upstream input {missing_ref}",
                "failed_layer": repo,
                "failed_field": failed_field,
            }

        basename = dir_map[repo]
        layer_dir = f"/workspace/source/{basename}"
        log.info("create_accept.layer_up_start",
                 req_id=req_id, layer=repo, dir=layer_dir, idx=idx, total=len(topo))

        start = time.monotonic()
        try:
            result = await rc.exec_in_runner(
                req_id,
                command=f"cd {shlex.quote(layer_dir)} && make accept-env-up",
                env=layer_env,
                timeout_sec=_TIMEOUT_ENV_UP_SEC,
            )
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            layers_record[idx]["status"] = "failed"
            layers_record[idx]["duration_ms"] = duration_ms
            log.exception("create_accept.layer_up_crashed",
                          req_id=req_id, layer=repo, error=str(e))
            await _record_accept_attribution(
                req_id, failed_layer=repo, failed_field=None, layers=layers_record,
            )
            return {"emit": Event.ACCEPT_ENV_UP_FAIL.value, "error": str(e)[:200]}

        duration_ms = int((time.monotonic() - start) * 1000)
        layers_record[idx]["duration_ms"] = duration_ms

        if result.exit_code != 0:
            layers_record[idx]["status"] = "failed"
            log.warning("create_accept.layer_up_failed",
                        req_id=req_id, layer=repo, exit_code=result.exit_code,
                        stderr_tail=result.stderr[-300:])
            await _record_accept_attribution(
                req_id, failed_layer=repo, failed_field=None, layers=layers_record,
            )
            return {
                "emit": Event.ACCEPT_ENV_UP_FAIL.value,
                "exit_code": result.exit_code,
                "stderr_tail": result.stderr[-500:],
                "failed_layer": repo,
            }

        parsed, last_line = _parse_json_tail(result.stdout)
        if parsed is None:
            layers_record[idx]["status"] = "failed"
            log.warning("create_accept.layer_up_bad_json",
                        req_id=req_id, layer=repo, last_line=last_line[:200])
            await _record_accept_attribution(
                req_id, failed_layer=repo, failed_field=None, layers=layers_record,
            )
            return {
                "emit": Event.ACCEPT_ENV_UP_FAIL.value,
                "reason": f"layer {repo} stdout tail is not JSON",
                "failed_layer": repo,
            }

        # R4 amendment: pattern-form emits are pre-resolved (already in bundle from R12).
        # the JSON parse only extracts bare-string emits — pattern-form fields MUST NOT
        # be re-extracted from the layer's accept-env-up output.
        bare_string_emits = [
            fld for fld in manifest.emits if fld not in manifest.emit_patterns
        ]
        emits_extracted: dict = {}
        for fld in bare_string_emits:
            if fld not in parsed:
                layers_record[idx]["status"] = "failed"
                log.warning("create_accept.layer_emit_missing",
                            req_id=req_id, layer=repo, field=fld)
                await _record_accept_attribution(
                    req_id, failed_layer=repo, failed_field=fld, layers=layers_record,
                )
                return {
                    "emit": Event.ACCEPT_ENV_UP_FAIL.value,
                    "reason": f"layer {repo} missing emit field {fld!r}",
                    "failed_layer": repo,
                    "failed_field": fld,
                }
            emits_extracted[fld] = parsed[fld]
        # merge bare-string emits onto whatever R12 pre-resolve already seeded for this repo
        bundle.setdefault(repo, {}).update(emits_extracted)
        layers_record[idx]["status"] = "success"
        if repo == source_repo:
            final_endpoint_json = parsed

    # ── Dispatch ──────────────────────────────────────────────────────────
    await _record_accept_attribution(
        req_id, failed_layer=None, failed_field=None, layers=layers_record,
    )

    endpoint = _select_primary_endpoint(bundle, topo, source_repo)
    if endpoint is None:
        log.warning("create_accept.no_primary_endpoint", req_id=req_id, bundle=bundle)
        return {
            "emit": Event.ACCEPT_ENV_UP_FAIL.value,
            "reason": "no layer emitted an `endpoint` field (cannot drive accept-agent)",
        }

    accept_env: dict = {
        "endpoint": endpoint,
        "namespace": namespace,
        "bundle": bundle,
    }
    if final_endpoint_json:
        thanatos_block = final_endpoint_json.get("thanatos")
        if thanatos_block:
            accept_env["thanatos"] = thanatos_block

    return await _dispatch_accept_agent(
        req_id=req_id, ctx=ctx, body=body, source_issue_id=source_issue_id,
        accept_env=accept_env, endpoint=endpoint, namespace=namespace,
        accept_layers_topo=list(topo),
    )
