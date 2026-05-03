"""PR queue health cron — 检测 OPEN PR base drift，写入 pr_drift_log。

每 pr_health_interval_sec（默认 1800s = 30min）扫一次配置的 repos：
- 列出所有 OPEN PR（GitHub REST API，自动分页）
- compare/{base.ref}...{head.sha} 算 behind_count
- 用 PR.mergeable / mergeable_state 预测冲突
- behind_count > pr_health_behind_threshold 才写 pr_drift_log

drift_kind 分类：
  'semantic-drift'  = behind > threshold 且 conflict_predicted = True
  'pure-lint-drift' = behind > threshold 且无冲突（main 修了 lint 等，PR 只需 rebase）

不做飞书 alert / 跨 PR 传染检测 / 业务仓扩展（留 followup issue）。
"""
from __future__ import annotations

import asyncio

import httpx
import structlog

from .config import settings
from .store import db

log = structlog.get_logger(__name__)

_GH_API = "https://api.github.com"


async def _gh_get(client: httpx.AsyncClient, path: str) -> dict | list:
    r = await client.get(f"{_GH_API}{path}")
    r.raise_for_status()
    return r.json()


async def _list_open_prs(client: httpx.AsyncClient, repo: str) -> list[dict]:
    """列出 repo 全部 OPEN PR，自动分页（最多 10 页 / 1000 条）。"""
    prs: list[dict] = []
    page = 1
    while page <= 10:
        batch: list = await _gh_get(  # type: ignore[assignment]
            client, f"/repos/{repo}/pulls?state=open&per_page=100&page={page}"
        )
        if not batch:
            break
        prs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return prs


async def _behind_count(
    client: httpx.AsyncClient, repo: str, head_sha: str, base_ref: str
) -> int:
    """用 GitHub compare API 算 PR head 落后 base branch 的 commit 数。

    compare/{base_ref}...{head_sha} 的 behind_by 即 main 比 PR head 多的 commit 数。
    """
    data: dict = await _gh_get(  # type: ignore[assignment]
        client, f"/repos/{repo}/compare/{base_ref}...{head_sha}"
    )
    return int(data.get("behind_by", 0))


def _predict_conflict(pr: dict) -> bool:
    """根据 PR.mergeable / mergeable_state 预测是否存在合并冲突。

    GitHub 异步计算 mergeable，null 表示还没算完，保守返 False（宁漏不误报）。
    """
    if pr.get("mergeable") is False:
        return True
    if pr.get("mergeable_state") == "dirty":
        return True
    return False


async def check_pr_drift_once(repos: list[str]) -> dict:
    """单次扫描：遍历所有 repo 的 OPEN PR，把 drift 写入 pr_drift_log。"""
    if not settings.github_token:
        log.warning("pr_health.skip", reason="github_token not configured")
        return {"skipped": "no_github_token"}

    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    threshold = settings.pr_health_behind_threshold
    pool = db.get_pool()
    total_inserted = 0

    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        for repo in repos:
            try:
                prs = await _list_open_prs(client, repo)
                log.debug("pr_health.repo_scanned", repo=repo, pr_count=len(prs))

                for pr in prs:
                    pr_number: int = pr["number"]
                    head_sha: str = pr["head"]["sha"]
                    base_ref: str = pr["base"]["ref"]
                    base_sha: str = pr["base"]["sha"]

                    try:
                        behind = await _behind_count(client, repo, head_sha, base_ref)
                    except Exception as e:
                        log.warning(
                            "pr_health.compare_failed",
                            repo=repo, pr=pr_number, error=str(e),
                        )
                        continue

                    if behind <= threshold:
                        continue

                    has_conflict = _predict_conflict(pr)
                    drift_kind = "semantic-drift" if has_conflict else "pure-lint-drift"

                    await pool.execute(
                        """
                        INSERT INTO pr_drift_log
                            (pr_number, repo, base_sha, behind_count,
                             conflict_predicted, drift_kind)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        """,
                        pr_number, repo, base_sha, behind, has_conflict, drift_kind,
                    )
                    total_inserted += 1
                    log.info(
                        "pr_health.drift_detected",
                        repo=repo, pr=pr_number, behind=behind,
                        drift_kind=drift_kind, conflict=has_conflict,
                    )

            except Exception as e:
                log.exception("pr_health.repo_failed", repo=repo, error=str(e))

    return {"inserted": total_inserted, "repos_scanned": repos}


async def run_loop() -> None:
    """orchestrator 启动起的后台任务，周期性扫 PR drift。"""
    interval = settings.pr_health_interval_sec
    repos = settings.pr_health_repos
    log.info("pr_health.loop.started", interval_sec=interval, repos=repos)
    while True:
        try:
            result = await check_pr_drift_once(repos)
            log.debug("pr_health.tick", result=result)
        except asyncio.CancelledError:
            log.info("pr_health.loop.stopped")
            raise
        except Exception as e:
            log.exception("pr_health.loop.error", error=str(e))
        await asyncio.sleep(interval)
