"""Unit tests for orchestrator.intent_status helper.

REQ-bkd-intent-statusid-sync-1777280751

Covers BIS-S1..S8:
  S1-S3 status_id_for mapping (DONE, ESCALATED, non-terminal -> None)
  S4-S5 patch_terminal_status invokes BKD update_issue with mapped statusId
  S6-S7 patch_terminal_status skips when intent_issue_id missing or state non-terminal
  S8    patch_terminal_status swallows BKD exceptions, logs warning
"""
from __future__ import annotations

import pytest

from orchestrator import intent_status
from orchestrator.state import ReqState

# ─── BIS-S1..S3: status_id_for mapping ──────────────────────────────────


def test_bis_s1_status_id_for_done_returns_done():
    """BIS-S1: status_id_for(DONE) MUST equal 'done'."""
    assert intent_status.status_id_for(ReqState.DONE) == "done"


def test_bis_s2_status_id_for_escalated_returns_review():
    """BIS-S2: status_id_for(ESCALATED) MUST equal 'review'."""
    assert intent_status.status_id_for(ReqState.ESCALATED) == "review"


@pytest.mark.parametrize("non_terminal_state", [
    ReqState.INIT,
    ReqState.INTAKING,
    ReqState.ANALYZING,
    ReqState.SPEC_LINT_RUNNING,
    ReqState.CHALLENGER_RUNNING,
    ReqState.DEV_CROSS_CHECK_RUNNING,
    ReqState.STAGING_TEST_RUNNING,
    ReqState.PR_CI_RUNNING,
    ReqState.ACCEPT_RUNNING,
    ReqState.ACCEPT_TEARING_DOWN,
    ReqState.REVIEW_RUNNING,
    ReqState.FIXER_RUNNING,
    ReqState.ARCHIVING,
])
def test_bis_s3_status_id_for_non_terminal_returns_none(non_terminal_state):
    """BIS-S3: every non-terminal state MUST map to None (skip path)."""
    assert intent_status.status_id_for(non_terminal_state) is None


# ─── BIS-S4..S5: patch_terminal_status calls BKD with mapped statusId ────


class _RecordingBKD:
    """Minimal fake BKDClient context manager that records update_issue calls."""

    def __init__(self, *args, **kwargs):
        self.calls: list[dict] = []
        # Surface calls list on the *class* so tests can read it after exit.
        type(self)._last_calls = self.calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def update_issue(self, *, project_id, issue_id, status_id=None, tags=None, title=None):
        self.calls.append({
            "project_id": project_id,
            "issue_id": issue_id,
            "status_id": status_id,
            "tags": tags,
            "title": title,
        })


@pytest.mark.asyncio
async def test_bis_s4_done_patches_status_done(monkeypatch):
    """BIS-S4: DONE entry MUST call update_issue with status_id='done'."""
    monkeypatch.setattr(intent_status, "BKDClient", _RecordingBKD)

    result = await intent_status.patch_terminal_status(
        project_id="proj-x",
        intent_issue_id="intent-1",
        terminal_state=ReqState.DONE,
        source="test.bis_s4",
    )
    assert result is True, "PATCH attempted -> helper returns True"
    calls = _RecordingBKD._last_calls
    assert len(calls) == 1, f"expected 1 update_issue call, got {len(calls)}"
    assert calls[0] == {
        "project_id": "proj-x",
        "issue_id": "intent-1",
        "status_id": "done",
        "tags": None,
        "title": None,
    }


@pytest.mark.asyncio
async def test_bis_s5_escalated_patches_status_review(monkeypatch):
    """BIS-S5: ESCALATED entry MUST call update_issue with status_id='review'."""
    monkeypatch.setattr(intent_status, "BKDClient", _RecordingBKD)

    result = await intent_status.patch_terminal_status(
        project_id="proj-y",
        intent_issue_id="intent-2",
        terminal_state=ReqState.ESCALATED,
        source="test.bis_s5",
    )
    assert result is True
    calls = _RecordingBKD._last_calls
    assert len(calls) == 1
    assert calls[0]["status_id"] == "review"
    assert calls[0]["issue_id"] == "intent-2"
    assert calls[0]["project_id"] == "proj-y"


# ─── BIS-S6..S7: skip paths (no intent_issue_id, non-terminal state) ─────


@pytest.mark.asyncio
async def test_bis_s6_missing_intent_issue_id_skips_bkd(monkeypatch):
    """BIS-S6: empty intent_issue_id MUST skip BKD call, return False, no raise."""
    bkd_called = []

    class _FailIfCalled(_RecordingBKD):
        async def update_issue(self, **kw):
            bkd_called.append(kw)
            raise RuntimeError("MUST NOT be called")

    monkeypatch.setattr(intent_status, "BKDClient", _FailIfCalled)

    for empty in (None, ""):
        result = await intent_status.patch_terminal_status(
            project_id="proj-x",
            intent_issue_id=empty,
            terminal_state=ReqState.DONE,
            source="test.bis_s6",
        )
        assert result is False, f"empty intent_issue_id={empty!r} -> False"
    assert bkd_called == []


@pytest.mark.asyncio
async def test_bis_s7_non_terminal_state_skips_bkd(monkeypatch):
    """BIS-S7: non-terminal state MUST skip BKD call and return False."""
    bkd_called = []

    class _FailIfCalled(_RecordingBKD):
        async def update_issue(self, **kw):
            bkd_called.append(kw)
            raise RuntimeError("MUST NOT be called")

    monkeypatch.setattr(intent_status, "BKDClient", _FailIfCalled)

    result = await intent_status.patch_terminal_status(
        project_id="proj-x",
        intent_issue_id="intent-1",
        terminal_state=ReqState.INTAKING,
        source="test.bis_s7",
    )
    assert result is False
    assert bkd_called == []


# ─── BIS-S8: BKD exception -> warning + swallow ───────────────────────────


class _RaisingBKD:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def update_issue(self, **kw):
        raise RuntimeError("BKD 503")


@pytest.mark.asyncio
async def test_bis_s8_bkd_failure_logs_warning_no_raise(monkeypatch, capsys):
    """BIS-S8: BKD raise MUST be swallowed; helper logs warning and returns True.

    structlog routes to stdout (not stdlib logging), so we capture via capsys.
    """
    monkeypatch.setattr(intent_status, "BKDClient", _RaisingBKD)

    # MUST NOT raise.
    result = await intent_status.patch_terminal_status(
        project_id="proj-x",
        intent_issue_id="intent-1",
        terminal_state=ReqState.DONE,
        source="test.bis_s8",
    )
    # PATCH was attempted (even though it raised under the hood) -> True.
    assert result is True
    out = capsys.readouterr().out
    assert "intent_status.patch_failed" in out, (
        f"expected 'intent_status.patch_failed' in stdout; got: {out!r}"
    )
    assert "BKD 503" in out, f"expected error to be logged; got: {out!r}"
