"""pr-ci-watch 自检（M2）：sisyphus 直接调 GitHub REST API 轮询 PR check-runs。

M15：repo / pr_number 用 gh api 实时查，不读 manifest。
REQ-clone-and-pr-ci-fallback-1777115925：repos 列表**只**来自 caller
（per-REQ involved_repos / runner discovery）。空 → ValueError，**不**fallback
到任何全局环境变量 —— stale env 在多 REQ / 多仓场景下注定指错仓，silent-pass
或 false-fail，跟 sisyphus "无信号即失败" 哲学冲突。

dev agent 只需 push branch + 创 PR，不用回写任何东西。

SHA 刷新（force-push 检测）：
- 每 tick 重新拉 head SHA；SHA 变化时重置 check-runs 缓存，从新 SHA 开始判
- 单 repo 最多允许 _MAX_SHA_FLIPS 次翻转，超限 → fail reason=too-many-sha-flips
- refetch 失败（HTTP / 找不到 PR）→ 警告 + retry，与 check-run API 错误一致

PR 状态变化：
- merged → pass（PR 被合并）
- closed without merge → fail reason=pr-closed-without-merge

多仓 REQ 行为：
- 任一 repo 的 PR check-runs 红 → 整体 fail
- 任一 repo 上找不到 open PR → fail（说明 dev agent 没 push 完）
- 所有 repo 全绿 → pass
- 还有 pending → 等

GH API:
- GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=open  → open PR（含 head.sha）
- GET /repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=closed → closed/merged PR
- GET /repos/{owner}/{repo}/commits/{sha}/check-runs                 → check_runs[]
- GET /repos/{owner}/{repo}/commits/{sha}/statuses                   → statuses[]
  （commit statuses，给 PAT-only ci 系统用——ttpos-ci 用 createCommitStatus 写
  `CI / lint`, `CI / unit-test`, `CI / integration-test`, `CI / sonarqube`,
  `CI / image-publish`，详见 commit ttpos-ci@db24441；statuses 跟 check-runs
  合并判绿，image-publish 的 description 字段是 image_tag，extras 透出供 accept
  注入 SISYPHUS_IMAGE_TAGS env。closes #474）

退出码：
- 0   = 全绿（所有 repo 的 check-run/status 都 completed 且 conclusion 友好）
- 1   = 至少一个失败
- 124 = 超时
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx
import structlog

from ..config import settings
from ._types import CheckResult

log = structlog.get_logger(__name__)

_GH_API = "https://api.github.com"
_TAIL = 2048
_MAX_SHA_FLIPS = 5

_PASS_CONCLUSIONS = {"success", "neutral", "skipped"}
_FAIL_CONCLUSIONS = {"failure", "cancelled", "timed_out", "action_required", "stale"}

# sisyphus 契约：业务 PR 必须由 GitHub Actions 跑 lint/unit/integration。
# check-run 的 app.slug == "github-actions" 表示该 check 由 GHA workflow 产生；
# 别的 slug（如 "anthropic-claude" / 第三方 review bot）属于 review-only signal。
# 用于检测假阳性 pass：全绿但全是 review-only check-run（GHA 一次没跑）。
_GHA_APP_SLUG = "github-actions"

# ttpos-ci/.github/workflows/ci-go.yml 的 image-publish job 用 createCommitStatus
# 写入业务仓 PR 的 commit status；description 字段直接放 image_tag 字符串
# （`ghcr.io/<repo>:<REQ-id>-sha-<8>`）。pr_ci_watch 全绿时拿这个值塞进
# extras['image_tags'] 给 create_accept 写 ctx → SISYPHUS_IMAGE_TAGS env。
# 见 ttpos-ci@db24441 + docs/integration-contracts.md §11。
_IMAGE_PUBLISH_STATUS_CONTEXT = "CI / image-publish"


@dataclass
class _RepoState:
    repo: str
    pr_number: int
    sha: str
    flip_count: int = 0
    terminal_verdict: str | None = None  # "pass" | "fail" once decided
    terminal_reason: str | None = None


async def watch_pr_ci(
    req_id: str,
    branch: str,
    poll_interval_sec: int = 30,
    timeout_sec: int = 1800,
    repos: list[str] | None = None,
) -> CheckResult:
    """轮询所有 repos 的 PR check-runs → 全绿 / 任一失败 / 超时返 CheckResult。

    每 tick 重新拉 head SHA：force-push 自动切新 SHA；超过 _MAX_SHA_FLIPS 次翻转 → fail。
    merged → pass；closed without merge → fail；refetch 失败 → retry 到 deadline。
    repos 必须由 caller 显式传入（per-REQ involved_repos）；空 / None 直接
    ValueError —— 没 fallback 到任何 process-global env，跟 sisyphus "无信号
    即失败" 哲学一致。
    """
    repo_list = [r for r in (repos or []) if r]
    if not repo_list:
        raise ValueError(
            "no repos provided to watch_pr_ci (caller must pass per-REQ "
            "involved_repos; no global env fallback)"
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
        # Initial PR fetch: fail fast if no PR exists for any repo
        states: list[_RepoState] = []
        for repo in repo_list:
            try:
                pr_number, sha, pr_state = await _get_pr_info(client, repo, branch)
                state = _RepoState(repo=repo, pr_number=pr_number, sha=sha)
                if pr_state == "merged":
                    state.terminal_verdict = "pass"
                    log.info("checker.pr_ci_watch.pr_merged", repo=repo, pr=pr_number)
                elif pr_state == "closed":
                    state.terminal_verdict = "fail"
                    state.terminal_reason = "pr-closed-without-merge"
                    log.info("checker.pr_ci_watch.pr_closed", repo=repo, pr=pr_number)
                states.append(state)
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
                return CheckResult(
                    passed=False, exit_code=1,
                    stdout_tail="", stderr_tail=str(e)[:_TAIL],
                    duration_sec=time.monotonic() - start,
                    cmd=f"watch-pr-ci {repo}@{branch}",
                )

        def _cmd_label() -> str:
            return "watch-pr-ci " + " ".join(f"{s.repo}#{s.pr_number}@{s.sha[:8]}" for s in states)

        deadline = start + timeout_sec
        per_repo_runs: dict[str, list[dict]] = {}

        while True:
            # Re-fetch PR info each tick to detect force-pushes and PR state changes
            for state in states:
                if state.terminal_verdict is not None:
                    continue
                try:
                    _, new_sha, pr_state = await _get_pr_info(client, state.repo, branch)
                    if pr_state == "merged":
                        log.info("checker.pr_ci_watch.pr_merged",
                                 repo=state.repo, pr=state.pr_number)
                        state.terminal_verdict = "pass"
                    elif pr_state == "closed":
                        log.info("checker.pr_ci_watch.pr_closed",
                                 repo=state.repo, pr=state.pr_number)
                        state.terminal_verdict = "fail"
                        state.terminal_reason = "pr-closed-without-merge"
                    elif new_sha != state.sha:
                        log.info("checker.pr_ci_watch.sha_flip",
                                 repo=state.repo, old=state.sha[:8], new=new_sha[:8],
                                 flip_count=state.flip_count + 1)
                        state.flip_count += 1
                        state.sha = new_sha
                        per_repo_runs.pop(state.repo, None)  # clear stale runs
                        if state.flip_count > _MAX_SHA_FLIPS:
                            state.terminal_verdict = "fail"
                            state.terminal_reason = "too-many-sha-flips"
                            log.warning("checker.pr_ci_watch.too_many_sha_flips",
                                        repo=state.repo, flips=state.flip_count)
                except (httpx.HTTPError, ValueError) as e:
                    # Consistent with check-run API errors: retry until deadline
                    log.warning("checker.pr_ci_watch.pr_refetch_error",
                                repo=state.repo, error=str(e))

            # Early exit: any terminal fail (too-many-sha-flips / pr-closed)
            if any(s.terminal_verdict == "fail" for s in states):
                parts = [
                    f"{s.repo}: {s.terminal_reason}"
                    for s in states if s.terminal_verdict == "fail"
                ]
                return CheckResult(
                    passed=False, exit_code=1,
                    stdout_tail=" | ".join(parts)[:_TAIL],
                    stderr_tail="", duration_sec=time.monotonic() - start, cmd=_cmd_label(),
                )

            # Early exit: all repos merged (all terminal pass)
            if all(s.terminal_verdict == "pass" for s in states):
                parts = [f"{s.repo}: merged" for s in states]
                return CheckResult(
                    passed=True, exit_code=0,
                    stdout_tail=" | ".join(parts)[:_TAIL],
                    stderr_tail="", duration_sec=time.monotonic() - start, cmd=_cmd_label(),
                )

            # Fetch check-runs + commit statuses for non-terminal repos.
            # 两路信号合并：check-runs（GHA workflow 自己写的）+ statuses
            # （PAT-only 系统如 ttpos-ci 写的）—— ttpos-ci 的
            # `CI / lint`, `CI / unit-test`, `CI / integration-test`,
            # `CI / sonarqube`, `CI / image-publish` 都在 statuses 一侧，必须
            # 一并轮才不会过早判绿（closes #474 隐藏 bug：以前只看
            # check-runs，"Dispatch CI" 一发出 dispatch event 就 success，
            # ttpos-ci 还没跑完就 PR_CI_PASS）。
            api_error = None
            for state in states:
                if state.terminal_verdict is not None:
                    continue
                try:
                    runs = await _get_check_runs(client, state.repo, state.sha)
                except httpx.HTTPError as e:
                    api_error = (state.repo, e)
                    log.warning("checker.pr_ci_watch.api_error",
                                repo=state.repo, sha=state.sha[:8], error=str(e))
                    continue
                # statuses 是辅路：仓没接 statuses-based CI（如 ttpos-ci）时
                # API 也合法地返空数组；偶发 HTTP 抖动不应阻塞 check-runs 主路。
                # → 失败静默降级为空 list，verdict 仍以 check-runs 为准。
                statuses: list[dict] = []
                try:
                    statuses = await _get_commit_statuses(client, state.repo, state.sha)
                except httpx.HTTPError as e:
                    log.warning("checker.pr_ci_watch.statuses_api_error",
                                repo=state.repo, sha=state.sha[:8], error=str(e))
                per_repo_runs[state.repo] = runs + _statuses_to_runs(statuses)

            if api_error and time.monotonic() >= deadline:
                repo, e = api_error
                return CheckResult(
                    passed=False, exit_code=124,
                    stdout_tail="",
                    stderr_tail=f"API error at deadline for {repo}: {e}"[:_TAIL],
                    duration_sec=time.monotonic() - start, cmd=_cmd_label(),
                )
            if api_error:
                await asyncio.sleep(poll_interval_sec)
                continue

            # Compute verdicts: terminal overrides check-run classification
            verdicts: dict[str, str] = {}
            for state in states:
                if state.terminal_verdict is not None:
                    verdicts[state.repo] = state.terminal_verdict
                else:
                    verdicts[state.repo] = _classify(per_repo_runs.get(state.repo, []))

            log.info("checker.pr_ci_watch.poll",
                     verdicts=verdicts,
                     run_counts={s.repo: len(per_repo_runs.get(s.repo, [])) for s in states},
                     sha_flips={s.repo: s.flip_count for s in states if s.flip_count > 0})

            if any(v in ("fail", "no-gha") for v in verdicts.values()):
                parts = []
                for state in states:
                    v = verdicts[state.repo]
                    if v not in ("fail", "no-gha"):
                        continue
                    if state.terminal_verdict == "fail":
                        parts.append(f"{state.repo}: {state.terminal_reason}")
                    elif v == "no-gha":
                        # 全绿但 0 条 GHA check-run —— 列出实际跑了啥（露出 review-only bot
                        # 的真身），给 verifier / 人工审一眼能看出"GHA 没跑"。
                        runs = per_repo_runs.get(state.repo, [])
                        parts.append(
                            f"{state.repo}: no-gha-checks-ran "
                            f"(only non-CI signals: {_summarize(runs)})"
                        )
                    else:
                        runs = per_repo_runs.get(state.repo, [])
                        parts.append(f"{state.repo}: {_summarize(runs, failed_only=True)}")
                return CheckResult(
                    passed=False, exit_code=1,
                    stdout_tail=" | ".join(parts)[:_TAIL],
                    stderr_tail="", duration_sec=time.monotonic() - start, cmd=_cmd_label(),
                )

            if all(v == "pass" for v in verdicts.values()):
                parts = [
                    f"{state.repo}: merged" if state.terminal_verdict == "pass"
                    else f"{state.repo}: {_summarize(per_repo_runs.get(state.repo, []))}"
                    for state in states
                ]
                # 抽 ttpos-ci image-publish 的 image_tag。pass 时 PR-CI 全绿，每个非
                # merged 的 repo 应该都有 success 的 `CI / image-publish` status；
                # 没有 → log warning 但不阻塞 pass（业务仓还没接 image-publish job 的
                # 兼容期）。merged repo 走的是 PR 已合并的早期 terminal 路径，没轮
                # statuses，跳过。
                image_tags: dict[str, str] = {}
                for state in states:
                    if state.terminal_verdict == "pass":
                        continue  # merged，没 statuses 可读
                    tag = _extract_image_tag(per_repo_runs.get(state.repo, []))
                    if tag:
                        image_tags[state.repo] = tag
                    else:
                        log.warning(
                            "checker.pr_ci_watch.image_tag_missing",
                            repo=state.repo, sha=state.sha[:8],
                            note="PR all-green but `CI / image-publish` status absent or empty description "
                                 "— accept stage will fall back to chart default tag",
                        )
                extras = {"image_tags": image_tags} if image_tags else None
                return CheckResult(
                    passed=True, exit_code=0,
                    stdout_tail=" | ".join(parts)[:_TAIL],
                    stderr_tail="", duration_sec=time.monotonic() - start, cmd=_cmd_label(),
                    extras=extras,
                )

            if time.monotonic() + poll_interval_sec >= deadline:
                summary_parts = [
                    f"{state.repo}: {_summarize(per_repo_runs.get(state.repo, []))}"
                    for state in states
                ]
                return CheckResult(
                    passed=False, exit_code=124,
                    stdout_tail=" | ".join(summary_parts)[:_TAIL],
                    stderr_tail=f"timeout after {timeout_sec}s, still pending",
                    duration_sec=time.monotonic() - start, cmd=_cmd_label(),
                )
            await asyncio.sleep(poll_interval_sec)


# ── GH API helpers ───────────────────────────────────────────────────────

async def _get_pr_info(client: httpx.AsyncClient, repo: str, branch: str) -> tuple[int, str, str]:
    """查 branch 对应的 PR，返 (pr_number, head_sha, state)。

    state: "open" | "merged" | "closed"（closed without merge）。
    先查 open PR（最常见）；没找到再查 closed 判断是合并还是直接关闭。
    两种情况都找不到 → ValueError。
    """
    owner, _ = repo.split("/", 1)
    r = await client.get(
        f"/repos/{repo}/pulls",
        params={"head": f"{owner}:{branch}", "state": "open"},
    )
    r.raise_for_status()
    open_pulls = r.json()
    if open_pulls:
        pr = open_pulls[0]
        return int(pr["number"]), str(pr["head"]["sha"]), "open"

    # No open PR – check if merged or closed without merge
    r = await client.get(
        f"/repos/{repo}/pulls",
        params={"head": f"{owner}:{branch}", "state": "closed"},
    )
    r.raise_for_status()
    closed_pulls = r.json()
    if not closed_pulls:
        raise ValueError(f"No PR found for branch {branch} in {repo}")
    pr = closed_pulls[0]
    pr_state = "merged" if pr.get("merged_at") is not None else "closed"
    return int(pr["number"]), str(pr["head"]["sha"]), pr_state


async def _get_check_runs(client: httpx.AsyncClient, repo: str, sha: str) -> list[dict]:
    r = await client.get(f"/repos/{repo}/commits/{sha}/check-runs", params={"per_page": 100})
    r.raise_for_status()
    return r.json().get("check_runs", [])


async def _get_commit_statuses(client: httpx.AsyncClient, repo: str, sha: str) -> list[dict]:
    """GET /repos/{repo}/commits/{sha}/statuses。

    GitHub commit-statuses API（legacy）跟 check-runs 是两套独立信号；ttpos-ci
    用 PAT 写 statuses（PAT 不能写 check-runs），所以 sisyphus 必须两路都拉
    才看得到 ttpos-ci 的 lint / unit / integration / sonarqube / image-publish
    五个 status。
    """
    r = await client.get(f"/repos/{repo}/commits/{sha}/statuses", params={"per_page": 100})
    r.raise_for_status()
    return r.json() if isinstance(r.json(), list) else []


def _statuses_to_runs(statuses: list[dict]) -> list[dict]:
    """commit status → check-run shape，让 _classify 一并处理。

    每个 status 视作一条 GHA check-run：
      state=success  → status=completed, conclusion=success
      state=failure / error → status=completed, conclusion=failure
      state=pending  → status=in_progress, conclusion=None
    `app.slug` 强制设 github-actions —— ttpos-ci 走 PAT 写的 statuses 在语义上
    就是 PR-CI 信号，不算 review-only bot；no-gha 假阳性判定不应误伤。
    `_status_description` 是私字段，留给 _extract_image_tag 提 image-publish 的 image_tag。
    """
    out: list[dict] = []
    for s in statuses or []:
        state = s.get("state", "pending")
        if state in ("failure", "error"):
            status_field = "completed"
            conclusion = "failure"
        elif state == "success":
            status_field = "completed"
            conclusion = "success"
        else:  # pending / unknown
            status_field = "in_progress"
            conclusion = None
        out.append({
            "name": s.get("context", "?"),
            "status": status_field,
            "conclusion": conclusion,
            "app": {"slug": _GHA_APP_SLUG},
            "_status_description": s.get("description"),
        })
    return out


def _extract_image_tag(runs: list[dict]) -> str | None:
    """从 runs（含 status_to_runs 注入的条目）里抓 CI / image-publish 的 description。

    description 是 ttpos-ci/.github/workflows/ci-go.yml image-publish job 写
    回去的 image_tag 字符串。只在 status=completed && conclusion=success
    时认；否则跳过。
    """
    for r in runs:
        if r.get("name") != _IMAGE_PUBLISH_STATUS_CONTEXT:
            continue
        if r.get("status") != "completed":
            continue
        if r.get("conclusion") != "success":
            continue
        desc = r.get("_status_description")
        if isinstance(desc, str) and desc.strip():
            return desc.strip()
    return None


# ── verdict 计算 ─────────────────────────────────────────────────────────

def _classify(runs: list[dict]) -> str:
    """返 'pass' / 'fail' / 'pending' / 'no-gha'。

    - 任一 completed 且 conclusion 红 → fail（fail 优先：早死早超生）
    - 任一未 completed → pending
    - 全 completed 且 conclusion 全绿 + 至少一条 GHA check-run → pass
    - 全 completed 且 conclusion 全绿 但 0 条 GHA check-run → no-gha（假阳性 pass）
    - 空 → pending（PR 刚开 GHA 没触发，先等）

    no-gha 触发场景：PR 目标分支不在源仓 ci.yml 触发列表 / workflow 被禁 /
    GHA webhook miss 整套，导致 lint/unit/integration 一次都没跑；只剩
    claude-review 这种 review-only bot 报绿。verifier-agent 之前是兜底人肉 catch
    （REQ-acceptance-e2e-1777084500），机械层应自己识别。
    """
    if not runs:
        return "pending"

    has_fail = False
    has_pending = False
    has_gha = False
    for r in runs:
        if (r.get("app") or {}).get("slug") == _GHA_APP_SLUG:
            has_gha = True
        if r.get("status") != "completed":
            has_pending = True
            continue
        if r.get("conclusion") in _FAIL_CONCLUSIONS:
            has_fail = True

    if has_fail:
        return "fail"
    if has_pending:
        # 还有 pending 就继续等 —— GHA workflow 可能刚要起；不当下断 no-gha
        return "pending"
    if not has_gha:
        return "no-gha"
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
