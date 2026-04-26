"""Contract tests for cleanup-runner-zombie (REQ-cleanup-runner-zombie-1777170378).

Black-box challenger. Does NOT read admin.py. Derived from:
  openspec/changes/REQ-cleanup-runner-zombie-1777170378/specs/cleanup-runner-zombie/spec.md

Scenarios:
  FRE-S1  force_escalate on in-flight REQ → 200 force_escalated + cleanup task scheduled
  FRE-S2  force_escalate on already-escalated REQ → 200 noop, no SQL UPDATE, no cleanup
  FRE-S3  unknown REQ → 404 with 'not found' in detail, no SQL UPDATE, no cleanup
  FRE-S4  cleanup task MUST be called with terminal_state=ReqState.ESCALATED (→ retain_pvc=True)

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

_TOKEN = "test-webhook-token"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}


def _row(state: str, req_id: str = "REQ-X") -> dict:
    """Minimal asyncpg-compatible row dict for req_state.get to parse."""
    return {
        "req_id": req_id,
        "project_id": "test-proj",
        "state": state,
        "history": "[]",
        "context": "{}",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


class _FakePool:
    """Minimal asyncpg pool stub: returns preset fetchrow value, records execute calls."""

    def __init__(self, fetchrow_return=None):
        self._fetchrow_return = fetchrow_return
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, sql, *args):
        return self._fetchrow_return

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))


# ─── FRE-S1 ──────────────────────────────────────────────────────────────────


async def test_fre_s1_schedules_cleanup_task_on_active_req(monkeypatch):
    """FRE-S1: POST /admin/req/REQ-X/escalate with state='analyzing'
    MUST return 200 {action='force_escalated', from_state='analyzing'} and schedule
    a fire-and-forget asyncio.Task calling engine._cleanup_runner_on_terminal with
    ReqState.ESCALATED before the endpoint returns.
    """
    from httpx import ASGITransport, AsyncClient

    from orchestrator import engine as engine_mod
    from orchestrator.main import app
    from orchestrator.state import ReqState
    from orchestrator.store import db

    pool = _FakePool(fetchrow_return=_row("analyzing"))
    cleanup_calls: list[dict] = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append({"req_id": req_id, "terminal_state": terminal_state})

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(engine_mod, "_cleanup_runner_on_terminal", _fake_cleanup)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/req/REQ-X/escalate", headers=_AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("action") == "force_escalated", f"expected action=force_escalated, got {body}"
    assert body.get("from_state") == "analyzing", f"expected from_state=analyzing, got {body}"

    # Allow fire-and-forget asyncio.Task one event-loop tick to execute
    await asyncio.sleep(0)
    assert len(cleanup_calls) == 1, (
        f"cleanup must be scheduled exactly once; got {len(cleanup_calls)} calls"
    )
    assert cleanup_calls[0]["req_id"] == "REQ-X"
    assert cleanup_calls[0]["terminal_state"] == ReqState.ESCALATED, (
        f"cleanup must receive ReqState.ESCALATED, got {cleanup_calls[0]['terminal_state']}"
    )


# ─── FRE-S2 ──────────────────────────────────────────────────────────────────


async def test_fre_s2_noop_on_already_escalated(monkeypatch):
    """FRE-S2: POST /admin/req/REQ-X/escalate with state='escalated'
    MUST return 200 {action='noop', state='already escalated'} and
    MUST NOT execute any SQL UPDATE and MUST NOT schedule a cleanup task.
    """
    from httpx import ASGITransport, AsyncClient

    from orchestrator import engine as engine_mod
    from orchestrator.main import app
    from orchestrator.store import db

    pool = _FakePool(fetchrow_return=_row("escalated"))
    cleanup_calls: list[dict] = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append({"req_id": req_id, "terminal_state": terminal_state})

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(engine_mod, "_cleanup_runner_on_terminal", _fake_cleanup)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/admin/req/REQ-X/escalate", headers=_AUTH)

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("action") == "noop", f"expected action=noop, got {body}"
    assert "already escalated" in str(body.get("state", "")), (
        f"expected state to contain 'already escalated', got {body}"
    )

    assert len(pool.execute_calls) == 0, (
        f"no SQL UPDATE must be issued on noop path; got {pool.execute_calls}"
    )
    await asyncio.sleep(0)
    assert len(cleanup_calls) == 0, (
        f"no cleanup task must be scheduled on noop path; got {cleanup_calls}"
    )


# ─── FRE-S3 ──────────────────────────────────────────────────────────────────


async def test_fre_s3_unknown_req_returns_404_no_side_effects(monkeypatch):
    """FRE-S3: POST /admin/req/REQ-DOES-NOT-EXIST/escalate when no req_state row exists
    MUST return 404 with detail containing 'not found' (case-insensitive).
    MUST NOT execute any SQL UPDATE and MUST NOT schedule a cleanup task.
    """
    from httpx import ASGITransport, AsyncClient

    from orchestrator import engine as engine_mod
    from orchestrator.main import app
    from orchestrator.store import db

    pool = _FakePool(fetchrow_return=None)  # no row → req unknown
    cleanup_calls: list[dict] = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append({"req_id": req_id, "terminal_state": terminal_state})

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(engine_mod, "_cleanup_runner_on_terminal", _fake_cleanup)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/admin/req/REQ-DOES-NOT-EXIST/escalate", headers=_AUTH
        )

    assert resp.status_code == 404, f"expected 404, got {resp.status_code}: {resp.text}"
    body = resp.json()
    detail = str(body.get("detail", "")).lower()
    assert "not found" in detail, (
        f"404 detail must contain 'not found'; got detail={body.get('detail')!r}"
    )

    assert len(pool.execute_calls) == 0, (
        f"no SQL UPDATE must be issued before 404; got {pool.execute_calls}"
    )
    await asyncio.sleep(0)
    assert len(cleanup_calls) == 0, (
        f"no cleanup task must be scheduled on 404 path; got {cleanup_calls}"
    )


# ─── FRE-S4 ──────────────────────────────────────────────────────────────────


async def test_fre_s4_cleanup_arg_is_escalated_not_done(monkeypatch):
    """FRE-S4: The cleanup task scheduled by force_escalate MUST pass
    terminal_state=ReqState.ESCALATED (not ReqState.DONE), so that
    _cleanup_runner_on_terminal derives retain_pvc=True and keeps the PVC
    for the human-debug retention window.
    """
    from httpx import ASGITransport, AsyncClient

    from orchestrator import engine as engine_mod
    from orchestrator.main import app
    from orchestrator.state import ReqState
    from orchestrator.store import db

    pool = _FakePool(fetchrow_return=_row("analyzing"))
    terminal_state_args: list[ReqState] = []

    async def _fake_cleanup(req_id, terminal_state):
        terminal_state_args.append(terminal_state)

    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(engine_mod, "_cleanup_runner_on_terminal", _fake_cleanup)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/admin/req/REQ-X/escalate", headers=_AUTH)

    await asyncio.sleep(0)
    assert len(terminal_state_args) == 1, (
        "cleanup_runner_on_terminal must be called exactly once"
    )
    assert terminal_state_args[0] == ReqState.ESCALATED, (
        f"retain_pvc=True requires terminal_state=ReqState.ESCALATED; "
        f"got {terminal_state_args[0]!r}. Passing ReqState.DONE would delete the PVC."
    )
    assert terminal_state_args[0] != ReqState.DONE, (
        "MUST NOT pass ReqState.DONE — that would delete the PVC, "
        "breaking the human-debug retention window"
    )
