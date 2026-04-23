"""pr-ci-watch 自检（M2）：sisyphus 直接调 GitHub REST API 轮询 PR check-runs。

M15：repo / pr_number 用 gh api 实时查，不读 manifest。
repo 从环境变量 SISYPHUS_BUSINESS_REPO 拿，branch 从参数传入。
dev agent 只需 push branch + 创 PR，不用回写任何东西。

GH API:
- GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=open  → PR list（含 number + head.sha）
- GET /repos/{owner}/{repo}/commits/{sha}/check-runs                 → check_runs[]

退出码：
- 0   = 全绿（所有 check-run completed 且 conclusion 友好）
- 1   = 至少一个失败（completed + conclusion 红）
- 124 = 超时（到 timeout_sec 还有 check-run 没 completed）
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
) -> CheckResult:
    """轮询 PR check-runs → 全绿 / 任一失败 / 超时返 CheckResult。"""
    repo = os.getenv("SISYPHUS_BUSINESS_REPO")
    if not repo:
        raise ValueError("SISYPHUS_BUSINESS_REPO env var not set")

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    log.info("checker.pr_ci_watch.start", repo=repo, branch=branch,
             poll=poll_interval_sec, timeout=timeout_sec)

    start = time.monotonic()
    async with httpx.AsyncClient(base_url=_GH_API, headers=headers, timeout=30.0) as client:
        # 1. 用 REST API 查 PR number + head.sha（替代 gh CLI，避免同步阻塞事件循环）
        try:
            pr_number, sha = await _get_pr_info(client, repo, branch)
        except httpx.HTTPError as e:
            log.exception("checker.pr_ci_watch.pr_lookup_failed", repo=repo, branch=branch)
            return CheckResult(
                passed=False, exit_code=1,
                stdout_tail="", stderr_tail=f"PR lookup failed: {e}"[:_TAIL],
                duration_sec=time.monotonic() - start, cmd=f"watch-pr-ci {repo}@{branch}",
            )

        cmd_label = f"watch-pr-ci {repo}#{pr_number}@{sha[:8]}"
        deadline = start + timeout_sec
        last_runs: list[dict] = []

        while True:
            try:
                last_runs = await _get_check_runs(client, repo, sha)
            except httpx.HTTPError as e:
                log.warning("checker.pr_ci_watch.api_error", repo=repo, sha=sha[:8], error=str(e))
                if time.monotonic() >= deadline:
                    return CheckResult(
                        passed=False, exit_code=124,
                        stdout_tail="", stderr_tail=f"API error at deadline: {e}"[:_TAIL],
                        duration_sec=time.monotonic() - start, cmd=cmd_label,
                    )
                await asyncio.sleep(poll_interval_sec)
                continue

            verdict = _classify(last_runs)
            log.info("checker.pr_ci_watch.poll", repo=repo, sha=sha[:8],
                     verdict=verdict, run_count=len(last_runs))

            if verdict == "pass":
                return CheckResult(
                    passed=True, exit_code=0,
                    stdout_tail=_summarize(last_runs)[:_TAIL], stderr_tail="",
                    duration_sec=time.monotonic() - start, cmd=cmd_label,
                )
            if verdict == "fail":
                return CheckResult(
                    passed=False, exit_code=1,
                    stdout_tail=_summarize(last_runs, failed_only=True)[:_TAIL],
                    stderr_tail="",
                    duration_sec=time.monotonic() - start, cmd=cmd_label,
                )

            if time.monotonic() + poll_interval_sec >= deadline:
                return CheckResult(
                    passed=False, exit_code=124,
                    stdout_tail=_summarize(last_runs)[:_TAIL],
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
