"""actions/create_pr_ci_watch.py：runner discovery + direct dispatch 单测。

不测 watch_pr_ci 主体（在 test_checkers_pr_ci_watch.py），只测：
- _discover_repos_from_runner 对 runner stdout 的解析 + 失败兜底
- _dispatch_to_ci_repo 的 flag / skip / payload / error 行为（SIS-447-S1～S5）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

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


# ── _dispatch_to_ci_repo ──────────────────────────────────────────────────

def _patch_settings(monkeypatch, *, enabled: bool, repo: str, event_type: str = "pr-ci-run"):
    fake = MagicMock()
    fake.ci_dispatch_enabled = enabled
    fake.ci_dispatch_repo = repo
    fake.ci_dispatch_event_type = event_type
    fake.github_token = "ghp_test"
    monkeypatch.setattr("orchestrator.actions.create_pr_ci_watch.settings", fake)


@pytest.mark.asyncio
async def test_dispatch_skipped_when_disabled(monkeypatch):
    """SIS-447-S1: ci_dispatch_enabled=False → _get_pr_info never called, no HTTP."""
    _patch_settings(monkeypatch, enabled=False, repo="phona/ttpos-ci")
    fake_pr_info = AsyncMock()
    monkeypatch.setattr("orchestrator.actions.create_pr_ci_watch._get_pr_info", fake_pr_info)

    await action._dispatch_to_ci_repo(
        req_id="REQ-447", branch="feat/REQ-447", repos=["phona/repo-a"]
    )
    fake_pr_info.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_skipped_when_repo_empty(monkeypatch):
    """SIS-447-S2: ci_dispatch_repo="" → _get_pr_info never called even if enabled."""
    _patch_settings(monkeypatch, enabled=True, repo="")
    fake_pr_info = AsyncMock()
    monkeypatch.setattr("orchestrator.actions.create_pr_ci_watch._get_pr_info", fake_pr_info)

    await action._dispatch_to_ci_repo(
        req_id="REQ-447", branch="feat/REQ-447", repos=["phona/repo-a"]
    )
    fake_pr_info.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_sends_one_per_repo(httpx_mock, monkeypatch):
    """SIS-447-S3: two source repos → two dispatches with correct payload fields."""
    _patch_settings(monkeypatch, enabled=True, repo="phona/ttpos-ci")

    fake_pr_info = AsyncMock(side_effect=[
        (11, "aaa111", "open"),
        (22, "bbb222", "open"),
    ])
    monkeypatch.setattr("orchestrator.actions.create_pr_ci_watch._get_pr_info", fake_pr_info)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ttpos-ci/dispatches",
        status_code=204,
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ttpos-ci/dispatches",
        status_code=204,
    )

    await action._dispatch_to_ci_repo(
        req_id="REQ-447",
        branch="feat/REQ-447",
        repos=["phona/repo-a", "phona/repo-b"],
    )

    requests = httpx_mock.get_requests()
    assert len(requests) == 2

    body0 = json.loads(requests[0].content)
    assert body0["event_type"] == "pr-ci-run"
    assert body0["client_payload"]["source_repo"] == "phona/repo-a"
    assert body0["client_payload"]["sha"] == "aaa111"
    assert body0["client_payload"]["pr_number"] == 11
    assert body0["client_payload"]["req_id"] == "REQ-447"

    body1 = json.loads(requests[1].content)
    assert body1["client_payload"]["source_repo"] == "phona/repo-b"
    assert body1["client_payload"]["pr_number"] == 22


@pytest.mark.asyncio
async def test_dispatch_http_error_does_not_raise(httpx_mock, monkeypatch):
    """SIS-447-S4: HTTP 422 from dispatch endpoint → warning logged, no exception."""
    _patch_settings(monkeypatch, enabled=True, repo="phona/ttpos-ci")

    fake_pr_info = AsyncMock(return_value=(99, "ccc333", "open"))
    monkeypatch.setattr("orchestrator.actions.create_pr_ci_watch._get_pr_info", fake_pr_info)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ttpos-ci/dispatches",
        status_code=422,
        json={"message": "Unprocessable Entity"},
    )

    # Must not raise
    await action._dispatch_to_ci_repo(
        req_id="REQ-447", branch="feat/REQ-447", repos=["phona/repo-a"]
    )


@pytest.mark.asyncio
async def test_dispatch_skips_repo_with_no_pr(monkeypatch):
    """SIS-447-S5: PR not found → skip dispatch for that repo, no HTTP, no exception."""
    _patch_settings(monkeypatch, enabled=True, repo="phona/ttpos-ci")

    fake_pr_info = AsyncMock(
        side_effect=ValueError("No PR found for branch feat/REQ-447 in phona/repo-a")
    )
    monkeypatch.setattr("orchestrator.actions.create_pr_ci_watch._get_pr_info", fake_pr_info)

    # _get_pr_info raises ValueError → dispatch loop skips this repo
    # httpx_mock not needed (no HTTP call expected)
    await action._dispatch_to_ci_repo(
        req_id="REQ-447", branch="feat/REQ-447", repos=["phona/repo-a"]
    )
