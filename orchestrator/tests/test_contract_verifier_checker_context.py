"""Contract tests for REQ-fix-verifier-checker-context-1777723926:
verifier prompt 注入 mechanical checker stdout/stderr 上下文。

Black-box contracts:
- trigger=fail 的 verifier prompt 必须包含 artifact_checks 最新记录的
  stdout_tail / stderr_tail / exit_code。
- 输出过长时必须截断（只保留尾部 N 行）。
- trigger=success 时不应查询 artifact_checks（避免多余 IO）。
- agent stage（analyze / accept / challenger）无 artifact_checks 记录时
  prompt 正常渲染，不抛异常。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


class FakePool:
    def __init__(self, row: dict | None = None):
        self._row = row
        self.calls: list = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql.strip()[:60], args))
        if self._row is None:
            return None
        return self._row


@pytest.fixture
def fake_bkd(monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    fake = AsyncMock()
    fake.create_issue = AsyncMock(return_value=type("I", (), {"id": "vfy-test"})())
    fake.update_issue = AsyncMock()
    fake.follow_up_issue = AsyncMock()
    monkeypatch.setattr("orchestrator.actions._verifier.BKDClient", _ctx)
    monkeypatch.setattr(
        "orchestrator.actions._verifier.dispatch_slugs.get", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "orchestrator.actions._verifier.dispatch_slugs.put", AsyncMock()
    )
    monkeypatch.setattr(
        "orchestrator.actions._verifier.req_state.update_context", AsyncMock()
    )
    return fake


@pytest.mark.asyncio
async def test_fail_prompt_contains_checker_output(fake_bkd, monkeypatch):
    """C1: trigger=fail + DB 有记录 → prompt 包含 exit_code / stdout / stderr。"""
    from orchestrator.actions import _verifier as v

    pool = FakePool(row={
        "exit_code": 1,
        "stdout_tail": "PASS: unit tests\nFAIL: lint",
        "stderr_tail": "golangci-lint: type error in foo.go",
        "cmd": "make ci-lint",
        "duration_sec": 30.0,
        "attempts": 1,
        "flake_reason": None,
        "checked_at": None,
    })
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: pool)

    await v.invoke_verifier(
        stage="dev_cross_check", trigger="fail",
        req_id="REQ-273", project_id="proj",
    )

    _, kwargs = fake_bkd.follow_up_issue.await_args
    prompt = kwargs["prompt"]

    assert "exit_code: `1`" in prompt
    assert "PASS: unit tests" in prompt
    assert "golangci-lint: type error in foo.go" in prompt
    assert "机械 checker 输出" in prompt


@pytest.mark.asyncio
async def test_fail_prompt_truncates_long_output(fake_bkd, monkeypatch):
    """C2: stdout/stderr 超过 50 行时只保留尾部。"""
    from orchestrator.actions import _verifier as v

    stdout = "\n".join(f"stdout-line{i}" for i in range(100))
    stderr = "\n".join(f"stderr-line{i}" for i in range(100))
    pool = FakePool(row={
        "exit_code": 2,
        "stdout_tail": stdout,
        "stderr_tail": stderr,
        "cmd": "make ci-test",
        "duration_sec": 60.0,
        "attempts": 1,
        "flake_reason": None,
        "checked_at": None,
    })
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: pool)

    await v.invoke_verifier(
        stage="spec_lint", trigger="fail",
        req_id="REQ-273", project_id="proj",
    )

    _, kwargs = fake_bkd.follow_up_issue.await_args
    prompt = kwargs["prompt"]

    # 尾部行必须存在
    assert "stdout-line99" in prompt
    assert "stderr-line99" in prompt
    # 头部行应被截断
    assert "stdout-line0" not in prompt
    assert "stderr-line0" not in prompt
    # 行数应接近截断上限（允许少量额外换行来自模板格式）
    assert prompt.count("stdout-line") <= 55
    assert prompt.count("stderr-line") <= 55


@pytest.mark.asyncio
async def test_success_prompt_does_not_query_artifact_checks(fake_bkd, monkeypatch):
    """C3: trigger=success 时不应查询 artifact_checks。"""
    from orchestrator.actions import _verifier as v

    pool = FakePool(row=None)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: pool)

    await v.invoke_verifier(
        stage="staging_test", trigger="success",
        req_id="REQ-273", project_id="proj",
    )

    assert pool.calls == [], "success trigger must not query artifact_checks"
    fake_bkd.follow_up_issue.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_stage_fail_prompt_without_db_record(fake_bkd, monkeypatch):
    """C4: analyze / accept / challenger 等 agent stage 无 DB 记录时正常渲染。"""
    from orchestrator.actions import _verifier as v

    pool = FakePool(row=None)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: pool)

    await v.invoke_verifier(
        stage="analyze", trigger="fail",
        req_id="REQ-273", project_id="proj",
    )

    _, kwargs = fake_bkd.follow_up_issue.await_args
    prompt = kwargs["prompt"]
    assert "verifier-agent" in prompt
    assert "REQ-273" in prompt
    # 无 checker 输出区块（因为 DB 没记录）
    assert "机械 checker 输出" not in prompt


@pytest.mark.asyncio
async def test_pr_ci_stage_maps_correct_db_stage(fake_bkd, monkeypatch):
    """C5: pr_ci stage 查询 artifact_checks 时使用 pr-ci-watch 作为 stage 值。"""
    from orchestrator.actions import _verifier as v

    pool = FakePool(row={
        "exit_code": 1,
        "stdout_tail": "repo-a: lint=failure",
        "stderr_tail": "",
        "cmd": "watch-pr-ci",
        "duration_sec": 300.0,
        "attempts": 1,
        "flake_reason": None,
        "checked_at": None,
    })
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: pool)

    await v.invoke_verifier(
        stage="pr_ci", trigger="fail",
        req_id="REQ-273", project_id="proj",
    )

    assert len(pool.calls) == 1
    _sql, args = pool.calls[0]
    assert args[1] == "pr-ci-watch"

    _, kwargs = fake_bkd.follow_up_issue.await_args
    prompt = kwargs["prompt"]
    assert "repo-a: lint=failure" in prompt
