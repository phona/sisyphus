"""DB-read helpers backing the dispatch_mcp tools.

Pure async functions; no FastMCP / MCP SDK coupling so unit tests can
exercise them against a fake asyncpg pool the same way as
`tests/test_admission.py`.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import asyncpg

from ..state import ReqState

LIST_LIMIT_MIN = 1
LIST_LIMIT_MAX = 200
LIST_LIMIT_DEFAULT = 50


def _decode_jsonb(value: Any) -> Any:
    """asyncpg returns jsonb as either str (default) or already-parsed
    object (when a custom codec is registered). Match the same dual
    handling store/req_state.py uses."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _serialise_dt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _last_event(history: Any) -> dict | None:
    """Last entry of req_state.history (chronological append-only list).
    Returns the raw dict or None if history is empty / missing."""
    decoded = _decode_jsonb(history)
    if isinstance(decoded, list) and decoded:
        last = decoded[-1]
        if isinstance(last, dict):
            return last
    return None


def _context_keys(context: Any) -> list[str]:
    decoded = _decode_jsonb(context)
    if isinstance(decoded, dict):
        return list(decoded.keys())
    return []


async def fetch_req_state(pool: asyncpg.Pool, req_id: str) -> dict | None:
    """Return a JSON-shaped projection of req_state for `req_id`, or
    None if the row doesn't exist.

    Top-level `context` is intentionally redacted to `context_keys`
    (top-level keys only) — see design.md "Why redact context to
    keys-only".
    """
    row = await pool.fetchrow(
        "SELECT req_id, project_id, state, history, context, "
        "created_at, updated_at "
        "FROM req_state WHERE req_id = $1",
        req_id,
    )
    if row is None:
        return None
    return {
        "req_id": row["req_id"],
        "project_id": row["project_id"],
        "state": row["state"],
        "created_at": _serialise_dt(row["created_at"]),
        "updated_at": _serialise_dt(row["updated_at"]),
        "last_event": _last_event(row["history"]),
        "context_keys": _context_keys(row["context"]),
    }


def _clamp_limit(limit: int) -> int:
    if limit < LIST_LIMIT_MIN:
        return LIST_LIMIT_MIN
    if limit > LIST_LIMIT_MAX:
        return LIST_LIMIT_MAX
    return limit


def _validate_state(state: str | None) -> str | None:
    if state is None:
        return None
    valid = {s.value for s in ReqState}
    if state not in valid:
        raise ValueError(
            f"unknown state {state!r}; expected one of "
            f"{sorted(valid)}"
        )
    return state


async def fetch_reqs(
    pool: asyncpg.Pool,
    *,
    state: str | None = None,
    limit: int = LIST_LIMIT_DEFAULT,
) -> list[dict]:
    """List most-recently-updated REQs, optionally filtered by state.

    `limit` is clamped server-side to `[1, 200]` so a malformed client
    can't exfiltrate the whole table in one shot. `state` is
    validated against `ReqState`; an unknown value raises
    `ValueError`.
    """
    validated_state = _validate_state(state)
    bound_limit = _clamp_limit(limit)

    if validated_state is None:
        rows = await pool.fetch(
            "SELECT req_id, project_id, state, updated_at "
            "FROM req_state "
            "ORDER BY updated_at DESC "
            "LIMIT $1",
            bound_limit,
        )
    else:
        rows = await pool.fetch(
            "SELECT req_id, project_id, state, updated_at "
            "FROM req_state "
            "WHERE state = $1 "
            "ORDER BY updated_at DESC "
            "LIMIT $2",
            validated_state,
            bound_limit,
        )
    return [
        {
            "req_id": r["req_id"],
            "project_id": r["project_id"],
            "state": r["state"],
            "updated_at": _serialise_dt(r["updated_at"]),
        }
        for r in rows
    ]
