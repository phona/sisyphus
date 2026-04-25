"""Contract tests for REQ-watchdog-intake-no-result-1777078182.

feat(watchdog): detect intake session.completed without result tag, escalate
with reason intake-no-result-tag.

Black-box behavioral contract verification written by challenger-agent.
Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  WD-S1  INTAKING + session ended + no result tag → intake-no-result-tag
  WD-S2  INTAKING + session running → skip (no escalation)
  WD-S3  INTAKING + session completed + result:pass → generic stuck path
  WD-S4  BKD lookup failure (issue=None) → generic stuck path (not misclassified)
  WD-S5  escalate honors ctx.escalated_reason for intake event (non-canonical)
  WD-S6  escalate runs cleanup CAS for intake event (_SESSION_END_SIGNALS)
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

# ─── Contract 1: watchdog new constants ───────────────────────────────────────


class TestWatchdogConstantsContract:
    """Spec: three new constants in orchestrator.watchdog."""

    def test_intake_result_tags_exists_and_correct(self):
        """Spec: _INTAKE_RESULT_TAGS = {"result:pass", "result:fail"}."""
        from orchestrator.watchdog import _INTAKE_RESULT_TAGS

        assert _INTAKE_RESULT_TAGS == {"result:pass", "result:fail"}, (
            f"expected {{'result:pass', 'result:fail'}}, got {_INTAKE_RESULT_TAGS!r}"
        )

    def test_intake_no_result_event_exists_and_correct(self):
        """Spec: _INTAKE_NO_RESULT_EVENT = 'watchdog.intake_no_result_tag'."""
        from orchestrator.watchdog import _INTAKE_NO_RESULT_EVENT

        assert _INTAKE_NO_RESULT_EVENT == "watchdog.intake_no_result_tag", (
            f"expected 'watchdog.intake_no_result_tag', got {_INTAKE_NO_RESULT_EVENT!r}"
        )

    def test_intake_no_result_reason_exists_and_correct(self):
        """Spec: _INTAKE_NO_RESULT_REASON = 'intake-no-result-tag'."""
        from orchestrator.watchdog import _INTAKE_NO_RESULT_REASON

        assert _INTAKE_NO_RESULT_REASON == "intake-no-result-tag", (
            f"expected 'intake-no-result-tag', got {_INTAKE_NO_RESULT_REASON!r}"
        )


# ─── Contract 2: _is_intake_no_result_tag pure helper (WD-S1 to WD-S4) ────────


class TestIsIntakeNoResultTagContract:
    """Pure function: (ReqState, Issue | None) -> bool.

    Spec: Returns True iff state == INTAKING AND issue is not None AND
    issue.session_status != 'running' AND issue.tags has no overlap
    with {"result:pass", "result:fail"}.
    """

    def _make_issue(self, session_status: str, tags: list[str]):
        return SimpleNamespace(session_status=session_status, tags=tags)

    @staticmethod
    def _intaking():
        from orchestrator.state import ReqState

        return ReqState.INTAKING

    @staticmethod
    def _non_intaking():
        from orchestrator.state import ReqState

        return ReqState.ANALYZING

    def test_function_exists_and_is_callable(self):
        from orchestrator.watchdog import _is_intake_no_result_tag

        assert callable(_is_intake_no_result_tag)

    def test_function_signature_has_two_parameters(self):
        from orchestrator.watchdog import _is_intake_no_result_tag

        sig = inspect.signature(_is_intake_no_result_tag)
        assert len(sig.parameters) == 2, (
            f"_is_intake_no_result_tag must take 2 params, got {list(sig.parameters)}"
        )

    def test_wd_s1_intaking_completed_no_result_tag_returns_true(self):
        """WD-S1: INTAKING + session ended + no result tag → True."""
        from orchestrator.watchdog import _is_intake_no_result_tag

        issue = self._make_issue("completed", ["intake", "REQ-watchdog-intake-no-result-1777078182"])
        assert _is_intake_no_result_tag(self._intaking(), issue) is True

    def test_wd_s1_also_triggers_for_other_terminal_statuses(self):
        """WD-S1: any non-'running' session_status + no result tag → True."""
        from orchestrator.watchdog import _is_intake_no_result_tag

        for status in ("cancelled", "failed", "error"):
            issue = self._make_issue(status, ["intake", "REQ-x"])
            assert _is_intake_no_result_tag(self._intaking(), issue) is True, (
                f"expected True for session_status={status!r}"
            )

    def test_wd_s2_intaking_session_running_returns_false(self):
        """WD-S2: INTAKING + session running → False (agent still active, skip)."""
        from orchestrator.watchdog import _is_intake_no_result_tag

        issue = self._make_issue("running", ["intake", "REQ-x"])
        assert _is_intake_no_result_tag(self._intaking(), issue) is False

    def test_wd_s3_intaking_result_pass_returns_false(self):
        """WD-S3: INTAKING + session completed + result:pass → False (generic stuck)."""
        from orchestrator.watchdog import _is_intake_no_result_tag

        issue = self._make_issue("completed", ["intake", "REQ-x", "result:pass"])
        assert _is_intake_no_result_tag(self._intaking(), issue) is False

    def test_wd_s3_intaking_result_fail_returns_false(self):
        """WD-S3 variant: INTAKING + result:fail → False (generic stuck)."""
        from orchestrator.watchdog import _is_intake_no_result_tag

        issue = self._make_issue("completed", ["intake", "REQ-x", "result:fail"])
        assert _is_intake_no_result_tag(self._intaking(), issue) is False

    def test_wd_s4_issue_none_returns_false(self):
        """WD-S4: BKD lookup failure (issue=None) → False, must not raise."""
        from orchestrator.watchdog import _is_intake_no_result_tag

        try:
            result = _is_intake_no_result_tag(self._intaking(), None)
        except Exception as exc:
            pytest.fail(
                f"_is_intake_no_result_tag must not raise when issue=None; got {exc!r}"
            )
        assert result is False, (
            f"BKD lookup failure (issue=None) must return False, got {result!r}"
        )

    def test_non_intaking_state_returns_false(self):
        """Only INTAKING state should trigger the detection."""
        from orchestrator.watchdog import _is_intake_no_result_tag

        issue = self._make_issue("completed", ["intake", "REQ-x"])
        assert _is_intake_no_result_tag(self._non_intaking(), issue) is False


# ─── Contract 3: watchdog state-to-issue-key mapping covers INTAKING ──────────


class TestWatchdogStateIssueKeyContract:
    """Spec: state_to_issue_key_changes adds INTAKING → intent_issue_id.

    Watchdog must know which ctx key to use when looking up the BKD issue
    for INTAKING state; the ctx key is 'intent_issue_id'.
    """

    def test_intaking_mapped_to_intent_issue_id(self):
        """Spec: INTAKING state must use ctx_key='intent_issue_id' for BKD lookup."""
        import orchestrator.watchdog as wd
        from orchestrator.state import ReqState

        mapping = None
        for attr in dir(wd):
            if attr.startswith("_") and not attr.startswith("__"):
                val = getattr(wd, attr, None)
                if isinstance(val, dict) and ReqState.INTAKING in val:
                    mapping = val
                    break

        assert mapping is not None, (
            "orchestrator.watchdog must have a dict mapping ReqState.INTAKING "
            "to a ctx key string"
        )
        assert mapping[ReqState.INTAKING] == "intent_issue_id", (
            f"INTAKING must map to 'intent_issue_id', got {mapping[ReqState.INTAKING]!r}"
        )


# ─── Contract 4: escalate _SESSION_END_SIGNALS includes intake event (WD-S6) ──


class TestEscalateSessionEndSignalsContract:
    """WD-S6: escalate runs manual CAS → ESCALATED + cleanup_runner for intake event."""

    def test_session_end_signals_constant_exists(self):
        from orchestrator.actions.escalate import _SESSION_END_SIGNALS

        assert _SESSION_END_SIGNALS is not None

    def test_session_end_signals_contains_intake_no_result_event(self):
        """WD-S6: watchdog.intake_no_result_tag must be in _SESSION_END_SIGNALS."""
        from orchestrator.actions.escalate import _SESSION_END_SIGNALS

        assert "watchdog.intake_no_result_tag" in _SESSION_END_SIGNALS, (
            "_SESSION_END_SIGNALS must include 'watchdog.intake_no_result_tag' "
            "so escalate runs CAS + cleanup_runner for this event"
        )

    def test_session_end_signals_preserves_existing_events(self):
        """Spec: pre-existing session-end events must remain in _SESSION_END_SIGNALS."""
        from orchestrator.actions.escalate import _SESSION_END_SIGNALS

        for event in ("session.failed", "watchdog.stuck"):
            assert event in _SESSION_END_SIGNALS, (
                f"_SESSION_END_SIGNALS must still contain existing event {event!r}"
            )


# ─── Contract 5: escalate non-canonical classification (WD-S5) ────────────────


class TestEscalateNonCanonicalContract:
    """WD-S5: escalate honors ctx.escalated_reason for intake event.

    watchdog.intake_no_result_tag is intentionally NOT in _CANONICAL_SIGNALS
    so that ctx.escalated_reason='intake-no-result-tag' is used as the reason
    when tagging the BKD intent issue, instead of a body-derived reason.
    """

    def test_canonical_signals_exists(self):
        from orchestrator.actions.escalate import _CANONICAL_SIGNALS

        assert _CANONICAL_SIGNALS is not None

    def test_intake_no_result_not_in_canonical_signals(self):
        """WD-S5: not in _CANONICAL_SIGNALS → ctx.escalated_reason wins."""
        from orchestrator.actions.escalate import _CANONICAL_SIGNALS

        assert "watchdog.intake_no_result_tag" not in _CANONICAL_SIGNALS, (
            "watchdog.intake_no_result_tag must NOT be in _CANONICAL_SIGNALS; "
            "its reason must come from ctx.escalated_reason pre-written by watchdog"
        )
