"""retry.executor 单测：mock BKDClient + retry_store，验不同 decision 分发的行为。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from orchestrator.retry.executor import RetryContext, run


@dataclass
class FakeIssue:
    id: str
    project_id: str = "p"
    issue_number: int = 0
    title: str = ""
    status_id: str = "todo"
    tags: list | None = None
    session_status: str | None = None
    description: str | None = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


def make_fake_bkd() -> AsyncMock:
    bkd = AsyncMock()
    bkd.create_issue = AsyncMock(return_value=FakeIssue(id="new-1"))
    bkd.update_issue = AsyncMock(return_value=FakeIssue(id="new-1"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    bkd.cancel_issue = AsyncMock(return_value={})
    return bkd


def patch_bkd(monkeypatch, fake):
    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake
    monkeypatch.setattr("orchestrator.retry.executor.BKDClient", _ctx)


class FakePool:
    async def execute(self, *a, **kw):
        return None

    async def fetchrow(self, *a, **kw):
        return None


def patch_round(monkeypatch, round: int):
    """mock retry_store.increment_round 返预设 round 值。"""
    async def fake_inc(pool, req_id, stage):
        return round
    monkeypatch.setattr(
        "orchestrator.retry.executor.retry_store.increment_round", fake_inc,
    )
    monkeypatch.setattr("orchestrator.retry.executor.db.get_pool", lambda: FakePool())


def base_ctx(issue_id: str | None = "dev-1", fail_kind: str = "test") -> RetryContext:
    return RetryContext(
        req_id="REQ-9",
        project_id="proj-1",
        stage="staging-test",
        fail_kind=fail_kind,
        issue_id=issue_id,
        details={
            "cmd": "make test",
            "exit_code": 1,
            "stdout_tail": "FAIL\n",
            "stderr_tail": "panic\n",
            "duration_sec": 2.0,
        },
    )


# ─── follow_up：调 follow_up_issue，不 emit ───────────────────────────────
@pytest.mark.asyncio
async def test_follow_up_calls_bkd_follow_up(monkeypatch):
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=1)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(issue_id="dev-1", fail_kind="test"))

    assert out["retry_action"] == "follow_up"
    assert out["issue_id"] == "dev-1"
    assert "emit" not in out
    fake.follow_up_issue.assert_awaited_once()
    call = fake.follow_up_issue.call_args
    assert call.kwargs["issue_id"] == "dev-1"
    assert call.kwargs["project_id"] == "proj-1"
    assert "REQ-9" in call.kwargs["prompt"]
    assert "staging-test" in call.kwargs["prompt"]


@pytest.mark.asyncio
async def test_follow_up_without_issue_id_skips(monkeypatch):
    """follow_up 决策但 ctx 没 issue_id → skipped，不炸。"""
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=1)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(issue_id=None, fail_kind="test"))

    assert out["retry_action"] == "follow_up"
    assert out["skipped"] is True
    fake.follow_up_issue.assert_not_called()


@pytest.mark.asyncio
async def test_surgical_kind_follow_up(monkeypatch):
    """schema 错 → follow_up（不看 round 早晚，只要没越 max_rounds）。"""
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=3)  # diagnose_threshold 但外科手术类不 diagnose
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(fail_kind="schema"))

    assert out["retry_action"] == "follow_up"
    fake.follow_up_issue.assert_awaited_once()


# ─── diagnose：新开 issue with diagnose tag ───────────────────────────────
@pytest.mark.asyncio
async def test_diagnose_creates_new_issue(monkeypatch):
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="diag-1")
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=3)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(fail_kind="test"))

    assert out["retry_action"] == "diagnose"
    assert out["diagnose_issue_id"] == "diag-1"
    fake.create_issue.assert_awaited_once()
    create_call = fake.create_issue.call_args
    assert "diagnose" in create_call.kwargs["tags"]
    assert "staging-test" in create_call.kwargs["tags"]
    assert "REQ-9" in create_call.kwargs["tags"]
    fake.follow_up_issue.assert_awaited_once()
    # diagnose issue 最终推到 working
    fake.update_issue.assert_awaited_once()


# ─── fresh_start：cancel 旧 issue + 开新 ────────────────────────────────
@pytest.mark.asyncio
async def test_fresh_start_cancels_old_and_creates_new(monkeypatch):
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fresh-1")
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=1)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(issue_id="dev-1", fail_kind="prompt_too_long"))

    assert out["retry_action"] == "fresh_start"
    assert out["new_issue_id"] == "fresh-1"
    fake.cancel_issue.assert_awaited_once_with("proj-1", "dev-1")
    fake.create_issue.assert_awaited_once()
    fake.follow_up_issue.assert_awaited_once()
    fake.update_issue.assert_awaited_once()


@pytest.mark.asyncio
async def test_fresh_start_cancel_failure_still_creates_new(monkeypatch):
    """cancel 失败（老 issue 不存在了）不能阻塞新开 — 走日志警告继续。"""
    fake = make_fake_bkd()
    fake.cancel_issue.side_effect = RuntimeError("not found")
    fake.create_issue.return_value = FakeIssue(id="fresh-2")
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=1)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(issue_id="dev-1", fail_kind="prompt_too_long"))

    assert out["retry_action"] == "fresh_start"
    assert out["new_issue_id"] == "fresh-2"
    fake.cancel_issue.assert_awaited_once()
    fake.create_issue.assert_awaited_once()


@pytest.mark.asyncio
async def test_fresh_start_without_issue_id_just_creates(monkeypatch):
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fresh-3")
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=1)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(issue_id=None, fail_kind="prompt_too_long"))

    assert out["retry_action"] == "fresh_start"
    fake.cancel_issue.assert_not_called()
    fake.create_issue.assert_awaited_once()


# ─── skip_check_retry：返 hint，不碰 BKD ─────────────────────────────────
@pytest.mark.asyncio
async def test_flaky_skip_check_retry_no_bkd_call(monkeypatch):
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=1)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(fail_kind="flaky"))

    assert out["retry_action"] == "skip_check_retry"
    assert "hint" in out
    fake.follow_up_issue.assert_not_called()
    fake.create_issue.assert_not_called()


# ─── escalate：round ≥ max_rounds 发 SESSION_FAILED ──────────────────────
@pytest.mark.asyncio
async def test_escalate_emits_session_failed(monkeypatch):
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(fail_kind="test"))

    assert out["retry_action"] == "escalate"
    assert out["emit"] == "session.failed"
    fake.follow_up_issue.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_fail_kind_escalates(monkeypatch):
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_round(monkeypatch, round=1)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    out = await run(base_ctx(fail_kind="mystery"))

    assert out["retry_action"] == "escalate"
    assert out["emit"] == "session.failed"


# ─── round 递增被调用（间接证明持久化被触发）──────────────────────────
@pytest.mark.asyncio
async def test_increment_round_is_called(monkeypatch):
    calls: list = []
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)

    async def fake_inc(pool, req_id, stage):
        calls.append((req_id, stage))
        return 1

    monkeypatch.setattr("orchestrator.retry.executor.retry_store.increment_round", fake_inc)
    monkeypatch.setattr("orchestrator.retry.executor.db.get_pool", lambda: FakePool())
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_max_rounds", 5)
    monkeypatch.setattr("orchestrator.retry.executor.settings.retry_diagnose_threshold", 3)

    await run(base_ctx(fail_kind="test"))

    assert calls == [("REQ-9", "staging-test")]
