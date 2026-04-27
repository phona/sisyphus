"""Challenger contract tests for REQ-test-accept-phase-1777267654.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-test-accept-phase-1777267654/specs/accept-phase-tests/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  APT-S1  (ACCEPT_RUNNING, ACCEPT_PASS) → teardown → ARCHIVING; done_archive called; no cleanup
  APT-S2  (ACCEPT_RUNNING, ACCEPT_FAIL) → teardown → REVIEW_RUNNING; verifier called; no cleanup
  APT-S3  (ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL) → ESCALATED; escalate called; cleanup_runner retain_pvc=True once
  APT-S4  (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) → ARCHIVING; done_archive called; no cleanup
  APT-S5  (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) → REVIEW_RUNNING; verifier called; no cleanup
  APT-S6  (ACCEPT_RUNNING, SESSION_FAILED) → self-loop ACCEPT_RUNNING; escalate called; no cleanup
  APT-S7  (ACCEPT_TEARING_DOWN, SESSION_FAILED) → self-loop ACCEPT_TEARING_DOWN; escalate called; no cleanup

Module contract under test: orchestrator.engine.step
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

# ─── Shared helpers ───────────────────────────────────────────────────────────

_REQ_ID = "REQ-test-accept-phase-1777267654"
_PROJECT = "proj-apt"
_TAGS: list[str] = [_REQ_ID]
_POOL = object()  # sentinel; all DB calls are patched away


class _Body:
    issueId = "bkd-apt-test"
    projectId = _PROJECT


def _patch_io(monkeypatch) -> tuple[AsyncMock, AsyncMock]:
    """Patch all engine external I/O. Returns (cas_mock, get_mock)."""
    from orchestrator import engine

    cas = AsyncMock(return_value=True)
    monkeypatch.setattr(engine.req_state, "cas_transition", cas)
    monkeypatch.setattr(engine.req_state, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(engine.stage_runs, "close_latest_stage_run", AsyncMock())
    monkeypatch.setattr(engine.stage_runs, "insert_stage_run", AsyncMock())
    monkeypatch.setattr(engine.obs, "record_event", AsyncMock())
    return cas


class _FakeController:
    """Fake k8s_runner controller that records cleanup_runner calls."""

    def __init__(self):
        self.cleanup_calls: list[tuple] = []

    async def cleanup_runner(self, req_id, *, retain_pvc=False):
        self.cleanup_calls.append((req_id, retain_pvc))


@pytest.fixture(autouse=True)
def _restore_registry():
    """Snapshot and restore REGISTRY around each test to prevent pollution."""
    from orchestrator.actions import REGISTRY

    snapshot = dict(REGISTRY)
    yield
    REGISTRY.clear()
    REGISTRY.update(snapshot)


async def _step(**overrides):
    """Call engine.step with accept-phase defaults, overrideable per test."""
    from orchestrator.engine import step
    from orchestrator.state import Event, ReqState

    defaults: dict = dict(
        pool=_POOL,
        body=_Body(),
        req_id=_REQ_ID,
        project_id=_PROJECT,
        tags=_TAGS,
        cur_state=ReqState.ACCEPT_RUNNING,
        ctx={},
        event=Event.ACCEPT_PASS,
        depth=0,
    )
    defaults.update(overrides)
    return await step(**defaults)


# ─── APT-S1: accept.pass → teardown → archiving ──────────────────────────────


async def test_apt_s1_accept_pass_chains_through_teardown_to_archiving(
    monkeypatch,
) -> None:
    """
    APT-S1: (ACCEPT_RUNNING, ACCEPT_PASS) + teardown_accept_env emits teardown-done.pass
    MUST chain into (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) → ARCHIVING;
    done_archive MUST be called exactly once; cleanup_runner MUST NOT be awaited.
    """
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

    teardown_calls: list[int] = []
    archive_calls: list[int] = []
    controller = _FakeController()
    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: controller)

    # CAS must succeed for the chained transition too
    monkeypatch.setattr(engine.req_state, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(
        engine.req_state,
        "get",
        AsyncMock(return_value=type("Row", (), {"state": ReqState.ACCEPT_TEARING_DOWN, "context": {}})()),
    )

    async def _teardown(**_kw):
        teardown_calls.append(1)
        return {"emit": "teardown-done.pass"}

    async def _archive(**_kw):
        archive_calls.append(1)
        return {"ok": True}

    REGISTRY["teardown_accept_env"] = _teardown
    REGISTRY["done_archive"] = _archive

    result = await _step(
        cur_state=ReqState.ACCEPT_RUNNING,
        event=Event.ACCEPT_PASS,
    )

    # Drain any fire-and-forget tasks
    await asyncio.sleep(0)

    assert result.get("action") == "teardown_accept_env", (
        f"APT-S1: base action MUST be 'teardown_accept_env'; got {result!r}"
    )
    chained = result.get("chained")
    assert chained is not None, (
        f"APT-S1: result MUST contain 'chained' key for emitted teardown-done.pass; got {result!r}"
    )
    assert chained.get("action") == "done_archive", (
        f"APT-S1: chained action MUST be 'done_archive'; got {chained!r}"
    )
    assert chained.get("next_state") == ReqState.ARCHIVING.value, (
        f"APT-S1: chained next_state MUST be 'archiving'; got {chained!r}"
    )
    assert teardown_calls == [1], (
        f"APT-S1: teardown_accept_env MUST be called exactly once; got {len(teardown_calls)} call(s)"
    )
    assert archive_calls == [1], (
        f"APT-S1: done_archive MUST be called exactly once; got {len(archive_calls)} call(s)"
    )
    assert controller.cleanup_calls == [], (
        f"APT-S1: cleanup_runner MUST NOT be awaited on non-terminal path; "
        f"got {controller.cleanup_calls}"
    )


# ─── APT-S2: accept.fail → teardown → verifier ───────────────────────────────


async def test_apt_s2_accept_fail_chains_through_teardown_to_verifier(
    monkeypatch,
) -> None:
    """
    APT-S2: (ACCEPT_RUNNING, ACCEPT_FAIL) + teardown_accept_env emits teardown-done.fail
    MUST chain into (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) → REVIEW_RUNNING;
    invoke_verifier_for_accept_fail MUST be called exactly once; cleanup_runner MUST NOT be awaited.
    """
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

    teardown_calls: list[int] = []
    verifier_calls: list[int] = []
    controller = _FakeController()
    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: controller)

    monkeypatch.setattr(engine.req_state, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(
        engine.req_state,
        "get",
        AsyncMock(return_value=type("Row", (), {"state": ReqState.ACCEPT_TEARING_DOWN, "context": {}})()),
    )

    async def _teardown(**_kw):
        teardown_calls.append(1)
        return {"emit": "teardown-done.fail"}

    async def _verifier(**_kw):
        verifier_calls.append(1)
        return {"ok": True}

    REGISTRY["teardown_accept_env"] = _teardown
    REGISTRY["invoke_verifier_for_accept_fail"] = _verifier

    result = await _step(
        cur_state=ReqState.ACCEPT_RUNNING,
        event=Event.ACCEPT_FAIL,
    )

    await asyncio.sleep(0)

    assert result.get("action") == "teardown_accept_env", (
        f"APT-S2: base action MUST be 'teardown_accept_env'; got {result!r}"
    )
    chained = result.get("chained")
    assert chained is not None, (
        f"APT-S2: result MUST contain 'chained' key for emitted teardown-done.fail; got {result!r}"
    )
    assert chained.get("action") == "invoke_verifier_for_accept_fail", (
        f"APT-S2: chained action MUST be 'invoke_verifier_for_accept_fail'; got {chained!r}"
    )
    assert chained.get("next_state") == ReqState.REVIEW_RUNNING.value, (
        f"APT-S2: chained next_state MUST be 'review-running'; got {chained!r}"
    )
    assert teardown_calls == [1], (
        f"APT-S2: teardown_accept_env MUST be called exactly once; got {len(teardown_calls)} call(s)"
    )
    assert verifier_calls == [1], (
        f"APT-S2: invoke_verifier_for_accept_fail MUST be called exactly once; got {len(verifier_calls)} call(s)"
    )
    assert controller.cleanup_calls == [], (
        f"APT-S2: cleanup_runner MUST NOT be awaited on non-terminal path; "
        f"got {controller.cleanup_calls}"
    )


# ─── APT-S3: accept-env-up.fail → ESCALATED + cleanup retain_pvc ─────────────


async def test_apt_s3_accept_env_up_fail_escalates_and_cleans_up(
    monkeypatch,
) -> None:
    """
    APT-S3: (ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL) → ESCALATED;
    escalate MUST be called exactly once;
    cleanup_runner(req_id, retain_pvc=True) MUST be awaited exactly once.
    """
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

    escalate_calls: list[int] = []
    controller = _FakeController()
    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: controller)

    async def _escalate(**_kw):
        escalate_calls.append(1)
        return {"escalated": True}

    REGISTRY["escalate"] = _escalate

    result = await _step(
        cur_state=ReqState.ACCEPT_RUNNING,
        event=Event.ACCEPT_ENV_UP_FAIL,
    )

    # Drain fire-and-forget cleanup task
    await asyncio.sleep(0)

    assert result.get("action") == "escalate", (
        f"APT-S3: action MUST be 'escalate'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ESCALATED.value, (
        f"APT-S3: next_state MUST be 'escalated'; got {result!r}"
    )
    assert escalate_calls == [1], (
        f"APT-S3: escalate MUST be called exactly once; got {len(escalate_calls)} call(s)"
    )
    assert len(controller.cleanup_calls) == 1, (
        f"APT-S3: cleanup_runner MUST be awaited exactly once; "
        f"got {len(controller.cleanup_calls)} call(s): {controller.cleanup_calls}"
    )
    cleanup_req_id, cleanup_retain = controller.cleanup_calls[0]
    assert cleanup_req_id == _REQ_ID, (
        f"APT-S3: cleanup_runner req_id MUST be {_REQ_ID!r}; got {cleanup_req_id!r}"
    )
    assert cleanup_retain is True, (
        f"APT-S3: cleanup_runner MUST be called with retain_pvc=True; got {cleanup_retain!r}"
    )


# ─── APT-S4: teardown-done.pass → ARCHIVING ──────────────────────────────────


async def test_apt_s4_teardown_done_pass_advances_to_archiving(
    monkeypatch,
) -> None:
    """
    APT-S4: (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) → ARCHIVING;
    done_archive MUST be called exactly once; cleanup_runner MUST NOT be awaited.
    """
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

    archive_calls: list[int] = []
    controller = _FakeController()
    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: controller)

    async def _archive(**_kw):
        archive_calls.append(1)
        return {"ok": True}

    REGISTRY["done_archive"] = _archive

    result = await _step(
        cur_state=ReqState.ACCEPT_TEARING_DOWN,
        event=Event.TEARDOWN_DONE_PASS,
    )

    await asyncio.sleep(0)

    assert result.get("action") == "done_archive", (
        f"APT-S4: action MUST be 'done_archive'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ARCHIVING.value, (
        f"APT-S4: next_state MUST be 'archiving'; got {result!r}"
    )
    assert archive_calls == [1], (
        f"APT-S4: done_archive MUST be called exactly once; got {len(archive_calls)} call(s)"
    )
    assert controller.cleanup_calls == [], (
        f"APT-S4: cleanup_runner MUST NOT be awaited — ARCHIVING is not terminal; "
        f"got {controller.cleanup_calls}"
    )


# ─── APT-S5: teardown-done.fail → REVIEW_RUNNING ─────────────────────────────


async def test_apt_s5_teardown_done_fail_routes_to_verifier(
    monkeypatch,
) -> None:
    """
    APT-S5: (ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) → REVIEW_RUNNING;
    invoke_verifier_for_accept_fail MUST be called exactly once;
    cleanup_runner MUST NOT be awaited.
    """
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

    verifier_calls: list[int] = []
    controller = _FakeController()
    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: controller)

    async def _verifier(**_kw):
        verifier_calls.append(1)
        return {"ok": True}

    REGISTRY["invoke_verifier_for_accept_fail"] = _verifier

    result = await _step(
        cur_state=ReqState.ACCEPT_TEARING_DOWN,
        event=Event.TEARDOWN_DONE_FAIL,
    )

    await asyncio.sleep(0)

    assert result.get("action") == "invoke_verifier_for_accept_fail", (
        f"APT-S5: action MUST be 'invoke_verifier_for_accept_fail'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.REVIEW_RUNNING.value, (
        f"APT-S5: next_state MUST be 'review-running'; got {result!r}"
    )
    assert verifier_calls == [1], (
        f"APT-S5: invoke_verifier_for_accept_fail MUST be called exactly once; "
        f"got {len(verifier_calls)} call(s)"
    )
    assert controller.cleanup_calls == [], (
        f"APT-S5: cleanup_runner MUST NOT be awaited — REVIEW_RUNNING is not terminal; "
        f"got {controller.cleanup_calls}"
    )


# ─── APT-S6: session.failed at ACCEPT_RUNNING self-loops ─────────────────────


async def test_apt_s6_session_failed_at_accept_running_self_loops(
    monkeypatch,
) -> None:
    """
    APT-S6: (ACCEPT_RUNNING, SESSION_FAILED) → self-loop next_state=ACCEPT_RUNNING;
    escalate MUST be called exactly once; cleanup_runner MUST NOT be awaited.
    """
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

    escalate_calls: list[int] = []
    controller = _FakeController()
    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: controller)

    async def _escalate(**_kw):
        escalate_calls.append(1)
        return {"ok": True}

    REGISTRY["escalate"] = _escalate

    result = await _step(
        cur_state=ReqState.ACCEPT_RUNNING,
        event=Event.SESSION_FAILED,
    )

    await asyncio.sleep(0)

    assert result.get("action") == "escalate", (
        f"APT-S6: action MUST be 'escalate'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ACCEPT_RUNNING.value, (
        f"APT-S6: next_state MUST remain 'accept-running' (self-loop); got {result!r}"
    )
    assert escalate_calls == [1], (
        f"APT-S6: escalate MUST be called exactly once; got {len(escalate_calls)} call(s)"
    )
    assert controller.cleanup_calls == [], (
        f"APT-S6: cleanup_runner MUST NOT be awaited — ACCEPT_RUNNING is not terminal; "
        f"got {controller.cleanup_calls}"
    )


# ─── APT-S7: session.failed at ACCEPT_TEARING_DOWN self-loops ────────────────


async def test_apt_s7_session_failed_at_accept_tearing_down_self_loops(
    monkeypatch,
) -> None:
    """
    APT-S7: (ACCEPT_TEARING_DOWN, SESSION_FAILED) → self-loop next_state=ACCEPT_TEARING_DOWN;
    escalate MUST be called exactly once; cleanup_runner MUST NOT be awaited.
    """
    from orchestrator import engine
    from orchestrator.actions import REGISTRY
    from orchestrator.state import Event, ReqState

    _patch_io(monkeypatch)

    escalate_calls: list[int] = []
    controller = _FakeController()
    monkeypatch.setattr(engine.k8s_runner, "get_controller", lambda: controller)

    async def _escalate(**_kw):
        escalate_calls.append(1)
        return {"ok": True}

    REGISTRY["escalate"] = _escalate

    result = await _step(
        cur_state=ReqState.ACCEPT_TEARING_DOWN,
        event=Event.SESSION_FAILED,
    )

    await asyncio.sleep(0)

    assert result.get("action") == "escalate", (
        f"APT-S7: action MUST be 'escalate'; got {result!r}"
    )
    assert result.get("next_state") == ReqState.ACCEPT_TEARING_DOWN.value, (
        f"APT-S7: next_state MUST remain 'accept-tearing-down' (self-loop); got {result!r}"
    )
    assert escalate_calls == [1], (
        f"APT-S7: escalate MUST be called exactly once; got {len(escalate_calls)} call(s)"
    )
    assert controller.cleanup_calls == [], (
        f"APT-S7: cleanup_runner MUST NOT be awaited — ACCEPT_TEARING_DOWN is not terminal; "
        f"got {controller.cleanup_calls}"
    )
