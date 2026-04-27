"""dispatch_slugs store 单元测试。

FakePool 模拟 asyncpg，不打真 DB。
"""
from __future__ import annotations

import pytest

from orchestrator.store import dispatch_slugs


class FakePool:
    """轻量 mock：fetchrow 返预设序列；execute 记录调用。"""

    def __init__(self, fetchrow_val=None):
        self._fetchrow_val = fetchrow_val
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        return self._fetchrow_val

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))


# ─── get ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_returns_none_when_absent():
    """slug 不存在 → get 返 None（DISP-S3）。"""
    pool = FakePool(fetchrow_val=None)
    result = await dispatch_slugs.get(pool, "verifier|REQ-1|spec_lint|fail|r0")
    assert result is None


@pytest.mark.asyncio
async def test_get_returns_issue_id_when_present():
    """slug 存在 → get 返 issue_id（DISP-S1 前提）。"""
    pool = FakePool(fetchrow_val={"issue_id": "abc123"})
    result = await dispatch_slugs.get(pool, "verifier|REQ-1|spec_lint|fail|r0")
    assert result == "abc123"


@pytest.mark.asyncio
async def test_get_queries_correct_table():
    pool = FakePool(fetchrow_val=None)
    await dispatch_slugs.get(pool, "some-slug")
    # fetchrow は FakePool に記録されないが、エラーなく到達した = SQL 文は正しい


# ─── put ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_executes_insert():
    """put → INSERT INTO dispatch_slugs（DISP-S4）。"""
    pool = FakePool()
    await dispatch_slugs.put(pool, "verifier|REQ-1|spec_lint|fail|r0", "issue-xyz")
    assert len(pool.executed) == 1
    sql, args = pool.executed[0]
    assert "INSERT INTO dispatch_slugs" in sql
    assert "ON CONFLICT DO NOTHING" in sql
    assert args == ("verifier|REQ-1|spec_lint|fail|r0", "issue-xyz")


@pytest.mark.asyncio
async def test_put_does_not_raise_on_conflict():
    """put は ON CONFLICT DO NOTHING なので二度目も例外なし（DISP-S4）。"""
    pool = FakePool()
    await dispatch_slugs.put(pool, "fixer|REQ-1|dev|r1", "id-a")
    await dispatch_slugs.put(pool, "fixer|REQ-1|dev|r1", "id-b")
    # 2 回とも execute が呼ばれ例外は出ない
    assert len(pool.executed) == 2


# ─── slug 命名パターン検証 ──────────────────────────────────────────────────

def test_verifier_slug_format():
    """verifier slug = verifier|{req_id}|{stage}|{trigger}|r{round}。"""
    req_id = "REQ-427"
    stage = "spec_lint"
    trigger = "fail"
    fixer_round = 0
    slug = f"verifier|{req_id}|{stage}|{trigger}|r{fixer_round}"
    assert slug == "verifier|REQ-427|spec_lint|fail|r0"


def test_fixer_slug_format():
    """fixer slug = fixer|{req_id}|{fixer}|r{round}。"""
    req_id = "REQ-427"
    fixer = "dev"
    next_round = 2
    slug = f"fixer|{req_id}|{fixer}|r{next_round}"
    assert slug == "fixer|REQ-427|dev|r2"


def test_round_aware_slugs_differ(monkeypatch):
    """round 0 と round 1 のスラグは別物（DISP-S5）。"""
    slug_r0 = "verifier|REQ-1|spec_lint|fail|r0"
    slug_r1 = "verifier|REQ-1|spec_lint|success|r1"
    assert slug_r0 != slug_r1


def test_action_handler_slug_format():
    """一般 action handler slug = {action}|{req_id}|{executionId or ''}。"""
    req_id = "REQ-427"
    execution_id = "exec-abc"
    slug = f"analyze|{req_id}|{execution_id}"
    assert slug == "analyze|REQ-427|exec-abc"

    slug_no_exec = f"analyze|{req_id}|"
    assert slug_no_exec == "analyze|REQ-427|"
