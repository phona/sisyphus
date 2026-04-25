"""checkers/pr_ci_watch.py 单测：mock GitHub API，验全绿/任一失败/全失败/超时/SHA翻转/PR合并关闭。

M15：watch_pr_ci(req_id, branch, ...)，repo 从 SISYPHUS_BUSINESS_REPO env 读，
pr_number + head.sha 用 GitHub REST API `head` 过滤器按 branch 查（这里 mock 掉），不再读 manifest。

SHA refresh（force-push 检测）：每 tick 重新拉 head SHA，SHA 变化时重置 check-runs 缓存。
"""
from __future__ import annotations

import httpx
import pytest

from orchestrator.checkers import pr_ci_watch
from orchestrator.checkers._types import CheckResult


def _pr_response(sha: str = "deadbeef" * 5):
    return {"head": {"sha": sha}, "number": 42}


def _runs_payload(*runs: dict) -> dict:
    return {"total_count": len(runs), "check_runs": list(runs)}


def _run(name: str, status: str = "completed", conclusion: str | None = "success") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


def patch_pr_lookup(monkeypatch, *, repo: str = "phona/ubox-crosser", pr_number: int | None = 42):
    """SISYPHUS_BUSINESS_REPO env + mock _get_pr_info 返指定 (number, sha, state)。"""
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", repo)

    async def fake_lookup(client, _repo: str, _branch: str) -> tuple[int, str, str]:
        if pr_number is None:
            raise ValueError("No open PR found")
        return pr_number, "deadbeef" * 5, "open"

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)


# ── 单轮直绿 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_all_green(httpx_mock, monkeypatch):
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_pr_lookup(monkeypatch)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(_run("lint"), _run("unit"), _run("integration")),
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0
    assert "lint=success" in result.stdout_tail
    assert "integration=success" in result.stdout_tail
    assert result.cmd.startswith("watch-pr-ci phona/ubox-crosser#42@deadbeef")


# ── 单轮失败 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_any_failed(httpx_mock, monkeypatch):
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_pr_lookup(monkeypatch)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(
            _run("lint"),
            _run("unit", conclusion="failure"),
            _run("integration"),
        ),
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert result.passed is False
    assert result.exit_code == 1
    # failed_only 模式：只列失败的
    assert "unit=failure" in result.stdout_tail
    assert "lint=success" not in result.stdout_tail


# ── 全失败 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_all_failed(httpx_mock, monkeypatch):
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_pr_lookup(monkeypatch)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(
            _run("lint", conclusion="failure"),
            _run("unit", conclusion="cancelled"),
        ),
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert result.passed is False
    assert result.exit_code == 1
    assert "lint=failure" in result.stdout_tail
    assert "unit=cancelled" in result.stdout_tail


# ── pending → 超时 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_timeout(httpx_mock, monkeypatch):
    """所有 check-run 都还 in_progress，到 timeout 返 124。"""
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_pr_lookup(monkeypatch)

    # 让 sleep 立即返回，避免真等
    async def fast_sleep(_):
        return None
    monkeypatch.setattr(pr_ci_watch.asyncio, "sleep", fast_sleep)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(_run("integration", status="in_progress", conclusion=None)),
        is_reusable=True,
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9", poll_interval_sec=0, timeout_sec=0)

    assert result.passed is False
    assert result.exit_code == 124
    assert "timeout" in result.stderr_tail.lower()
    assert "integration=in_progress" in result.stdout_tail


# ── pending → 中途变绿 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_pending_then_pass(httpx_mock, monkeypatch):
    """前一轮 pending，后一轮全绿。"""
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_pr_lookup(monkeypatch)

    async def fast_sleep(_):
        return None
    monkeypatch.setattr(pr_ci_watch.asyncio, "sleep", fast_sleep)

    # 第一次：pending
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(_run("lint", status="in_progress", conclusion=None)),
    )
    # 第二次：成功
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(_run("lint")),
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert result.passed is True
    assert result.exit_code == 0


