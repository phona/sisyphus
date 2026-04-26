"""dispatch_mcp.queries 单测：fake asyncpg pool（同 test_admission.py 的 pattern）。

覆盖 6 个 scenario（DISPATCH-MCP-S1..S6）+ 边界 limit / state 校验。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from orchestrator.dispatch_mcp import queries


class _FakePool:
    """Minimal asyncpg-like pool: only fetchrow / fetch are wired."""

    def __init__(
        self,
        *,
        single_row: dict | None = None,
        many_rows: list[dict] | None = None,
        single_raise: Exception | None = None,
    ):
        self._single = single_row
        self._many = many_rows or []
        self._single_raise = single_raise
        self.last_sql: str | None = None
        self.last_args: tuple | None = None

    async def fetchrow(self, sql, *args):
        self.last_sql = sql
        self.last_args = args
        if self._single_raise is not None:
            raise self._single_raise
        return self._single

    async def fetch(self, sql, *args):
        self.last_sql = sql
        self.last_args = args
        return self._many


# ─── Scenario S1: get_req_state happy path ─────────────────────────────────


@pytest.mark.asyncio
async def test_s1_get_req_state_returns_expected_shape():
    created = datetime(2026, 4, 26, 9, 0, 0, tzinfo=UTC)
    updated = datetime(2026, 4, 26, 10, 0, 0, tzinfo=UTC)
    history = [
        {"to": "init", "ts": "2026-04-26T09:00:00Z"},
        {"to": "analyzing", "ts": "2026-04-26T10:00:00Z"},
    ]
    context = {"a": 1, "b": 2}
    pool = _FakePool(single_row={
        "req_id": "REQ-x",
        "project_id": "p1",
        "state": "analyzing",
        "history": history,
        "context": context,
        "created_at": created,
        "updated_at": updated,
    })

    result = await queries.fetch_req_state(pool, "REQ-x")

    assert result is not None
    assert result["req_id"] == "REQ-x"
    assert result["project_id"] == "p1"
    assert result["state"] == "analyzing"
    assert result["created_at"] == created.isoformat()
    assert result["updated_at"] == updated.isoformat()
    assert result["last_event"] == {"to": "analyzing", "ts": "2026-04-26T10:00:00Z"}
    assert sorted(result["context_keys"]) == ["a", "b"]
    # The query MUST bind req_id as $1
    assert pool.last_args == ("REQ-x",)


@pytest.mark.asyncio
async def test_s1_history_string_jsonb_is_decoded():
    """asyncpg without a json codec returns jsonb as str — handle that."""
    history_str = json.dumps([{"to": "done", "ts": "2026-04-26T11:00:00Z"}])
    context_str = json.dumps({"k": "v"})
    pool = _FakePool(single_row={
        "req_id": "REQ-y",
        "project_id": "p1",
        "state": "done",
        "history": history_str,
        "context": context_str,
        "created_at": None,
        "updated_at": None,
    })

    result = await queries.fetch_req_state(pool, "REQ-y")
    assert result is not None
    assert result["last_event"] == {"to": "done", "ts": "2026-04-26T11:00:00Z"}
    assert result["context_keys"] == ["k"]


# ─── Scenario S2: get_req_state returns None for missing REQ ───────────────


@pytest.mark.asyncio
async def test_s2_get_req_state_returns_none_when_missing():
    pool = _FakePool(single_row=None)

    result = await queries.fetch_req_state(pool, "REQ-missing")

    assert result is None


# ─── Scenario S3: list_reqs limit clamping ─────────────────────────────────


@pytest.mark.asyncio
async def test_s3_list_reqs_clamps_limit_high():
    pool = _FakePool(many_rows=[])
    await queries.fetch_reqs(pool, state=None, limit=999)
    assert pool.last_args == (queries.LIST_LIMIT_MAX,)


@pytest.mark.asyncio
async def test_s3_list_reqs_clamps_limit_low():
    pool = _FakePool(many_rows=[])
    await queries.fetch_reqs(pool, state=None, limit=0)
    assert pool.last_args == (queries.LIST_LIMIT_MIN,)


@pytest.mark.asyncio
async def test_s3_list_reqs_in_range_limit_passes_through():
    pool = _FakePool(many_rows=[])
    await queries.fetch_reqs(pool, state=None, limit=42)
    assert pool.last_args == (42,)


# ─── Scenario S4: list_reqs filters by state ───────────────────────────────


@pytest.mark.asyncio
async def test_s4_list_reqs_with_state_binds_state_and_limit():
    pool = _FakePool(many_rows=[
        {"req_id": "REQ-a", "project_id": "p1", "state": "analyzing",
         "updated_at": datetime(2026, 4, 26, 10, 0, 0, tzinfo=UTC)},
    ])

    result = await queries.fetch_reqs(pool, state="analyzing", limit=50)

    assert pool.last_args == ("analyzing", 50)
    assert "WHERE state = $1" in (pool.last_sql or "")
    assert len(result) == 1
    assert result[0]["req_id"] == "REQ-a"
    assert result[0]["state"] == "analyzing"
    assert result[0]["updated_at"] == "2026-04-26T10:00:00+00:00"


# ─── Scenario S5: list_reqs raises ValueError on unknown state ─────────────


@pytest.mark.asyncio
async def test_s5_list_reqs_unknown_state_raises():
    pool = _FakePool(many_rows=[])
    with pytest.raises(ValueError) as exc:
        await queries.fetch_reqs(pool, state="banana", limit=50)
    msg = str(exc.value)
    assert "unknown state" in msg
    # Some valid value must appear in the message so the IDE agent can
    # immediately see the expected enum.
    assert "analyzing" in msg
    # And the pool was never touched.
    assert pool.last_sql is None


# ─── Scenario S6: get_req_state redacts context body ───────────────────────


@pytest.mark.asyncio
async def test_s6_get_req_state_redacts_context_body():
    secret = "<long secret prompt text that must not leak>"
    pool = _FakePool(single_row={
        "req_id": "REQ-redact",
        "project_id": "p1",
        "state": "intaking",
        "history": [],
        "context": {"prompt": secret, "intent": {"k": "v"}},
        "created_at": None,
        "updated_at": None,
    })

    result = await queries.fetch_req_state(pool, "REQ-redact")

    assert result is not None
    assert "context" not in result
    assert sorted(result["context_keys"]) == ["intent", "prompt"]
    # No string value from the context body should leak into the
    # serialised result.
    assert secret not in json.dumps(result)


# ─── Edge: empty history ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_req_state_empty_history_yields_none_last_event():
    pool = _FakePool(single_row={
        "req_id": "REQ-empty",
        "project_id": "p1",
        "state": "init",
        "history": [],
        "context": {},
        "created_at": None,
        "updated_at": None,
    })

    result = await queries.fetch_req_state(pool, "REQ-empty")
    assert result is not None
    assert result["last_event"] is None
    assert result["context_keys"] == []
