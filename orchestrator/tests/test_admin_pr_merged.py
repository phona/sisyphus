"""Unit tests: POST /admin/req/{req_id}/pr-merged endpoint.

REQ-pr-merge-archive-hook-1777344443

Scenarios:
  PMH-S1  state=pending-user-review → update_context + engine.step(PR_MERGED) called
  PMH-S2  state=review-running      → update_context + engine.step(PR_MERGED) called
  PMH-S3  state=pr-ci-running       → update_context + engine.step(PR_MERGED) called
  PMH-S4  state=done                → 200 noop, no DB write, no engine.step
  PMH-S5  state=escalated           → 200 noop, no DB write, no engine.step
  PMH-S6  state=analyzing           → 409 with valid-states hint
  PMH-S7  no/bad Bearer token       → 401
  PMH-S8  REQ not found             → 404
  PMH-S9  ctx written: merged_pr_url / merged_sha / merged_at / pr_merged_trigger
  PMH-S10 result shape: action=pr_merged, from_state, result
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from orchestrator.admin import PrMergedBody, pr_merged
from orchestrator.state import Event, ReqState

# ─── shared fixtures ──────────────────────────────────────────────────────────


@dataclass
class _Row:
    req_id: str
    project_id: str
    state: ReqState
    context: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class _FakePool:
    def __init__(self):
        self.executed: list = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        return "UPDATE 1"


_GOOD_BODY = PrMergedBody(
    merged_pr_url="https://github.com/phona/sisyphus/pull/99",
    merged_sha="abc1234",
    merged_at="2026-04-28T10:00:00Z",
)


def _setup(monkeypatch, *, state: ReqState, rows: list[_Row] | None = None):
    """Wire monkeypatch: bypass auth, fake pool, cycling req_state.get, capture update_context."""
    from orchestrator import admin as admin_mod

    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    pool = _FakePool()
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)

    base_row = _Row(req_id="REQ-X", project_id="proj", state=state)
    _rows = rows if rows is not None else [base_row, base_row]
    call_count = {"n": 0}

    async def _get(_pool, _req_id):
        idx = min(call_count["n"], len(_rows) - 1)
        call_count["n"] += 1
        return _rows[idx]

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)

    update_calls: list[dict] = []

    async def _update(_pool, _req_id, patch):
        update_calls.append(dict(patch))

    monkeypatch.setattr("orchestrator.admin.req_state.update_context", _update)

    step_calls: list[dict] = []

    async def _step(*a, **kw):
        step_calls.append(kw)
        return {"action": "done_archive", "next_state": "archiving"}

    monkeypatch.setattr("orchestrator.admin.engine.step", _step)

    return pool, update_calls, step_calls


# ─── PMH-S1/S2/S3: valid states trigger PR_MERGED ────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_state", [
    ReqState.PENDING_USER_REVIEW,
    ReqState.REVIEW_RUNNING,
    ReqState.PR_CI_RUNNING,
])
async def test_pmh_valid_state_emits_pr_merged(monkeypatch, valid_state):
    """PMH-S1/S2/S3: state ∈ valid set → update_context called + engine.step with PR_MERGED."""
    _pool, update_calls, step_calls = _setup(monkeypatch, state=valid_state)

    result = await pr_merged("REQ-X", body=_GOOD_BODY, authorization="Bearer tok")

    # engine.step called exactly once with PR_MERGED event
    assert len(step_calls) == 1, f"PMH: engine.step MUST be called once, got {step_calls}"
    assert step_calls[0]["event"] == Event.PR_MERGED, (
        f"PMH: event MUST be PR_MERGED, got {step_calls[0]['event']}"
    )
    assert step_calls[0]["cur_state"] == valid_state

    # update_context called with merge metadata
    assert len(update_calls) == 1
    patch = update_calls[0]
    assert patch["merged_pr_url"] == _GOOD_BODY.merged_pr_url
    assert patch["merged_sha"] == _GOOD_BODY.merged_sha
    assert patch["merged_at"] == _GOOD_BODY.merged_at
    assert patch["pr_merged_trigger"] == "gha-hook"

    # result shape
    assert result["action"] == "pr_merged"
    assert result["from_state"] == valid_state.value
    assert "result" in result


# ─── PMH-S4/S5: noop states ──────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("noop_state", [ReqState.DONE, ReqState.ESCALATED])
async def test_pmh_noop_for_terminal_states(monkeypatch, noop_state):
    """PMH-S4/S5: state ∈ {done, escalated} → 200 noop, no DB write, no engine.step."""
    _pool, update_calls, step_calls = _setup(monkeypatch, state=noop_state)

    result = await pr_merged("REQ-X", body=_GOOD_BODY, authorization="Bearer tok")

    assert result["action"] == "noop"
    assert result["state"] == noop_state.value
    assert step_calls == [], "PMH-S4/5: engine.step MUST NOT be called on noop"
    assert update_calls == [], "PMH-S4/5: update_context MUST NOT be called on noop"


# ─── PMH-S6: invalid state → 409 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pmh_409_for_unexpected_state(monkeypatch):
    """PMH-S6: state=analyzing → 409 with valid-states hint."""
    _pool, update_calls, step_calls = _setup(monkeypatch, state=ReqState.ANALYZING)

    with pytest.raises(HTTPException) as ei:
        await pr_merged("REQ-X", body=_GOOD_BODY, authorization="Bearer tok")

    assert ei.value.status_code == 409
    assert "analyzing" in ei.value.detail
    assert step_calls == []
    assert update_calls == []


# ─── PMH-S7: auth ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pmh_401_bad_token(monkeypatch):
    """PMH-S7: bad/missing token → 401, no DB access."""
    from orchestrator import admin as admin_mod

    def _bad(_):
        raise HTTPException(status_code=401, detail="bad token")

    monkeypatch.setattr(admin_mod, "_verify_token", _bad)

    get_calls: list = []

    async def _get(*a, **kw):
        get_calls.append(1)

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: _FakePool())

    with pytest.raises(HTTPException) as ei:
        await pr_merged("REQ-X", body=_GOOD_BODY, authorization=None)

    assert ei.value.status_code == 401
    assert get_calls == [], "PMH-S7: req_state.get MUST NOT be called on auth failure"


# ─── PMH-S8: 404 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pmh_404_when_req_not_found(monkeypatch):
    """PMH-S8: REQ not found → 404."""
    from orchestrator import admin as admin_mod

    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: _FakePool())

    async def _get(_pool, _req_id):
        return None

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)

    with pytest.raises(HTTPException) as ei:
        await pr_merged("REQ-MISSING", body=_GOOD_BODY, authorization="Bearer tok")

    assert ei.value.status_code == 404
    assert "not found" in ei.value.detail


# ─── PMH-S9: ctx fields ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pmh_ctx_contains_all_merge_fields(monkeypatch):
    """PMH-S9: context patch contains merged_pr_url, merged_sha, merged_at, pr_merged_trigger."""
    _pool, update_calls, _step_calls = _setup(
        monkeypatch, state=ReqState.PENDING_USER_REVIEW
    )

    body = PrMergedBody(
        merged_pr_url="https://github.com/org/repo/pull/42",
        merged_sha="deadbeef",
        merged_at="2026-04-28T12:34:56Z",
    )
    await pr_merged("REQ-X", body=body, authorization="Bearer tok")

    assert len(update_calls) == 1
    patch = update_calls[0]
    assert patch["merged_pr_url"] == "https://github.com/org/repo/pull/42"
    assert patch["merged_sha"] == "deadbeef"
    assert patch["merged_at"] == "2026-04-28T12:34:56Z"
    assert patch["pr_merged_trigger"] == "gha-hook"


# ─── PMH-S10: route registered ────────────────────────────────────────────────


def test_pmh_route_registered():
    """PMH-S10: /admin/req/{req_id}/pr-merged POST route is registered."""
    from orchestrator.admin import admin as admin_router
    from orchestrator.admin import pr_merged as endpoint_fn

    paths_to_endpoint = {
        r.path: r.endpoint
        for r in admin_router.routes
        if hasattr(r, "path") and "POST" in (getattr(r, "methods", set()) or set())
    }
    assert "/admin/req/{req_id}/pr-merged" in paths_to_endpoint, (
        "PMH-S10: route /admin/req/{req_id}/pr-merged MUST be registered"
    )
    assert paths_to_endpoint["/admin/req/{req_id}/pr-merged"] is endpoint_fn


# ─── PMH state machine: PR_MERGED event registered ───────────────────────────


def test_pr_merged_event_in_state_machine():
    """PR_MERGED event MUST have transitions for all three valid states."""
    from orchestrator.state import TRANSITIONS, Event, ReqState

    expected = [
        (ReqState.PENDING_USER_REVIEW, Event.PR_MERGED),
        (ReqState.REVIEW_RUNNING, Event.PR_MERGED),
        (ReqState.PR_CI_RUNNING, Event.PR_MERGED),
    ]
    for key in expected:
        assert key in TRANSITIONS, (
            f"TRANSITIONS must contain {key}; PR_MERGED event not wired"
        )
        t = TRANSITIONS[key]
        assert t.next_state == ReqState.ARCHIVING, (
            f"{key}: next_state MUST be ARCHIVING, got {t.next_state}"
        )
        assert t.action == "done_archive", (
            f"{key}: action MUST be 'done_archive', got {t.action}"
        )