# ── PR lookup HTTP 错误 → fail ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_pr_lookup_http_error(monkeypatch):
    """_get_pr_info 抛 httpx.HTTPError → watch_pr_ci 捕获后返 exit_code=1。"""
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/ubox-crosser")

    async def fake_lookup_fail(client, _repo: str, _branch: str) -> tuple[int, str, str]:
        raise httpx.HTTPError("mocked API error")
    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup_fail)

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert result.passed is False
    assert result.exit_code == 1
    assert "PR lookup failed" in result.stderr_tail


# ── 空 check-runs → pending → 超时 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_empty_runs_times_out(httpx_mock, monkeypatch):
    """PR 刚开 GHA 还没触发，check-runs 为空 → pending → 超时。"""
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_pr_lookup(monkeypatch)

    async def fast_sleep(_):
        return None
    monkeypatch.setattr(pr_ci_watch.asyncio, "sleep", fast_sleep)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(),
        is_reusable=True,
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9", poll_interval_sec=0, timeout_sec=0)
    assert result.exit_code == 124


# ── env / branch 不全 → 抛 ValueError ────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_raises_when_no_repos(monkeypatch):
    """没传 repos 参数 + SISYPHUS_BUSINESS_REPO 没设 → 直接 ValueError。"""
    monkeypatch.delenv("SISYPHUS_BUSINESS_REPO", raising=False)
    with pytest.raises(ValueError, match="no repos provided"):
        await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9")


@pytest.mark.asyncio
async def test_watch_pr_ci_returns_fail_when_no_pr(monkeypatch):
    """找不到对应 PR → 返 fail CheckResult（exit=1），不再抛 ValueError 让 caller 处理。"""
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/ubox-crosser")

    async def fake_lookup_none(client, _repo: str, _branch: str) -> tuple[int, str, str]:
        raise ValueError("No open PR found for branch")
    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup_none)

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9")
    assert result.passed is False
    assert result.exit_code == 1
    assert "No open PR found" in result.stderr_tail


@pytest.mark.asyncio
async def test_watch_pr_ci_per_req_repos_override_env(monkeypatch):
    """传入 repos 参数应覆盖 SISYPHUS_BUSINESS_REPO env var（per-REQ 覆盖全局）。"""
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/wrong-repo")

    looked_up: list[str] = []

    async def fake_lookup(client, repo: str, _branch: str) -> tuple[int, str, str]:
        looked_up.append(repo)
        return (101, "abc1234567890def", "open")

    async def fake_check_runs(client, _repo: str, _sha: str):
        return [_run("CI", conclusion="success")]

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)
    monkeypatch.setattr(pr_ci_watch, "_get_check_runs", fake_check_runs)

    result = await pr_ci_watch.watch_pr_ci(
        "REQ-9", "feat/REQ-9",
        poll_interval_sec=0, timeout_sec=10,
        repos=["ZonEaseTech/ttpos-server-go"],
    )
    assert result.passed
    # env var 完全没被用到，只用 caller 给的 repo
    assert set(looked_up) == {"ZonEaseTech/ttpos-server-go"}


@pytest.mark.asyncio
async def test_watch_pr_ci_multi_repo_all_green(monkeypatch):
    """多 repo REQ：所有 repo 都绿 → pass，cmd label 含全部 repo+sha。"""
    looked_up: list[str] = []

    async def fake_lookup(client, repo: str, _branch: str) -> tuple[int, str, str]:
        looked_up.append(repo)
        return (1, f"sha-{repo[:4]}aaaa", "open")

    async def fake_check_runs(client, _repo: str, _sha: str):
        return [_run("CI", conclusion="success")]

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)
    monkeypatch.setattr(pr_ci_watch, "_get_check_runs", fake_check_runs)

    result = await pr_ci_watch.watch_pr_ci(
        "REQ-9", "feat/REQ-9",
        poll_interval_sec=0, timeout_sec=10,
        repos=["a/repo-x", "b/repo-y"],
    )
    assert result.passed
    assert "a/repo-x" in result.cmd
    assert "b/repo-y" in result.cmd


