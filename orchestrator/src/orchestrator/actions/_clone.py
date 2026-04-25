"""server-side clone helper：start_analyze 系列 action 用，把 involved_repos 落到
runner pod 的 /workspace/source/<basename>/。

入口：`clone_involved_repos_into_runner(req_id, ctx, *, tags, default_repos)`，
三种返回：
- (None, None)：runner controller 没就绪 / 所有 fallback 层都没拿到 repos →
  caller 跳过 clone 直接 dispatch agent（直接 analyze 路径兼容）
- (repos, None)：clone 成功，repos 是真跑过 helper 的列表
- (repos, exit_code)：clone 失败，exit_code 是 helper 退码（caller 应
  emit VERIFY_ESCALATE，不打 agent 进空 PVC）

REQ-clone-fallback-direct-analyze-1777119520：multi-layer fallback for direct
analyze entry。原版只读 ctx，intake 跳过时 ctx 是空的，sisyphus 把 clone
完全推给 agent prompt。新版按下列优先级解析：

1. ctx.intake_finalized_intent.involved_repos —— intake 路径产物
2. ctx.involved_repos —— 直接被 caller 写到 ctx 的 involved
3. tags 里 `repo:<org>/<name>` 形式的 explicit opt-in
4. settings.default_involved_repos —— 单仓部署的 last-resort

L3/L4 都是显式信号：tag 是用户在 BKD intent issue 上挂的，env 是 operator
在 sisyphus 部署时配的。**故意不**从 issue 标题 / prompt 自由文本解析
slug —— 假阳性风险高（"src/orchestrator"、"M14b/M14c" 之类路径会误命中），
不如等用户显式打 tag 或 ops 配 default。
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Iterable

import structlog

from .. import k8s_runner

log = structlog.get_logger(__name__)

_CLONE_HELPER = "/opt/sisyphus/scripts/sisyphus-clone-repos.sh"
_CLONE_TIMEOUT_SEC = 600

# `repo:<org>/<name>` BKD tag 形式。github org/repo slug 规则：
# org 字母数字 + 连字符；repo 字母数字 + . _ -。
_REPO_TAG_PREFIX = "repo:"
_REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]*$")


def _normalize_repos(raw: object) -> list[str]:
    """把任意 iterable 过滤成 list[str]，丢非字符串 / 空串。顺序保留 + 去重。"""
    if not isinstance(raw, (list, tuple)):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        s = item.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _extract_repo_tags(tags: Iterable[str] | None) -> list[str]:
    """从 BKD issue tags 抽 `repo:<org>/<name>` 并校验 slug 合法。"""
    if not tags:
        return []
    out: list[str] = []
    for t in tags:
        if not isinstance(t, str) or not t.startswith(_REPO_TAG_PREFIX):
            continue
        slug = t[len(_REPO_TAG_PREFIX):].strip()
        if _REPO_SLUG_RE.match(slug):
            out.append(slug)
        else:
            log.warning("clone.invalid_repo_tag", tag=t)
    # 去重保留顺序
    seen: set[str] = set()
    return [r for r in out if not (r in seen or seen.add(r))]


def resolve_repos(
    ctx: dict | None,
    *,
    tags: Iterable[str] | None = None,
    default_repos: Iterable[str] | None = None,
) -> tuple[list[str], str]:
    """multi-layer fallback。返回 (repos, source_label)。

    source_label 给 log / obs 用，标明 repos 是从哪一层取到的。
    """
    finalized = (ctx or {}).get("intake_finalized_intent") or {}
    layers: list[tuple[str, object]] = [
        ("ctx.intake_finalized_intent.involved_repos", finalized.get("involved_repos")),
        ("ctx.involved_repos", (ctx or {}).get("involved_repos")),
        ("tags.repo", _extract_repo_tags(tags)),
        ("settings.default_involved_repos", list(default_repos or [])),
    ]
    for label, raw in layers:
        repos = _normalize_repos(raw)
        if repos:
            return repos, label
    return [], "none"


async def clone_involved_repos_into_runner(
    req_id: str,
    ctx: dict | None,
    *,
    tags: Iterable[str] | None = None,
    default_repos: Iterable[str] | None = None,
) -> tuple[list[str] | None, int | None]:
    """在 runner pod 里跑 sisyphus-clone-repos.sh。

    返回 (repos, exit_code)：
    - (None, None)：跳过（无 controller 或所有 fallback 都没 repos），
      caller 继续 dispatch agent
    - (repos, None)：成功（helper exit 0）
    - (repos, exit_code)：失败（helper 非 0），caller 应 escalate
    """
    repos, source = resolve_repos(ctx, tags=tags, default_repos=default_repos)
    if not repos:
        log.info("clone.skip_no_repos", req_id=req_id)
        return None, None

    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        # dev 环境无 K8s：跳过 server-side clone，agent 自己 clone
        log.warning("clone.no_runner_controller", req_id=req_id, error=str(e))
        return None, None

    args = " ".join(shlex.quote(r) for r in repos)
    cmd = f"{_CLONE_HELPER} {args}"
    log.info("clone.exec", req_id=req_id, repos=repos, source=source)
    result = await rc.exec_in_runner(req_id, cmd, timeout_sec=_CLONE_TIMEOUT_SEC)

    if result.exit_code != 0:
        log.error(
            "clone.failed", req_id=req_id, repos=repos, source=source,
            exit_code=result.exit_code,
            stderr_tail=result.stderr[-512:] if result.stderr else "",
        )
        return repos, result.exit_code

    log.info("clone.done", req_id=req_id, repos=repos, source=source,
             duration_sec=round(result.duration_sec, 1))
    return repos, None
