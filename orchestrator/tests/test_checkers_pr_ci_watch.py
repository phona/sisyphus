"""checkers/pr_ci_watch.py 单测：mock GitHub API，验全绿/任一失败/全失败/超时。

M15：watch_pr_ci(req_id, branch, ...)，repo 从 SISYPHUS_BUSINESS_REPO env 读，
pr_number + head.sha 用 GitHub REST API `head` 过滤器按 branch 查（这里 mock 掉），不再读 manifest。
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
    """SISYPHUS_BUSINESS_REPO env + mock _get_pr_info 返指定 (number, sha)。"""
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", repo)

    async def fake_lookup(client, _repo: str, _branch: str) -> tuple[int, str]:
        if pr_number is None:
            raise ValueError("No open PR found")
        return pr_number, "deadbeef" * 5

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

    async def fake_lookup_fail(client, _repo: str, _branch: str) -> tuple[int, str]:
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
async def test_watch_pr_ci_raises_when_env_missing(monkeypatch):
    """SISYPHUS_BUSINESS_REPO 没设 → 直接 ValueError。"""
    monkeypatch.delenv("SISYPHUS_BUSINESS_REPO", raising=False)
    with pytest.raises(ValueError, match="SISYPHUS_BUSINESS_REPO"):
        await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9")


@pytest.mark.asyncio
async def test_watch_pr_ci_raises_when_no_pr(monkeypatch):
    """_get_pr_info 找不到对应 PR → ValueError。"""
    monkeypatch.setenv("SISYPHUS_BUSINESS_REPO", "phona/ubox-crosser")

    async def fake_lookup_none(client, _repo: str, _branch: str) -> tuple[int, str]:
        raise ValueError("No open PR found for branch")
    monkeypatch.setattr(pr_ci_watch, "_get_pr_info", fake_lookup_none)

    with pytest.raises(ValueError, match="No open PR found"):
        await pr_ci_watch.watch_pr_ci("REQ-9", "feat/REQ-9")


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