@pytest.mark.asyncio
async def test_watch_pr_ci_multi_repo_one_fails(monkeypatch):
    """多 repo REQ：任一 repo CI 红 → 整体 fail，stdout 标出哪个 repo 红。"""
    async def fake_lookup(client, repo: str, _branch: str) -> tuple[int, str, str]:
        return (1, f"sha-{repo[:4]}aaaa", "open")

    async def fake_check_runs(client, repo: str, _sha: str):
        if repo == "b/repo-y":
            return [_run("CI", conclusion="failure")]
        return [_run("CI", conclusion="success")]

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)
    monkeypatch.setattr(pr_ci_watch, "_get_check_runs", fake_check_runs)

    result = await pr_ci_watch.watch_pr_ci(
        "REQ-9", "feat/REQ-9",
        poll_interval_sec=0, timeout_sec=10,
        repos=["a/repo-x", "b/repo-y"],
    )
    assert not result.passed
    assert result.exit_code == 1
    # 只输出失败的 repo 摘要
    assert "b/repo-y" in result.stdout_tail
    assert "failure" in result.stdout_tail


# ── SHA 翻转（force-push）检测 ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_sha_flip_restarts_check_runs(monkeypatch):
    """force-push 后 SHA 变化 → 清除旧 check-runs，从新 SHA 重新轮询，最终通过。"""
    sha_a = "a" * 40
    sha_b = "b" * 40
    call_count = [0]

    async def fake_lookup(client, repo: str, branch: str) -> tuple[int, str, str]:
        call_count[0] += 1
        # 前两次调用（initial + loop tick 1）返回 sha_a；之后返回 sha_b
        sha = sha_a if call_count[0] <= 2 else sha_b
        return (1, sha, "open")

    runs_fetched_for: list[str] = []

    async def fake_check_runs(client, repo: str, sha: str) -> list[dict]:
        runs_fetched_for.append(sha[:8])
        if sha == sha_b:
            return [_run("CI", conclusion="success")]
        return [_run("CI", status="in_progress", conclusion=None)]

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)
    monkeypatch.setattr(pr_ci_watch, "_get_check_runs", fake_check_runs)
    monkeypatch.setattr(pr_ci_watch.asyncio, "sleep", fast_sleep)
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/repo")

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9",
                                            poll_interval_sec=1, timeout_sec=60)

    assert result.passed is True
    assert result.exit_code == 0
    # SHA 翻转后 cmd 应反映新 SHA
    assert sha_b[:8] in result.cmd


@pytest.mark.asyncio
async def test_watch_pr_ci_too_many_sha_flips(monkeypatch):
    """SHA 翻转超过 5 次 → fail reason=too-many-sha-flips。"""
    call_count = [0]

    async def fake_lookup(client, repo: str, branch: str) -> tuple[int, str, str]:
        call_count[0] += 1
        # 每次调用返回不同 SHA，模拟持续 force-push
        sha = f"{call_count[0]:040x}"
        return (1, sha, "open")

    async def fake_check_runs(client, repo: str, sha: str) -> list[dict]:
        return [_run("CI", status="in_progress", conclusion=None)]

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)
    monkeypatch.setattr(pr_ci_watch, "_get_check_runs", fake_check_runs)
    monkeypatch.setattr(pr_ci_watch.asyncio, "sleep", fast_sleep)
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/repo")

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9",
                                            poll_interval_sec=0, timeout_sec=60)

    assert result.passed is False
    assert result.exit_code == 1
    assert "too-many-sha-flips" in result.stdout_tail


# ── PR merged / closed 检测 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_pr_merged_returns_pass(monkeypatch):
    """loop 轮询中 PR 被 merge → 立即返 pass（不等 check-runs）。"""
    call_count = [0]

    async def fake_lookup(client, repo: str, branch: str) -> tuple[int, str, str]:
        call_count[0] += 1
        # initial fetch → open；loop tick 1 re-fetch → merged
        state = "open" if call_count[0] == 1 else "merged"
        return (1, "a" * 40, state)

    check_run_calls = [0]

    async def fake_check_runs(client, repo: str, sha: str) -> list[dict]:
        check_run_calls[0] += 1
        return [_run("CI", status="in_progress", conclusion=None)]

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)
    monkeypatch.setattr(pr_ci_watch, "_get_check_runs", fake_check_runs)
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/repo")

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9",
                                            poll_interval_sec=0, timeout_sec=60)

    assert result.passed is True
    assert result.exit_code == 0
    assert "merged" in result.stdout_tail
    # 检测到 merged 后不再拉 check-runs
    assert check_run_calls[0] == 0


