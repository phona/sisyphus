"""PR-link discovery + tag synthesis (REQ-issue-link-pr-quality-base-1777218242).

每个 sisyphus 为 REQ 创建的 BKD issue 都应带 `pr:<owner>/<repo>#<N>` tag，
让人在 BKD UI 看 sub-issue 时能一跳跳到 GitHub PR。

discover 时机：lazy。第一次 callsite 调 `ensure_pr_links_in_ctx` 时：
1. 优先读 `ctx.pr_links` 缓存（O(1)）
2. miss → runner pod ls /workspace/source/* + GH REST 查 open PR
3. 失败（无 controller / GH 5xx / 0 PR）一律 best-effort 返 [],
   **绝不**抛异常出来阻断 issue 创建
4. 成功 → 持久化 ctx.pr_links + 顺手 backfill ctx 里已知的 sisyphus issue id
   的 tag（典型场景：analyze issue 在创建时 PR 还没开，第一次 verifier
   issue 创建时回填）

之所以 cache 在 ctx：
- ctx 是 jsonb，update_context append 即可，没新表
- ctx 是 REQ 维度，刚好对应"PR 是 REQ 的产物"语义
- observability dashboard 直接 SELECT context->'pr_links' 就能查"该 REQ 开了哪些 PR"
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
import structlog

from . import k8s_runner
from .config import settings
from .store import db, req_state

log = structlog.get_logger(__name__)

_GH_API = "https://api.github.com"
_DISCOVER_TIMEOUT_SEC = 30
_GH_REQUEST_TIMEOUT = 15.0

# git@github.com:owner/repo(.git) | https://github.com/owner/repo(.git)
_REMOTE_RE = re.compile(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$")

# ctx 里 sisyphus 已创/记的 issue id key 集合 —— first-time discovery 时回填这些
_KNOWN_ISSUE_ID_KEYS = (
    "analyze_issue_id",
    "staging_test_issue_id",
    "pr_ci_watch_issue_id",
    "accept_issue_id",
    "archive_issue_id",
)


@dataclass(frozen=True)
class PrLink:
    """单条 GitHub PR 引用：owner/repo + PR 号 + html_url。"""

    repo: str        # "owner/repo"
    number: int
    url: str

    def tag(self) -> str:
        return f"pr:{self.repo}#{self.number}"

    def to_dict(self) -> dict:
        return {"repo": self.repo, "number": self.number, "url": self.url}


def pr_link_tags(links: list[PrLink]) -> list[str]:
    """`[PrLink(...)]` → `["pr:owner/repo#N", ...]`，顺序保留。"""
    return [link.tag() for link in links]


def from_ctx(ctx: dict | None) -> list[PrLink]:
    """解析 ctx.pr_links list[dict] → list[PrLink]。malformed entry 静默跳过。

    防御老版本 sisyphus 写过的 ctx drift forward：缺 key / 类型错的条目跳过，
    不抛异常。
    """
    raw = (ctx or {}).get("pr_links") or []
    if not isinstance(raw, list):
        return []
    out: list[PrLink] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            link = PrLink(
                repo=str(entry["repo"]),
                number=int(entry["number"]),
                url=str(entry.get("url", "")),
            )
        except (KeyError, TypeError, ValueError):
            continue
        out.append(link)
    return out


async def _discover_repos_via_runner(req_id: str) -> list[str]:
    """ls /workspace/source/*/.git → ['owner/repo', ...]。失败返 []。

    跟 actions/create_pr_ci_watch._discover_repos_from_runner 同样 helper 逻辑，
    避免循环 import 自己写一遍。
    """
    cmd = (
        "for d in /workspace/source/*/; do "
        "  [ -d \"$d/.git\" ] && git -C \"$d\" remote get-url origin 2>/dev/null; "
        "done"
    )
    try:
        rc = k8s_runner.get_controller()
    except RuntimeError as e:
        log.debug("pr_links.no_runner_controller", req_id=req_id, error=str(e))
        return []
    try:
        result = await rc.exec_in_runner(req_id, cmd, timeout_sec=_DISCOVER_TIMEOUT_SEC)
    except Exception as e:
        log.warning("pr_links.discover_repos_failed", req_id=req_id, error=str(e))
        return []
    repos: list[str] = []
    seen: set[str] = set()
    for line in (result.stdout or "").splitlines():
        m = _REMOTE_RE.search(line.strip())
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        repos.append(slug)
    return repos


def _gh_headers() -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        h["Authorization"] = f"Bearer {settings.github_token}"
    return h


