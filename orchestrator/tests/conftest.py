"""Test config: 设默认 env，让 Settings 不需要外部 .env 也能 import。"""
from __future__ import annotations

import os
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
    "orchestrator.checkers.execute_artifact_check.ensure_runner_alive",
    "orchestrator.actions.create_pr_ci_watch.ensure_runner_alive",
    "orchestrator.actions.teardown_accept_env.ensure_runner_alive",
)


@pytest.fixture(autouse=True)
def _stub_ensure_runner_alive(request, monkeypatch):
    if "no_stub_self_heal" in request.keywords:
        return
    for target in _SELF_HEAL_PATCH_TARGETS:
        monkeypatch.setattr(target, AsyncMock(return_value=True))
