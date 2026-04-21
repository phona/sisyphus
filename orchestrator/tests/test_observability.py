"""observability.record_event 烟测：obs pool 关 → no-op；开 → 写一行带派生 stage。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from orchestrator import observability as obs


class FakeObsPool:
    def __init__(self):
        self.calls: list[tuple] = []
        self.execute = AsyncMock(side_effect=self._exec)

    async def _exec(self, sql, *args):
        self.calls.append((sql.strip()[:30], args))


def test_infer_stage_table():
    assert obs.infer_stage(["dev", "REQ-1"]) == "dev"
    assert obs.infer_stage(["accept-test", "REQ-1"]) == "accept-test"
    assert obs.infer_stage(["random"]) is None


@pytest.mark.asyncio
async def test_record_event_noop_without_pool(monkeypatch):
    monkeypatch.setattr(obs.db, "get_obs_pool", lambda: None)
    # 不抛即可
    await obs.record_event("router.decision", req_id="REQ-1")


@pytest.mark.asyncio
async def test_record_event_writes_when_pool(monkeypatch):
    pool = FakeObsPool()
    monkeypatch.setattr(obs.db, "get_obs_pool", lambda: pool)
    await obs.record_event(
        "action.executed",
        req_id="REQ-1", issue_id="i-9",
        tags=["dev", "REQ-1", "round-2", "target:unit", "parent-id:p1", "parent:reviewer"],
        router_action="create_dev",
        duration_ms=42,
    )
    assert len(pool.calls) == 1
    sql, args = pool.calls[0]
    assert sql.startswith("INSERT INTO event_log")
    # args order matches SQL: kind, req_id, stage, issue_id, parent_issue_id, parent_stage, tags, round, target, ...
    assert args[0] == "action.executed"
    assert args[1] == "REQ-1"
    assert args[2] == "dev"          # 自动派生
    assert args[3] == "i-9"
    assert args[4] == "p1"           # parent-id
    assert args[5] == "reviewer"     # parent:
    assert args[7] == 2              # round
    assert args[8] == "unit"         # target
    assert args[9] == "create_dev"   # router_action
    assert args[11] == 42            # duration_ms


@pytest.mark.asyncio
async def test_record_event_swallows_db_error(monkeypatch):
    """tap 不能阻塞业务：execute 抛了 record_event 也得 swallow。"""
    pool = FakeObsPool()
    pool.execute.side_effect = RuntimeError("PG down")
    monkeypatch.setattr(obs.db, "get_obs_pool", lambda: pool)
    await obs.record_event("router.decision", req_id="REQ-1")  # 不抛即过


@pytest.mark.asyncio
async def test_record_event_serializes_extras(monkeypatch):
    pool = FakeObsPool()
    monkeypatch.setattr(obs.db, "get_obs_pool", lambda: pool)
    await obs.record_event("router.decision", extras={"a": 1, "b": "x"})
    args = pool.calls[0][1]
    # extras 在最后一个位置，且是 JSON 字符串
    import json
    assert json.loads(args[-1]) == {"a": 1, "b": "x"}
