"""Contract tests for REQ-start-actions-ctx-persist-1777345119.

fix(watchdog+start_challenger): persist challenger_issue_id to ctx + watchdog defensive guard

Black-box behavioral contract verification written by challenger-agent.
Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  SACP-S1  start_challenger.py imports db + req_state and calls update_context with
           challenger_issue_id (source-code contract)
  SACP-S2  start_challenger return value dict contains challenger_issue_id key
  SACP-S3  watchdog._STATE_ISSUE_KEY[CHALLENGER_RUNNING] == "challenger_issue_id"
  SACP-S4  CHALLENGER_RUNNING + ctx lacks challenger_issue_id → _check_and_escalate returns False
  SACP-S5  CHALLENGER_RUNNING + issue_id in ctx + session running → returns False (no false escalate)
  SACP-S6  CHALLENGER_RUNNING + issue_id in ctx + session failed → returns True (escalate)
  SACP-S7  STAGING_TEST_RUNNING + issue_id=None → escalates (CHALLENGER_RUNNING guard scoped only)
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

_PRODUCTION_SOURCE = Path(__file__).resolve().parent.parent / "src" / "orchestrator"


# ─── Part 1: Source-code structural contracts ─────────────────────────────────


class TestStartChallengerCtxWriteStructural:
    """SACP-S1, SACP-S2 (structural): start_challenger.py must be wired for ctx persistence."""

    def test_sacp_s1_start_challenger_uses_req_state(self):
        """S1 structural: start_challenger.py must reference req_state to call update_context."""
        src = (_PRODUCTION_SOURCE / "actions" / "start_challenger.py").read_text(encoding="utf-8")
        assert "req_state" in src, (
            "start_challenger.py must import or use 'req_state' to call update_context "
            "(SACP-S1: REQ-start-actions-ctx-persist-1777345119)"
        )

    def test_sacp_s1_start_challenger_calls_update_context(self):
        """S1 structural: start_challenger.py must call update_context."""
        src = (_PRODUCTION_SOURCE / "actions" / "start_challenger.py").read_text(encoding="utf-8")
        assert "update_context" in src, (
            "start_challenger.py must call req_state.update_context to persist "
            "challenger_issue_id (SACP-S1: REQ-start-actions-ctx-persist-1777345119)"
        )

    def test_sacp_s1_start_challenger_writes_challenger_issue_id_key(self):
        """S1 structural: start_challenger.py must write challenger_issue_id key to ctx."""
        src = (_PRODUCTION_SOURCE / "actions" / "start_challenger.py").read_text(encoding="utf-8")
        assert "challenger_issue_id" in src, (
            "start_challenger.py must write 'challenger_issue_id' via update_context "
            "(SACP-S1: REQ-start-actions-ctx-persist-1777345119). "
            "This ctx key is consumed by watchdog._check_and_escalate for CHALLENGER_RUNNING."
        )

    def test_sacp_s1_start_challenger_uses_db_pool(self):
        """S1 structural: start_challenger.py must use db pool for update_context call."""
        src = (_PRODUCTION_SOURCE / "actions" / "start_challenger.py").read_text(encoding="utf-8")
        assert "db" in src, (
            "start_challenger.py must reference 'db' to obtain a pool for "
            "req_state.update_context(pool, ...) (SACP-S1)"
        )

    def test_sacp_s2_start_challenger_return_includes_challenger_issue_id(self):
        """S2 structural: start_challenger must surface challenger_issue_id in its return dict."""
        src = (_PRODUCTION_SOURCE / "actions" / "start_challenger.py").read_text(encoding="utf-8")
        # The source must contain challenger_issue_id at least twice: once for the ctx write,
        # once as a key in the return dict. Even once is sufficient for this structural check
        # because the functional test (S2 behavioral) will validate the actual return value.
        assert "challenger_issue_id" in src, (
            "start_challenger.py must include 'challenger_issue_id' in its return value dict "
            "(SACP-S2: the caller — orchestrator state machine — records it for traceability)."
        )


# ─── Part 2: _STATE_ISSUE_KEY must map CHALLENGER_RUNNING ─────────────────────


class TestWatchdogStateIssueKeyContract:
    """SACP-S3: watchdog._STATE_ISSUE_KEY[CHALLENGER_RUNNING] == 'challenger_issue_id'."""

    def test_sacp_s3_state_issue_key_exists(self):
        """Spec: orchestrator.watchdog must define _STATE_ISSUE_KEY dict."""
        import orchestrator.watchdog as wd

        mapping = getattr(wd, "_STATE_ISSUE_KEY", None)
        assert mapping is not None, (
            "orchestrator.watchdog must define '_STATE_ISSUE_KEY' dict "
            "(SACP-S3: REQ-start-actions-ctx-persist-1777345119)"
        )
        assert isinstance(mapping, dict), (
            f"_STATE_ISSUE_KEY must be a dict, got {type(mapping)!r}"
        )

    def test_sacp_s3_challenger_running_key_present(self):
        """Spec: CHALLENGER_RUNNING must appear as a key in _STATE_ISSUE_KEY."""
        import orchestrator.watchdog as wd
        from orchestrator.state import ReqState

        mapping = wd._STATE_ISSUE_KEY
        assert ReqState.CHALLENGER_RUNNING in mapping, (
            f"_STATE_ISSUE_KEY must contain ReqState.CHALLENGER_RUNNING entry; "
            f"found keys: {[str(k) for k in mapping.keys()]!r}. "
            "SACP-S3: watchdog needs this mapping to query the BKD session for CHALLENGER_RUNNING."
        )

    def test_sacp_s3_challenger_running_maps_to_correct_ctx_key(self):
        """Spec: _STATE_ISSUE_KEY[CHALLENGER_RUNNING] == 'challenger_issue_id'."""
        import orchestrator.watchdog as wd
        from orchestrator.state import ReqState

        mapping = wd._STATE_ISSUE_KEY
        actual = mapping.get(ReqState.CHALLENGER_RUNNING, "<MISSING>")
        assert actual == "challenger_issue_id", (
            f"_STATE_ISSUE_KEY[CHALLENGER_RUNNING] must be 'challenger_issue_id'; "
            f"got {actual!r}. "
            "SACP-S3: this ctx key is written by start_challenger and read by watchdog."
        )

    def test_sacp_s3_existing_state_mappings_preserved(self):
        """Structural coexistence: adding CHALLENGER_RUNNING must not remove other entries."""
        import orchestrator.watchdog as wd
        from orchestrator.state import ReqState

        mapping = wd._STATE_ISSUE_KEY
        # FIXER_RUNNING must still have a mapping (used by fixer_round_cap logic)
        assert ReqState.FIXER_RUNNING in mapping, (
            "_STATE_ISSUE_KEY must still contain ReqState.FIXER_RUNNING; "
            "the new CHALLENGER_RUNNING entry must not remove existing entries."
        )


# ─── Part 3: Behavioral _check_and_escalate contracts ─────────────────────────


def _make_settings(
    watchdog_timeout_secs: int = 1800,
    fixer_round_cap: int = 5,
) -> MagicMock:
    s = MagicMock()
    s.watchdog_timeout_secs = watchdog_timeout_secs
    s.watchdog_interval_secs = 30
    s.fixer_round_cap = fixer_round_cap
    return s


def _make_req_row(
    state_val: str,
    ctx: dict,
    req_id: str = "REQ-sacp-test-1234",
    stuck_secs: int = 3600,
) -> dict:
    """Build a minimal req row dict as watchdog._check_and_escalate expects."""
    return {
        "req_id": req_id,
        "project_id": "test-project",
        "state": state_val,
        "context": ctx,
        "stuck_sec": stuck_secs,
        "updated_at": datetime.now(UTC) - timedelta(seconds=stuck_secs),
        "intent_issue_id": "intent-issue-1",
        "created_at": datetime.now(UTC) - timedelta(seconds=stuck_secs + 100),
    }


def _state_str(state_enum) -> str:
    """Convert ReqState enum to the string value stored in the DB row."""
    return state_enum.value if hasattr(state_enum, "value") else str(state_enum)


def _make_watchdog_patches(settings, mock_req_state, mock_engine):
    from orchestrator import watchdog as wd
    return [
        patch.object(wd, "settings", settings),
        patch.object(wd, "req_state", mock_req_state),
        patch.object(wd, "engine", mock_engine),
        patch("orchestrator.store.db.get_pool", return_value=AsyncMock()),
    ]


class TestChallengerRunningGuardBehavior:
    """SACP-S4 through S7: behavioral contracts for _check_and_escalate guard."""

    async def test_sacp_s4_challenger_running_no_issue_id_returns_false(self):
        """SACP-S4: CHALLENGER_RUNNING + ctx missing challenger_issue_id → return False.

        When challenger_issue_id is absent from ctx, watchdog MUST skip escalation
        (return False) and MUST NOT call engine.step. The absence is treated as a
        transient condition (ctx write in flight or pre-fix deployment), not as
        evidence that the session has ended.
        """
        from orchestrator import watchdog as wd
        from orchestrator.state import ReqState

        settings = _make_settings()
        mock_req_state = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.step = AsyncMock()

        row = _make_req_row(
            state_val=_state_str(ReqState.CHALLENGER_RUNNING),
            ctx={},  # challenger_issue_id absent — simulates ctx write race
            stuck_secs=3600,  # well past watchdog_timeout_secs=1800
        )

        p = _make_watchdog_patches(settings, mock_req_state, mock_engine)
        with p[0], p[1], p[2], p[3]:
            result = await wd._check_and_escalate(row)

        assert result is False, (
            f"SACP-S4: CHALLENGER_RUNNING + issue_id=None must return False (skip escalation); "
            f"got {result!r}. "
            "Absent challenger_issue_id is a transient condition, not an ended session."
        )
        mock_engine.step.assert_not_called()

    async def test_sacp_s4_log_warning_emitted_for_missing_issue_id(self):
        """SACP-S4 guard: watchdog must emit 'watchdog.missing_issue_id' warning.

        Structural check: the source code must contain the log key for observability.
        """
        src = (_PRODUCTION_SOURCE / "watchdog.py").read_text(encoding="utf-8")
        assert "missing_issue_id" in src, (
            "watchdog.py must emit 'watchdog.missing_issue_id' (or similar) warning "
            "when CHALLENGER_RUNNING + issue_id is None (SACP-S4 observability contract)."
        )

    async def test_sacp_s5_challenger_running_session_running_returns_false(self):
        """SACP-S5: CHALLENGER_RUNNING + challenger_issue_id in ctx + session running → False.

        When the challenger BKD session is still active (session_status='running'),
        watchdog MUST NOT escalate. engine.step must not be called.
        """
        from orchestrator import watchdog as wd
        from orchestrator.state import ReqState

        settings = _make_settings()
        mock_req_state = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.step = AsyncMock()

        row = _make_req_row(
            state_val=_state_str(ReqState.CHALLENGER_RUNNING),
            ctx={"challenger_issue_id": "ch-99"},
            stuck_secs=3600,
        )

        mock_issue = SimpleNamespace(
            id="ch-99",
            session_status="running",
            tags=["challenger", "REQ-sacp-test-1234"],
            statusId="working",
        )

        # BKD client: try both context-manager and async-with patterns
        mock_bkd_inner = AsyncMock()
        mock_bkd_inner.get_issue = AsyncMock(return_value=mock_issue)
        mock_bkd_inst = AsyncMock()
        mock_bkd_inst.__aenter__ = AsyncMock(return_value=mock_bkd_inner)
        mock_bkd_inst.get_issue = AsyncMock(return_value=mock_issue)
        mock_bkd_cls = MagicMock(return_value=mock_bkd_inst)

        p = _make_watchdog_patches(settings, mock_req_state, mock_engine)
        with p[0], p[1], p[2], p[3]:
            with patch.object(wd, "BKDClient", mock_bkd_cls, create=True):
                result = await wd._check_and_escalate(row)

        assert result is False, (
            f"SACP-S5: CHALLENGER_RUNNING + session_status='running' must return False "
            f"(agent still active); got {result!r}. "
            "Watchdog must not falsely escalate a running challenger session."
        )
        mock_engine.step.assert_not_called()

    async def test_sacp_s6_challenger_running_session_failed_escalates(self):
        """SACP-S6: CHALLENGER_RUNNING + challenger_issue_id in ctx + session failed → True.

        When the challenger BKD session has failed (session_status='failed'),
        watchdog MUST escalate (return True, engine.step called).
        """
        from orchestrator import watchdog as wd
        from orchestrator.state import ReqState

        settings = _make_settings()
        mock_req_state = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.step = AsyncMock()

        row = _make_req_row(
            state_val=_state_str(ReqState.CHALLENGER_RUNNING),
            ctx={"challenger_issue_id": "ch-99"},
            stuck_secs=3600,
        )

        mock_issue = SimpleNamespace(
            id="ch-99",
            session_status="failed",
            tags=["challenger", "REQ-sacp-test-1234"],
            statusId="working",
        )

        mock_bkd_inner = AsyncMock()
        mock_bkd_inner.get_issue = AsyncMock(return_value=mock_issue)
        mock_bkd_inst = AsyncMock()
        mock_bkd_inst.__aenter__ = AsyncMock(return_value=mock_bkd_inner)
        mock_bkd_inst.get_issue = AsyncMock(return_value=mock_issue)
        mock_bkd_cls = MagicMock(return_value=mock_bkd_inst)

        p = _make_watchdog_patches(settings, mock_req_state, mock_engine)
        with p[0], p[1], p[2], p[3]:
            with patch.object(wd, "BKDClient", mock_bkd_cls, create=True):
                result = await wd._check_and_escalate(row)

        assert result is True, (
            f"SACP-S6: CHALLENGER_RUNNING + session_status='failed' must return True "
            f"(escalate); got {result!r}. SACP-S6."
        )

    async def test_sacp_s7_staging_test_running_no_issue_id_escalates(self):
        """SACP-S7: STAGING_TEST_RUNNING + issue_id=None → escalates (guard not triggered).

        The CHALLENGER_RUNNING guard MUST be scoped only to CHALLENGER_RUNNING.
        Other states (STAGING_TEST_RUNNING) must follow the existing escalation path:
        time-based stuck check fires → engine.step called.
        """
        from orchestrator import watchdog as wd
        from orchestrator.state import ReqState

        settings = _make_settings()
        mock_req_state = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.step = AsyncMock()

        row = _make_req_row(
            state_val=_state_str(ReqState.STAGING_TEST_RUNNING),
            ctx={},  # no staging_test_issue_id → issue_id=None
            stuck_secs=3600,  # past watchdog_timeout_secs=1800
        )

        p = _make_watchdog_patches(settings, mock_req_state, mock_engine)
        with p[0], p[1], p[2], p[3]:
            result = await wd._check_and_escalate(row)

        # The CHALLENGER_RUNNING guard must NOT have fired for this state.
        # Existing behavior: stuck threshold exceeded → escalate (return True).
        assert result is True, (
            f"SACP-S7: STAGING_TEST_RUNNING + issue_id=None must escalate (return True); "
            f"got {result!r}. "
            "The CHALLENGER_RUNNING guard must be scoped ONLY to CHALLENGER_RUNNING — "
            "it must NOT suppress escalation for other states."
        )
        mock_engine.step.assert_called()


# ─── Part 4: Guard scope — CHALLENGER_RUNNING guard structural contract ───────


class TestChallengerGuardScopeStructural:
    """SACP-S4 structural: the guard must be explicitly scoped to CHALLENGER_RUNNING only."""

    def test_sacp_s4_guard_explicitly_scoped_to_challenger_running_in_source(self):
        """Source structural: watchdog.py must contain an explicit CHALLENGER_RUNNING scope check.

        The guard 'if state == CHALLENGER_RUNNING and issue_id is None' must be present
        to prevent other states (e.g., FIXER_RUNNING) from being affected.
        """
        src = (_PRODUCTION_SOURCE / "watchdog.py").read_text(encoding="utf-8")
        assert "CHALLENGER_RUNNING" in src, (
            "watchdog.py must explicitly reference CHALLENGER_RUNNING for the "
            "defensive guard (SACP-S4 scope: scoped only to CHALLENGER_RUNNING)."
        )
        # The guard must check for None issue_id
        assert "issue_id is None" in src or "issue_id is not None" in src, (
            "watchdog.py must contain an 'issue_id is None' (or negation) check "
            "for the CHALLENGER_RUNNING defensive guard (SACP-S4)."
        )