async def _get_open_pr(
    client: httpx.AsyncClient, repo: str, branch: str,
) -> PrLink | None:
    """查 repo 在 head={owner}:{branch} 的第一条 open PR。

    单 repo 错误 best-effort：HTTP error / 解析错误返 None + log warning，
    上层继续遍历下一 repo。
    """
    if "/" not in repo:
        return None
    owner, _ = repo.split("/", 1)
    try:
        r = await client.get(
            f"/repos/{repo}/pulls",
            params={"head": f"{owner}:{branch}", "state": "open"},
        )
        r.raise_for_status()
        pulls = r.json()
    except httpx.HTTPError as e:
        log.warning("pr_links.gh_api_error", repo=repo, branch=branch, error=str(e))
        return None
    except (ValueError, TypeError) as e:
        log.warning("pr_links.gh_api_parse_error", repo=repo, branch=branch, error=str(e))
        return None
    if not pulls:
        return None
    pr = pulls[0]
    try:
        return PrLink(
            repo=repo,
            number=int(pr["number"]),
            url=str(pr.get("html_url", "")),
        )
    except (KeyError, TypeError, ValueError) as e:
        log.warning("pr_links.pr_payload_invalid", repo=repo, error=str(e))
        return None


async def discover_pr_links(
    req_id: str,
    branch: str,
    repos: list[str] | None = None,
) -> list[PrLink]:
    """对每个 repo 查 open PR。repos=None → runner discovery 拿仓清单。

    任意一步失败均不抛异常出来：返回当前已收集到的列表（best-effort）。
    """
    repo_list = repos if repos is not None else await _discover_repos_via_runner(req_id)
    if not repo_list:
        return []
    links: list[PrLink] = []
    async with httpx.AsyncClient(
        base_url=_GH_API, headers=_gh_headers(), timeout=_GH_REQUEST_TIMEOUT,
    ) as client:
        for repo in repo_list:
            link = await _get_open_pr(client, repo, branch)
            if link is not None:
                links.append(link)
    return links


def _gather_known_issue_ids(ctx: dict | None) -> list[str]:
    """从 ctx 收集已知的 sisyphus issue id（去重，保留出现顺序）。"""
    if not ctx:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for key in _KNOWN_ISSUE_ID_KEYS:
        v = ctx.get(key)
        if isinstance(v, str) and v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


async def _backfill_known_issues(
    project_id: str,
    issue_ids: list[str],
    links: list[PrLink],
) -> None:
    """给 ctx 里已知的 sisyphus issue 补 pr:* tag。每条独立 try/except，单条失败不影响其他。"""
    if not issue_ids or not links:
        return
    # 延迟 import 避免顶层循环（bkd_rest 导入了 bkd._to_issue → 闭环）
    from .bkd import BKDClient

    add_tags = pr_link_tags(links)
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        for iid in issue_ids:
            try:
                await bkd.merge_tags_and_update(project_id, iid, add=add_tags)
            except Exception as e:
                log.warning(
                    "pr_links.backfill_failed", issue_id=iid, error=str(e),
                )


async def ensure_pr_links_in_ctx(
    *,
    req_id: str,
    branch: str,
    ctx: dict | None,
    project_id: str,
) -> list[PrLink]:
    """读缓存 → 没有则 discover + 持久化 + backfill 已知 issue。

    返回 PrLink 列表（可能为空）。callsite 用 `pr_link_tags(...)` 拼到
    新建 issue 的 tags 数组里。

    不抛异常：任何失败均 log warning 并返当前能拿到的列表（多数是 []）。
    """
    cached = from_ctx(ctx)
    if cached:
        return cached

    links = await discover_pr_links(req_id, branch)
    if not links:
        return []

    # 持久化 ctx.pr_links（让后续 callsite 命中 cache）
    pool = db.get_pool()
    try:
        await req_state.update_context(
            pool, req_id,
            {"pr_links": [link.to_dict() for link in links]},
        )
    except Exception as e:
        # ctx 更新失败不阻断本次 callsite —— 当前调用还是返 links，下次会再试
        log.warning("pr_links.ctx_update_failed", req_id=req_id, error=str(e))

    # 第一次 discover 成功 → 顺手 backfill ctx 里已经记下来的 sisyphus issue
    backfill_ids = _gather_known_issue_ids(ctx)
    if backfill_ids:
        await _backfill_known_issues(project_id, backfill_ids, links)

    log.info(
        "pr_links.discovered",
        req_id=req_id, branch=branch,
        links=[link.to_dict() for link in links],
        backfilled=backfill_ids,
    )
    return links
