"""pr-ci-watch 自检（M2 + M11）：sisyphus 直接调 GitHub REST API 轮询 PR check-runs，
不再起 BKD agent 让它跑 `gh pr checks` 然后报 tag。

M11：repo / pr_number 从 /workspace/.sisyphus/manifest.yaml 的 `pr` 段读。
admission 强制 `pr.repo` 必填；`pr.number` 由 dev / staging-test agent
开 PR 后回写 manifest — pr-ci-watch 跑到这里时必须已有 number，否则直接抛。

GH API:
- GET /repos/{owner}/{repo}/pulls/{pr_number}        → head.sha
- GET /repos/{owner}/{repo}/commits/{sha}/check-runs → check_runs[]

退出码：
- 0   = 全绿（所有 check-run completed 且 conclusion 友好）
- 1   = 至少一个失败（completed + conclusion 红）
- 124 = 超时（到 timeout_sec 还有 check-run 没 completed）
"""
from __future__ import annotations

import asyncio
import time

import httpx
import structlog

from ..config import settings
from . import manifest_io
from ._types import CheckResult

log = structlog.get_logger(__name__)

_GH_API = "https://api.github.com"
_TAIL = 2048

# check-run conclusion 分类
# https://docs.github.com/en/rest/checks/runs#about-check-runs
_PASS_CONCLUSIONS = {"success", "neutral", "skipped"}
_FAIL_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required", "stale"}


async def watch_pr_ci(
    req_id: str,
    poll_interval_sec: int = 30,
    timeout_sec: int = 1800,
) -> CheckResult:
    """读 manifest.pr → 轮询 check-runs → 全绿 / 任一失败 / 超时返 CheckResult。

    Raises:
        manifest_io.ManifestReadError: 读 manifest 失败或 pr 段缺字段
          （包括 pr.number 缺失 —— admission 不强制，但 pr-ci 阶段必须已由
          上游 agent 写入，缺了按 infra fail 走 retry）。
    """
    manifest = await manifest_io.read_manifest(req_id)

    pr = manifest.get("pr")
    if not isinstance(pr, dict):
        raise manifest_io.ManifestReadError("manifest 缺 pr 段（admission 应已挡）")

    repo = pr.get("repo")
    pr_number = pr.get("number")
    if not repo:
        raise manifest_io.ManifestReadError(f"manifest.pr.repo 缺失：{pr!r}")
    if not pr_number:
        raise manifest_io.ManifestReadError(
            "manifest.pr.number 缺失 —— 上游应在开 PR 后回写 manifest"
        )

    return await _watch_pr_ci_inner(
        repo=repo,
        pr_number=int(pr_number),
        poll_interval_sec=poll_interval_sec,
        timeout_sec=timeout_sec,
    )


async def _watch_pr_ci_inner(
    *,
    repo: str,
    pr_number: int,
    poll_interval_sec: int,
    timeout_sec: int,
) -> CheckResult:
    cmd_label = f"watch-pr-ci {repo}#{pr_number}"
    start = time.monotonic()

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"

    log.info("checker.pr_ci_watch.start", repo=repo, pr=pr_number,
             poll=poll_interval_sec, timeout=timeout_sec)

    async with httpx.AsyncClient(base_url=_GH_API, headers=headers, timeout=30.0) as client:
        try:
            sha = await _get_pr_head_sha(client, repo, pr_number)
        except httpx.HTTPError as e:
            log.exception("checker.pr_ci_watch.pr_lookup_failed", repo=repo, pr=pr_number)
            return CheckResult(
                passed=False, exit_code=1,
                stdout_tail="", stderr_tail=f"PR lookup failed: {e}"[:_TAIL],
                duration_sec=time.monotonic() - start, cmd=cmd_label,
            )

        cmd_label = f"watch-pr-ci {repo}#{pr_number}@{sha[:8]}"
        deadline = start + timeout_sec
        last_runs: list[dict] = []

        while True:
            try:
                last_runs = await _get_check_runs(client, repo, sha)
            except httpx.HTTPError as e:
                log.warning("checker.pr_ci_watch.api_error", repo=repo, sha=sha[:8], error=str(e))
                # 临时网络错误不立即失败，下一轮重试；超时再返 124
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

            # pending：再等一轮
            if time.monotonic() + poll_interval_sec >= deadline:
                return CheckResult(
                    passed=False, exit_code=124,
                    stdout_tail=_summarize(last_runs)[:_TAIL],
                    stderr_tail=f"timeout after {timeout_sec}s, still pending",
                    duration_sec=time.monotonic() - start, cmd=cmd_label,
                )
            await asyncio.sleep(poll_interval_sec)


# ── GH API helpers ───────────────────────────────────────────────────────

async def _get_pr_head_sha(client: httpx.AsyncClient, repo: str, pr_number: int) -> str:
    r = await client.get(f"/repos/{repo}/pulls/{pr_number}")
    r.raise_for_status()
    return r.json()["head"]["sha"]


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