@pytest.mark.asyncio
async def test_watch_pr_ci_pr_closed_returns_fail(monkeypatch):
    """loop 轮询中 PR 被关闭（未 merge）→ fail reason=pr-closed-without-merge。"""
    call_count = [0]

    async def fake_lookup(client, repo: str, branch: str) -> tuple[int, str, str]:
        call_count[0] += 1
        state = "open" if call_count[0] == 1 else "closed"
        return (1, "a" * 40, state)

    async def fake_check_runs(client, repo: str, sha: str) -> list[dict]:
        return [_run("CI", conclusion="success")]

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)
    monkeypatch.setattr(pr_ci_watch, "_get_check_runs", fake_check_runs)
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/repo")

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9",
                                            poll_interval_sec=0, timeout_sec=60)

    assert result.passed is False
    assert result.exit_code == 1
    assert "pr-closed-without-merge" in result.stdout_tail


@pytest.mark.asyncio
async def test_watch_pr_ci_initial_pr_already_merged(monkeypatch):
    """初始 fetch 就发现 PR 已 merge → 立即 pass（连 loop 都不需等）。"""
    async def fake_lookup(client, repo: str, branch: str) -> tuple[int, str, str]:
        return (1, "a" * 40, "merged")

    check_run_calls = [0]

    async def fake_check_runs(client, repo: str, sha: str) -> list[dict]:
        check_run_calls[0] += 1
        return []

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)
    monkeypatch.setattr(pr_ci_watch, "_get_check_runs", fake_check_runs)
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/repo")

    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9",
                                            poll_interval_sec=0, timeout_sec=60)

    assert result.passed is True
    assert "merged" in result.stdout_tail
    assert check_run_calls[0] == 0


# ── refetch 失败重试 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_pr_refetch_error_retries(monkeypatch):
    """loop 内 _get_pr_info 抛 HTTP 错误 → 警告 + retry，不立即 fail。"""
    call_count = [0]

    async def fake_lookup(client, repo: str, branch: str) -> tuple[int, str, str]:
        call_count[0] += 1
        if call_count[0] == 2:  # loop tick 1 re-fetch 失败
            raise httpx.HTTPError("transient error")
        return (1, "a" * 40, "open")

    async def fake_check_runs(client, repo: str, sha: str) -> list[dict]:
        return [_run("CI", conclusion="success")]

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup)
    monkeypatch.setattr(pr_ci_watch, "_get_check_runs", fake_check_runs)
    monkeypatch.setattr(pr_ci_watch.asyncio, "sleep", fast_sleep)
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/repo")

    # tick 1: re-fetch 失败但 check-runs 用 cached SHA 成功 → pass
    result = await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9",
                                            poll_interval_sec=1, timeout_sec=60)

    assert result.passed is True


# ── _classify 单测 ───────────────────────────────────────────────────────

def test_classify_empty_is_pending():
    assert pr_ci_watch._classify([]) == "pending"


def test_classify_all_green():
    assert pr_ci_watch._classify([
        _run("a"), _run("b", conclusion="neutral"), _run("c", conclusion="skipped"),
    ]) == "pass"


def test_classify_any_fail_wins_over_pending():
    """fail 优先，即使还有 pending 没跑完。"""
    assert pr_ci_watch._classify([
        _run("a", conclusion="failure"),
        _run("b", status="in_progress", conclusion=None),
    ]) == "fail"


def test_classify_pending_when_any_in_progress():
    assert pr_ci_watch._classify([
        _run("a"),
        _run("b", status="in_progress", conclusion=None),
    ]) == "pending"


def test_classify_recognizes_all_fail_conclusions():
    for c in ["failure", "cancelled", "timed_out", "action_required", "stale"]:
        assert pr_ci_watch._classify([_run("x", conclusion=c)]) == "fail", c
