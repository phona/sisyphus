"""Challenger contract tests for REQ-mcp-sisyphus-dispatch-m0-1777220172.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-mcp-sisyphus-dispatch-m0-1777220172/specs/dispatch-mcp/spec.md

Scenarios covered:
  DISPATCH-MCP-M0  queries/server/__main__ modules importable + symbols exposed
  DISPATCH-MCP-S1  fetch_req_state returns dict with required keys, correct state, correct context_keys
  DISPATCH-MCP-S2  fetch_req_state returns None for missing REQ
  DISPATCH-MCP-S3  fetch_reqs limit clamped to [1, 200] — SQL param verified
  DISPATCH-MCP-S4  fetch_reqs state filter binds 'analyzing' as $N SQL param
  DISPATCH-MCP-S5  fetch_reqs raises ValueError on unknown state with message listing valid values
  DISPATCH-MCP-S6  fetch_req_state redacts context body: only keys returned, no raw body exposed
"""
from __future__ import annotations

import pytest

# ── Fake pool ────────────────────────────────────────────────────────────────


class _FakePool:
    """Captures fetchrow / fetch calls; returns configured data."""

    def __init__(self, row: dict | None = None, rows: list | None = None):
        self._row = row
        self._rows = rows or []
        self.last_sql: str | None = None
        self.last_args: tuple = ()

    async def fetchrow(self, sql: str, *args):
        self.last_sql = sql
        self.last_args = args
        return self._row

    async def fetch(self, sql: str, *args):
        self.last_sql = sql
        self.last_args = args
        return self._rows


# ── M0: module + symbol importability ───────────────────────────────────────


def test_DISPATCH_MCP_M0_queries_module_importable() -> None:
    """orchestrator.dispatch_mcp.queries must be importable with no side effects."""
    import orchestrator.dispatch_mcp.queries  # noqa: F401


def test_DISPATCH_MCP_M0_fetch_req_state_callable() -> None:
    """queries must expose fetch_req_state callable."""
    from orchestrator.dispatch_mcp.queries import fetch_req_state

    assert callable(fetch_req_state)


def test_DISPATCH_MCP_M0_fetch_reqs_callable() -> None:
    """queries must expose fetch_reqs callable."""
    from orchestrator.dispatch_mcp.queries import fetch_reqs

    assert callable(fetch_reqs)


def test_DISPATCH_MCP_M0_server_module_importable() -> None:
    """orchestrator.dispatch_mcp.server must be importable (FastMCP instance creation)."""
    import orchestrator.dispatch_mcp.server  # noqa: F401


def test_DISPATCH_MCP_M0_main_module_importable() -> None:
    """orchestrator.dispatch_mcp.__main__ must be importable without triggering run_stdio()."""
    import orchestrator.dispatch_mcp.__main__  # noqa: F401


# ── S1 helpers ───────────────────────────────────────────────────────────────


def _s1_row() -> dict:
    return {
        "req_id": "REQ-x",
        "project_id": "p1",
        "state": "analyzing",
        "created_at": "2026-04-26T10:00:00Z",
        "updated_at": "2026-04-26T10:00:00Z",
        "last_event": "analyzing",
        "context": {"a": 1, "b": 2},
        "history": [{"to": "analyzing", "ts": "2026-04-26T10:00:00Z"}],
    }


# ── S1: correct shape for existing REQ ───────────────────────────────────────


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S1_result_not_none() -> None:
    """fetch_req_state must return a non-None dict for an existing REQ row."""
    from orchestrator.dispatch_mcp.queries import fetch_req_state

    pool = _FakePool(row=_s1_row())
    result = await fetch_req_state(pool, "REQ-x")

    assert result is not None, "Expected dict for existing REQ, got None"


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S1_all_required_keys_present() -> None:
    """fetch_req_state result must contain all 7 required keys from spec."""
    from orchestrator.dispatch_mcp.queries import fetch_req_state

    pool = _FakePool(row=_s1_row())
    result = await fetch_req_state(pool, "REQ-x")

    assert result is not None
    required = {"req_id", "project_id", "state", "created_at", "updated_at", "last_event", "context_keys"}
    missing = required - set(result.keys())
    assert not missing, f"Result missing required keys: {missing}"


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S1_state_is_analyzing() -> None:
    """fetch_req_state result must have state == 'analyzing'."""
    from orchestrator.dispatch_mcp.queries import fetch_req_state

    pool = _FakePool(row=_s1_row())
    result = await fetch_req_state(pool, "REQ-x")

    assert result is not None
    assert result["state"] == "analyzing", (
        f"Expected state='analyzing', got {result['state']!r}"
    )


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S1_context_keys_are_a_and_b() -> None:
    """fetch_req_state result must have sorted(context_keys) == ['a', 'b']."""
    from orchestrator.dispatch_mcp.queries import fetch_req_state

    pool = _FakePool(row=_s1_row())
    result = await fetch_req_state(pool, "REQ-x")

    assert result is not None
    assert sorted(result["context_keys"]) == ["a", "b"], (
        f"Expected sorted context_keys=['a','b'], got {sorted(result['context_keys'])!r}"
    )


# ── S2: None for missing REQ ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S2_returns_none_for_missing_req() -> None:
    """fetch_req_state returns None when pool returns no row."""
    from orchestrator.dispatch_mcp.queries import fetch_req_state

    pool = _FakePool(row=None)
    result = await fetch_req_state(pool, "REQ-missing")

    assert result is None, f"Expected None for missing REQ, got {result!r}"


