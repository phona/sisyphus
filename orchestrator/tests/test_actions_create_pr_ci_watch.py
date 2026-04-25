"""actions/create_pr_ci_watch.py：runner discovery 单测。

不测 watch_pr_ci 主体（在 test_checkers_pr_ci_watch.py），只测 _discover_repos_from_runner
对 runner stdout 的解析 + 失败兜底。
"""
from __future__ import annotations

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
