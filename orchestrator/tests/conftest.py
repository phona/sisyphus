"""Test config: 设默认 env，让 Settings 不需要外部 .env 也能 import。"""
from __future__ import annotations

import os
import re
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("SISYPHUS_BKD_TOKEN", "test-token")
os.environ.setdefault("SISYPHUS_PG_DSN", "postgresql://test:test@localhost/test")
os.environ.setdefault("SISYPHUS_BKD_BASE_URL", "https://bkd.example.test/api")
os.environ.setdefault("SISYPHUS_WEBHOOK_TOKEN", "test-webhook-token")


# REQ-fix-runner-self-heal-394：checker / action 在 exec_in_runner 之前调
# `ensure_runner_alive` (lazy recreate)。tests 用 partial-mock RunnerController
# (只 stub `exec_in_runner`)，少了 `get_runner_status` 等方法。autouse fixture 把
# 自愈钩 stub 成 True，让既有 checker/action 测试不需要改。专测自愈逻辑的
# `test_actions_runner_self_heal.py` opt-out 这条 fixture。
_SELF_HEAL_PATCH_TARGETS = (
    "orchestrator.checkers.spec_lint.ensure_runner_alive",
    "orchestrator.checkers.dev_cross_check.ensure_runner_alive",
    "orchestrator.checkers.staging_test.ensure_runner_alive",
    "orchestrator.checkers.analyze_artifact_check.ensure_runner_alive",
    "orchestrator.actions.create_pr_ci_watch.ensure_runner_alive",
    "orchestrator.actions.teardown_accept_env.ensure_runner_alive",
)


@pytest.fixture(autouse=True)
def _stub_ensure_runner_alive(request, monkeypatch):
    if "no_stub_self_heal" in request.keywords:
        return
    for target in _SELF_HEAL_PATCH_TARGETS:
        monkeypatch.setattr(target, AsyncMock(return_value=True))


# closes #474: pr_ci_watch 现在双路拉 check-runs + commit statuses。已有
# pr_ci_watch 测试只 mock 了 check-runs；statuses 默认空数组兜底，让现存测试
# 不用挨个改，专门测 statuses / image_tag 的新测试自己 add_response 覆写。
#
# 不写 `httpx_mock` 进 fixture signature —— pytest-httpx 的 httpx_mock 是
# request-scoped lazy fixture，autouse fixture 强引用会让它**所有 test** 都
# 激活 httpx 拦截，破坏只用 monkeypatch / 不沾 HTTP 的纯逻辑测试。这里走 dynamic
# request.getfixturevalue 仅在 test 自己已请求 httpx_mock 时才 inject。
@pytest.fixture(autouse=True)
def _default_empty_commit_statuses(request):
    if "no_stub_commit_statuses" in request.keywords:
        return
    if "httpx_mock" not in request.fixturenames:
        return
    httpx_mock = request.getfixturevalue("httpx_mock")
    httpx_mock.add_response(
        url=re.compile(
            r"^https://api\.github\.com/repos/[^/]+/[^/]+/commits/.+/statuses(\?.*)?$"
        ),
        json=[],
        is_reusable=True,
        is_optional=True,
    )