# ── S3: limit clamping ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S3_limit_999_clamped_to_200() -> None:
    """fetch_reqs(limit=999) must pass SQL LIMIT param = 200."""
    from orchestrator.dispatch_mcp.queries import fetch_reqs

    pool = _FakePool()
    await fetch_reqs(pool, state=None, limit=999)

    assert 200 in pool.last_args, (
        f"Expected SQL LIMIT param=200 in pool args, got args={pool.last_args}"
    )


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S3_limit_0_clamped_to_1() -> None:
    """fetch_reqs(limit=0) must pass SQL LIMIT param = 1."""
    from orchestrator.dispatch_mcp.queries import fetch_reqs

    pool = _FakePool()
    await fetch_reqs(pool, state=None, limit=0)

    assert 1 in pool.last_args, (
        f"Expected SQL LIMIT param=1 in pool args, got args={pool.last_args}"
    )


# ── S4: state filter bound as $N param ───────────────────────────────────────


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S4_state_value_bound_as_sql_param() -> None:
    """fetch_reqs(state='analyzing') must bind 'analyzing' as a $N SQL parameter."""
    from orchestrator.dispatch_mcp.queries import fetch_reqs

    pool = _FakePool()
    await fetch_reqs(pool, state="analyzing", limit=50)

    assert "analyzing" in pool.last_args, (
        f"Expected bound param 'analyzing' in SQL args, got {pool.last_args}"
    )


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S4_sql_uses_dollar_placeholder() -> None:
    """SQL with state filter must use $N parameterized placeholder, not string interpolation."""
    from orchestrator.dispatch_mcp.queries import fetch_reqs

    pool = _FakePool()
    await fetch_reqs(pool, state="analyzing", limit=50)

    assert pool.last_sql is not None
    assert "$" in pool.last_sql, (
        f"SQL must use $N params (not interpolation), got SQL: {pool.last_sql!r}"
    )


# ── S5: ValueError on unknown state ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S5_unknown_state_raises_value_error() -> None:
    """fetch_reqs with unknown state must raise ValueError before touching pool."""
    from orchestrator.dispatch_mcp.queries import fetch_reqs

    pool = _FakePool()
    with pytest.raises(ValueError):
        await fetch_reqs(pool, state="banana", limit=50)


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S5_error_message_contains_unknown_state() -> None:
    """ValueError message must contain the substring 'unknown state'."""
    from orchestrator.dispatch_mcp.queries import fetch_reqs

    pool = _FakePool()
    with pytest.raises(ValueError) as exc_info:
        await fetch_reqs(pool, state="banana", limit=50)

    msg = str(exc_info.value)
    assert "unknown state" in msg, (
        f"ValueError must contain 'unknown state', got: {msg!r}"
    )


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S5_error_lists_valid_state_values() -> None:
    """ValueError message must include a list of valid ReqState values."""
    from orchestrator.dispatch_mcp.queries import fetch_reqs

    pool = _FakePool()
    with pytest.raises(ValueError) as exc_info:
        await fetch_reqs(pool, state="banana", limit=50)

    msg = str(exc_info.value)
    known_valid = ["analyzing", "done", "escalated"]
    found = any(v in msg for v in known_valid)
    assert found, (
        f"ValueError must list valid state values (e.g. 'analyzing', 'done', 'escalated'), "
        f"got: {msg!r}"
    )


# ── S6: context redacted to keys-only ────────────────────────────────────────


def _s6_row() -> dict:
    return {
        "req_id": "REQ-redact",
        "project_id": "p1",
        "state": "analyzing",
        "created_at": "2026-04-26T10:00:00Z",
        "updated_at": "2026-04-26T10:00:00Z",
        "last_event": "analyzing",
        "context": {"prompt": "<long secret text>", "intent": {"k": "v"}},
        "history": [],
    }


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S6_context_keys_contains_correct_keys() -> None:
    """context_keys must equal ['intent', 'prompt'] in any order."""
    from orchestrator.dispatch_mcp.queries import fetch_req_state

    pool = _FakePool(row=_s6_row())
    result = await fetch_req_state(pool, "REQ-redact")

    assert result is not None
    assert "context_keys" in result, "Result must contain 'context_keys'"
    assert sorted(result["context_keys"]) == sorted(["prompt", "intent"]), (
        f"Expected context_keys=['intent','prompt'] (any order), "
        f"got {sorted(result['context_keys'])!r}"
    )


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S6_no_top_level_context_field() -> None:
    """Result must NOT contain a top-level 'context' field."""
    from orchestrator.dispatch_mcp.queries import fetch_req_state

    pool = _FakePool(row=_s6_row())
    result = await fetch_req_state(pool, "REQ-redact")

    assert result is not None
    assert "context" not in result, (
        f"Result must NOT expose top-level 'context', got keys: {list(result.keys())}"
    )


@pytest.mark.asyncio
async def test_DISPATCH_MCP_S6_context_body_values_not_leaked() -> None:
    """Result must NOT contain any string value from the original context body."""
    from orchestrator.dispatch_mcp.queries import fetch_req_state

    secret = "<long secret text>"
    pool = _FakePool(row=_s6_row())
    result = await fetch_req_state(pool, "REQ-redact")

    assert result is not None
    result_repr = str(result)
    assert secret not in result_repr, (
        f"Secret context value must not appear anywhere in result, got: {result_repr!r}"
    )
