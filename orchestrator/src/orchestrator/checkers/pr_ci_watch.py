"""pr-ci-watch 自检（M2）：sisyphus 直接调 GitHub REST API 轮询 PR check-runs。

M15：repo / pr_number 用 gh api 实时查，不读 manifest。
repos 列表优先来自 caller（per-REQ involved_repos），fallback 到全局环境变量
SISYPHUS_BUSINESS_REPO（兼容旧单仓 REQ）。

dev agent 只需 push branch + 创 PR，不用回写任何东西。

多仓 REQ 行为：
- 任一 repo 的 PR check-runs 红 → 整体 fail
- 任一 repo 上找不到 open PR → fail（说明 dev agent 没 push 完）
- 所有 repo 全绿 → pass
- 还有 pending → 等

GH API:
- GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=open  → PR list（含 number + head.sha）
- GET /repos/{owner}/{repo}/commits/{sha}/check-runs                 → check_runs[]

退出码：
- 0   = 全绿（所有 repo 的 check-run completed 且 conclusion 友好）
- 1   = 至少一个失败
- 124 = 超时
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx
import structlog

from ..config import settings
from ._types import CheckResult

log = structlog.get_logger(__name__)

_GH_API = "https://api.github.com"
_TAIL = 2048

_PASS_CONCLUSIONS = {"success", "neutral", "skipped"}
_FAIL_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required", "stale"}


async def watch_pr_ci(
    req_id: str,
    branch: str,
    poll_interval_sec: int = 30,
    timeout_sec: int = 1800,
    repos: list[str] | None = None,
) -> CheckResult:
    """轮询所有 repos 的 PR check-runs → 全绿 / 任一失败 / 超时返 CheckResult。

    repos 优先于 SISYPHUS_BUSINESS_REPO 环境变量（per-REQ involved_repos 优先于全局）。
    """
    repo_list = repos or ([os.getenv("SISYPHUS_BUSINESS_REPO")] if os.getenv("SISYPHUS_BUSINESS_REPO") else [])
    repo_list = [r for r in repo_list if r]
    if not repo_list:
        raise ValueError(
            "no repos provided to watch_pr_ci (caller didn't pass repos and "
            "SISYPHUS_BUSINESS_REPO env var not set)"
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    log.info("checker.pr_ci_watch.start", repos=repo_list, branch=branch,
             poll=poll_interval_sec, timeout=timeout_sec)

    start = time.monotonic()
    async with httpx.AsyncClient(base_url=_GH_API, headers=headers, timeout=30.0) as client:
        # 1. 查每个 repo 的 PR number + head.sha
        pr_infos: list[tuple[str, int, str]] = []  # [(repo, pr_number, sha)]
        for repo in repo_list:
            try:
                pr_number, sha = await _get_pr_info(client, repo, branch)
                pr_infos.append((repo, pr_number, sha))
            except httpx.HTTPError as e:
                log.exception("checker.pr_ci_watch.pr_lookup_failed",
                              repo=repo, branch=branch)
                return CheckResult(
                    passed=False, exit_code=1,
                    stdout_tail="", stderr_tail=f"PR lookup failed for {repo}: {e}"[:_TAIL],
                    duration_sec=time.monotonic() - start,
                    cmd=f"watch-pr-ci {repo}@{branch}",
                )
            except ValueError as e:
                # No open PR found
                return CheckResult(
                    passed=False, exit_code=1,
                    stdout_tail="", stderr_tail=str(e)[:_TAIL],
                    duration_sec=time.monotonic() - start,
                    cmd=f"watch-pr-ci {repo}@{branch}",
                )

        cmd_label = "watch-pr-ci " + " ".join(f"{r}#{n}@{s[:8]}" for r, n, s in pr_infos)
        deadline = start + timeout_sec
        per_repo_runs: dict[str, list[dict]] = {}

        while True:
            api_error = None
            for repo, _, sha in pr_infos:
                try:
                    per_repo_runs[repo] = await _get_check_runs(client, repo, sha)
                except httpx.HTTPError as e:
                    api_error = (repo, e)
                    log.warning("checker.pr_ci_watch.api_error",
                                repo=repo, sha=sha[:8], error=str(e))

            if api_error and time.monotonic() >= deadline:
                repo, e = api_error
                return CheckResult(
                    passed=False, exit_code=124,
                    stdout_tail="",
                    stderr_tail=f"API error at deadline for {repo}: {e}"[:_TAIL],
                    duration_sec=time.monotonic() - start, cmd=cmd_label,
                )
            if api_error:
                await asyncio.sleep(poll_interval_sec)
                continue

            # 聚合判 verdict：任一 fail → fail；全部 pass → pass；否则 pending
            verdicts = {repo: _classify(runs) for repo, runs in per_repo_runs.items()}
            log.info("checker.pr_ci_watch.poll",
                     verdicts=verdicts,
                     run_counts={r: len(rs) for r, rs in per_repo_runs.items()})

            if any(v == "fail" for v in verdicts.values()):
                summary_parts = [
                    f"{repo}: {_summarize(runs, failed_only=True)}"
                    for repo, runs in per_repo_runs.items()
                    if verdicts[repo] == "fail"
                ]
                return CheckResult(
                    passed=False, exit_code=1,
                    stdout_tail=" | ".join(summary_parts)[:_TAIL],
                    stderr_tail="", duration_sec=time.monotonic() - start, cmd=cmd_label,
                )
            if all(v == "pass" for v in verdicts.values()):
                summary_parts = [
                    f"{repo}: {_summarize(runs)}"
                    for repo, runs in per_repo_runs.items()
                ]
                return CheckResult(
                    passed=True, exit_code=0,
                    stdout_tail=" | ".join(summary_parts)[:_TAIL],
                    stderr_tail="", duration_sec=time.monotonic() - start, cmd=cmd_label,
                )

            if time.monotonic() + poll_interval_sec >= deadline:
                summary_parts = [
                    f"{repo}: {_summarize(runs)}"
                    for repo, runs in per_repo_runs.items()
                ]
                return CheckResult(
                    passed=False, exit_code=124,
                    stdout_tail=" | ".join(summary_parts)[:_TAIL],
                    stderr_tail=f"timeout after {timeout_sec}s, still pending",
                    duration_sec=time.monotonic() - start, cmd=cmd_label,
                )
            await asyncio.sleep(poll_interval_sec)


# ── GH API helpers ───────────────────────────────────────────────────────

async def _get_pr_info(client: httpx.AsyncClient, repo: str, branch: str) -> tuple[int, str]:
    """查 branch 对应的 open PR，返 (pr_number, head_sha)。

    用 GitHub REST API `head` 过滤器（替代旧 gh CLI 调用），全程 async 不阻塞事件循环。
    """
    owner, _ = repo.split("/", 1)
    r = await client.get(
        f"/repos/{repo}/pulls",
        params={"head": f"{owner}:{branch}", "state": "open"},
    )
    r.raise_for_status()
    pulls = r.json()
    if not pulls:
        raise ValueError(f"No open PR found for branch {branch} in {repo}")
    pr = pulls[0]
    return int(pr["number"]), str(pr["head"]["sha"])


async def _get_check_runs(client: httpx.AsyncClient, repo: str, sha: str) -> list[dict]:
    r = await client.get(f"/repos/{repo}/commits/{sha}/check-runs", params={"per_page": 100})
    r.raise_for_status()
    return r.json().get("check_runs", [])


# ── verdict 计算 ─────────────────────────────────────────────────────────

def _classify(runs: list[dict]) -> str:
    """返 'pass' / 'fail' / 'pending'。

    - 任一 completed 且 conclusion 红 → fail（fail 优先：早死早超生）
    - 任一未 completed → pending
    - 全 completed 且 conclusion 全绿 → pass
    - 空 → pending（PR 刚开 GHA 没触发，先等）
    """
    if not runs:
        return "pending"

    has_fail = False
    has_pending = False
    for r in runs:
        if r.get("status") != "completed":
            has_pending = True
            continue
        if r.get("conclusion") in _FAIL_CONCLUSIONS:
            has_fail = True

    if has_fail:
        return "fail"
    if has_pending:
        return "pending"
    return "pass"


def _summarize(runs: list[dict], failed_only: bool = False) -> str:
    """渲染 check-run 列表为 'name=conclusion' 一行串，给 stdout_tail。"""
    parts = []
    for r in runs:
        name = r.get("name", "?")
        status = r.get("status", "?")
        conclusion = r.get("conclusion") or status
        if failed_only and conclusion not in _FAIL_CONCLUSIONS:
            continue
        parts.append(f"{name}={conclusion}")
    return " ".join(parts)
