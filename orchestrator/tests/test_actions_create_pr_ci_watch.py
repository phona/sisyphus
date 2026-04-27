"""actions/create_pr_ci_watch.py：runner discovery + CI dispatch 单测。

不测 watch_pr_ci 主体（在 test_checkers_pr_ci_watch.py），只测：
- _discover_repos_from_runner 对 runner stdout 的解析 + 失败兜底
- _dispatch_ci_trigger 的 GH API 调用、容错、flag 开关
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from orchestrator.actions import create_pr_ci_watch as action


@dataclass
class FakeExec:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


def _patch_controller(monkeypatch, fake_exec: AsyncMock) -> None:
    class FakeRC:
        def __init__(self, exec_fn):
            self.exec_in_runner = exec_fn

    monkeypatch.setattr(
        "orchestrator.actions.create_pr_ci_watch.k8s_runner.get_controller",
        lambda: FakeRC(fake_exec),
    )


@pytest.mark.asyncio
async def test_discover_repos_parses_ssh_and_https(monkeypatch):
    stdout = "\n".join([
        "git@github.com:phona/sisyphus.git",
        "https://github.com/ZonEaseTech/ttpos-server-go.git",
        "https://github.com/phona/ttpos-flutter",
    ])
    exec_fn = AsyncMock(return_value=FakeExec(stdout=stdout))
    _patch_controller(monkeypatch, exec_fn)

    repos = await action._discover_repos_from_runner("REQ-x")
    assert repos == [
        "phona/sisyphus",
        "ZonEaseTech/ttpos-server-go",
        "phona/ttpos-flutter",
    ]
    exec_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_repos_returns_empty_on_exec_error(monkeypatch):
    exec_fn = AsyncMock(side_effect=RuntimeError("pod not found"))
    _patch_controller(monkeypatch, exec_fn)

    repos = await action._discover_repos_from_runner("REQ-x")
    assert repos == []


@pytest.mark.asyncio
async def test_discover_repos_skips_non_github_remotes(monkeypatch):
    stdout = "\n".join([
        "git@gitlab.com:foo/bar.git",
        "git@github.com:phona/sisyphus.git",
        "ssh://gerrit.example.com/team/proj",
    ])
    exec_fn = AsyncMock(return_value=FakeExec(stdout=stdout))
    _patch_controller(monkeypatch, exec_fn)

    repos = await action._discover_repos_from_runner("REQ-x")
    assert repos == ["phona/sisyphus"]


@pytest.mark.asyncio
async def test_discover_repos_empty_stdout(monkeypatch):
    exec_fn = AsyncMock(return_value=FakeExec(stdout=""))
    _patch_controller(monkeypatch, exec_fn)

    repos = await action._discover_repos_from_runner("REQ-x")
    assert repos == []


# ── _dispatch_ci_trigger tests ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_ci_trigger_calls_gh_api(httpx_mock, monkeypatch):
    """PRCIAD-S1: dispatch fires POST /dispatches for each repo before polling."""
    monkeypatch.setattr(action.settings, "github_token", "tok-test")
    monkeypatch.setattr(action.settings, "pr_ci_dispatch_event_type", "ci-trigger")

    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo-a/dispatches",
        status_code=204,
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo-b/dispatches",
        status_code=204,
    )

    await action._dispatch_ci_trigger(
        repos=["owner/repo-a", "owner/repo-b"],
        branch="feat/REQ-426",
        req_id="REQ-426",
    )

    requests = httpx_mock.get_requests()
    assert len(requests) == 2
    urls = {str(r.url) for r in requests}
    assert "https://api.github.com/repos/owner/repo-a/dispatches" in urls
    assert "https://api.github.com/repos/owner/repo-b/dispatches" in urls

    body = json.loads(requests[0].content)
    assert body["event_type"] == "ci-trigger"
    assert body["client_payload"]["branch"] == "feat/REQ-426"
    assert body["client_payload"]["req_id"] == "REQ-426"


@pytest.mark.asyncio
async def test_dispatch_ci_trigger_tolerates_per_repo_error(httpx_mock, monkeypatch):
    """PRCIAD-S3: one repo 422 does not abort; other repo succeeds; no exception."""
    monkeypatch.setattr(action.settings, "github_token", "tok-test")
    monkeypatch.setattr(action.settings, "pr_ci_dispatch_event_type", "ci-trigger")

    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo-a/dispatches",
        status_code=422,
        json={"message": "Unprocessable"},
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/owner/repo-b/dispatches",
        status_code=204,
    )

    # Must not raise even though repo-a returns 422
    await action._dispatch_ci_trigger(
        repos=["owner/repo-a", "owner/repo-b"],
        branch="feat/REQ-426",
        req_id="REQ-426",
    )

    requests = httpx_mock.get_requests()
    assert len(requests) == 2


@pytest.mark.asyncio
async def test_run_checker_skips_dispatch_when_disabled(monkeypatch):
    """PRCIAD-S2: pr_ci_dispatch_enabled=False — no _dispatch_ci_trigger call issued."""
    monkeypatch.setattr(action.settings, "pr_ci_dispatch_enabled", False)

    dispatch_called = []

    async def fake_dispatch(**kwargs):
        dispatch_called.append(kwargs)

    monkeypatch.setattr(action, "_dispatch_ci_trigger", fake_dispatch)

    # Stub out watch_pr_ci and discover_repos so _run_checker exits cleanly
    from orchestrator.checkers._types import CheckResult
    monkeypatch.setattr(
        action.checker, "watch_pr_ci",
        AsyncMock(return_value=CheckResult(
            passed=True, exit_code=0, stdout_tail="ok", stderr_tail="",
            duration_sec=0.1, cmd="test",
        )),
    )
    exec_fn = AsyncMock(return_value=FakeExec(
        stdout="git@github.com:owner/repo-a.git"
    ))
    _patch_controller(monkeypatch, exec_fn)

    # Stub DB pool so artifact_checks.insert_check doesn't fail
    pool_mock = AsyncMock()
    monkeypatch.setattr(action.db, "get_pool", lambda: pool_mock)
    monkeypatch.setattr(action.artifact_checks, "insert_check", AsyncMock())

    await action._run_checker(req_id="REQ-426", ctx={"branch": "feat/REQ-426"})

    assert dispatch_called == [], "dispatch should not be called when flag is False"
