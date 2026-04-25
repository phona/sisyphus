"""
Contract tests for REQ-fixer-round-cap-1777078900:
feat(engine+watchdog): hard cap fixer rounds at N (default 5)

Black-box behavioral contracts derived from:
  openspec/changes/REQ-fixer-round-cap-1777078900/specs/fixer-round-cap/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  FRC-S1  第一次 start_fixer 写 round=1，BKD create_issue 调用一次，tag 含 round:1
  FRC-S2  round 计数随每轮单调递增（fixer_round=2 → 3，tag round:3）
  FRC-S3  第 (cap+1) 次 start_fixer 触发 escalate（不创 BKD issue，返回 emit:verify.escalate）
  FRC-S4  cap 可通过 settings 覆盖（cap=2, round=2 → 触发 cap）
  FRC-S5  FIXER_RUNNING + VERIFY_ESCALATE → decide() 返回 ESCALATED transition
  FRC-S6  escalate: ctx hard reason "fixer-round-cap" 压过 canonical body.event
  FRC-S7  _is_transient(body_event, "fixer-round-cap") 永远返回 False
  FRC-S8  watchdog 兜底：FIXER_RUNNING + round>=cap → 写 escalated_reason="fixer-round-cap"
  FRC-S9  watchdog 在 round<cap 时不写 fixer-round-cap reason

Function signatures (verified via inspect without reading source):
  start_fixer(*, body, req_id, tags, ctx) -> dict
  _is_transient(body_event: str | None, reason: str) -> bool
  _check_and_escalate(row) -> bool
  body.projectId: str  (body is an object with .projectId, not a plain dict)
  row keys: req_id, project_id, state, context, stuck_sec, updated_at, intent_issue_id
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_settings(fixer_round_cap: int = 5) -> Any:
    s = MagicMock()
    s.fixer_round_cap = fixer_round_cap
    s.watchdog_timeout_secs = 1800
    s.watchdog_interval_secs = 30
    return s


def _make_req_row(
    state_val: str,
    ctx: dict,
    req_id: str = "REQ-test-1234",
    stuck_secs: int = 3600,
) -> dict:
    """Build a minimal req row dict as _check_and_escalate expects from DB query."""
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


def _make_body(project_id: str = "test-project") -> Any:
    """Create a body object with .projectId (as start_fixer expects)."""
    b = MagicMock()
    b.projectId = project_id
    return b


def _make_mock_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    pool.execute.return_value = None
    return pool


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: State machine transition  FRC-S5
# ─────────────────────────────────────────────────────────────────────────────


class TestStateMachineTransitionFRCS5:
    """Spec: state machine MUST define (FIXER_RUNNING, VERIFY_ESCALATE) → ESCALATED."""

    def test_frc_s5_fixer_running_verify_escalate_decides_escalated(self):
        """
        FRC-S5: decide(FIXER_RUNNING, VERIFY_ESCALATE) must return a non-None
        Transition with next_state=ESCALATED and action='escalate'.
        """
        from orchestrator.state import Event, ReqState, decide

        result = decide(ReqState.FIXER_RUNNING, Event.VERIFY_ESCALATE)

        assert result is not None, (
            "decide(FIXER_RUNNING, VERIFY_ESCALATE) must return a non-None Transition; "
            "got None — transition is missing from state machine"
        )
        assert result.next_state == ReqState.ESCALATED, (
            f"next_state must be ESCALATED, got {result.next_state!r}"
        )
        assert result.action == "escalate", (
            f"action must be 'escalate', got {result.action!r}"
        )

    def test_frc_s5_existing_review_running_verify_escalate_still_works(self):
        """
        Spec coexistence: existing (REVIEW_RUNNING, VERIFY_ESCALATE) → ESCALATED
        must not be broken by adding the new FIXER_RUNNING transition.
        """
        from orchestrator.state import Event, ReqState, decide

        result = decide(ReqState.REVIEW_RUNNING, Event.VERIFY_ESCALATE)

        assert result is not None, (
            "decide(REVIEW_RUNNING, VERIFY_ESCALATE) must still return a Transition "
            "(existing transition must not be removed)"
        )
        assert result.next_state == ReqState.ESCALATED


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: _is_transient invariant  FRC-S7
# Signature: _is_transient(body_event: str | None, reason: str) -> bool
# ─────────────────────────────────────────────────────────────────────────────


class TestIsTransientFRCS7:
    """Spec: _is_transient MUST return False for reason='fixer-round-cap', all body_events."""

    @pytest.mark.parametrize("body_event", ["session.failed", "watchdog.stuck", None])
    def test_frc_s7_fixer_round_cap_is_never_transient(self, body_event):
        """
        FRC-S7: _is_transient(body_event, 'fixer-round-cap') must always return False.
        Signature: _is_transient(body_event: str|None, reason: str).
        """
        from orchestrator.actions.escalate import _is_transient

        result = _is_transient(body_event, "fixer-round-cap")

        assert result is False, (
            f"_is_transient({body_event!r}, 'fixer-round-cap') must return False; "
            f"got {result!r}. A True result would enable auto-resume, "
            "restarting the verifier↔fixer loop after cap was tripped."
        )

    def test_frc_s7_fixer_round_cap_in_hard_reasons(self):
        """
        Structural: 'fixer-round-cap' must be in escalate._HARD_REASONS.
        """
        import orchestrator.actions.escalate as escalate_mod

        hard_reasons = getattr(escalate_mod, "_HARD_REASONS", None)
        assert hard_reasons is not None, (
            "escalate module must define _HARD_REASONS set; attribute not found"
        )
        assert "fixer-round-cap" in hard_reasons, (
            f"'fixer-round-cap' must be in _HARD_REASONS; found: {hard_reasons!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: start_fixer cap logic  FRC-S1, S2, S3, S4
# Signature: start_fixer(*, body, req_id, tags, ctx)
# Patches: settings, req_state, BKDClient, orchestrator.store.db.get_pool
# ─────────────────────────────────────────────────────────────────────────────


def _start_fixer_patches(settings, mock_req_state, mock_bkd_cls):
    """Return a list of patch contexts for start_fixer tests."""
    return [
        patch("orchestrator.actions._verifier.settings", settings),
        patch("orchestrator.actions._verifier.req_state", mock_req_state),
        patch("orchestrator.actions._verifier.BKDClient", mock_bkd_cls),
        patch("orchestrator.store.db.get_pool", return_value=_make_mock_pool()),
    ]


class TestStartFixerCapLogicFRCS3S4:
    """Black-box contract: start_fixer MUST enforce fixer_round_cap."""

    async def test_frc_s3_cap_hit_returns_escalate_emit(self):
        """
        FRC-S3: ctx.fixer_round=5, cap=5 → start_fixer MUST return
        {"emit":"verify.escalate","reason":"fixer-round-cap","fixer_round":5,"cap":5}
        and MUST NOT call BKD create_issue.
        """
        from orchestrator.actions._verifier import start_fixer

        ctx = {"fixer_round": 5}
        settings = _make_settings(fixer_round_cap=5)
        created_issues: list = []

        mock_bkd_inst = AsyncMock()

        async def _no_create(*args, **kwargs):
            created_issues.append((args, kwargs))
            return {"data": {"id": "must-not-be-called"}}

        mock_bkd_inst.create_issue.side_effect = _no_create
        mock_bkd_cls = MagicMock(return_value=mock_bkd_inst)

        mock_req_state = AsyncMock()
        written_ctx: dict = {}

        async def _capture_ctx(*args, **updates):
            written_ctx.update(updates)
            for a in args:
                if isinstance(a, dict):
                    written_ctx.update(a)

        mock_req_state.update_context.side_effect = _capture_ctx

        patches = _start_fixer_patches(settings, mock_req_state, mock_bkd_cls)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await start_fixer(
                body=_make_body(),
                req_id="REQ-test",
                tags=["verifier", "REQ-test"],
                ctx=ctx,
            )

        assert isinstance(result, dict), (
            f"start_fixer must return a dict when cap hit; got {type(result)}"
        )
        assert result.get("emit") == "verify.escalate", (
            f"emit must be 'verify.escalate' when cap hit; got {result!r}"
        )
        assert result.get("reason") == "fixer-round-cap", (
            f"reason must be 'fixer-round-cap'; got {result!r}"
        )
        assert result.get("cap") == 5, (
            f"cap field must be 5; got {result!r}"
        )
        assert result.get("fixer_round") == 5, (
            f"fixer_round field must report current round=5; got {result!r}"
        )
        assert len(created_issues) == 0, (
            f"BKD create_issue MUST NOT be called when cap hit; "
            f"was called {len(created_issues)} time(s)"
        )
        assert written_ctx.get("escalated_reason") == "fixer-round-cap", (
            f"ctx.escalated_reason must be 'fixer-round-cap'; "
            f"update_context received: {written_ctx!r}"
        )
        assert written_ctx.get("fixer_round_cap_hit") == 5, (
            f"ctx.fixer_round_cap_hit must be 5; update_context received: {written_ctx!r}"
        )

    async def test_frc_s4_custom_cap_enforced(self):
        """
        FRC-S4: settings.fixer_round_cap=2 + ctx.fixer_round=2 →
        next_round=3 > cap=2 → escalate, no BKD issue.
        """
        from orchestrator.actions._verifier import start_fixer

        ctx = {"fixer_round": 2}
        settings = _make_settings(fixer_round_cap=2)
        created_issues: list = []

        mock_bkd_inst = AsyncMock()
        mock_bkd_inst.create_issue.side_effect = lambda *a, **kw: (
            created_issues.append(True) or {"data": {"id": "x"}}
        )
        mock_bkd_cls = MagicMock(return_value=mock_bkd_inst)
        mock_req_state = AsyncMock()

        patches = _start_fixer_patches(settings, mock_req_state, mock_bkd_cls)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await start_fixer(
                body=_make_body(),
                req_id="REQ-test",
                tags=[],
                ctx=ctx,
            )

        assert isinstance(result, dict), (
            f"start_fixer must return dict when cap hit (cap=2, round=2); got {type(result)}"
        )
        assert result.get("emit") == "verify.escalate", (
            f"Must escalate when cap=2 and round=2; got emit={result.get('emit')!r}"
        )
        assert len(created_issues) == 0, (
            "BKD create_issue must not be called when custom cap is hit"
        )


class TestStartFixerCounterFRCS1S2:
    """Contract: fixer_round counter is written and issue is tagged round:N."""

    async def test_frc_s1_first_call_writes_round_1_and_tags_issue(self):
        """
        FRC-S1: Fresh REQ (no fixer_round). start_fixer must write fixer_round=1,
        call BKD create_issue once, and include tag 'round:1'.
        """
        from orchestrator.actions._verifier import start_fixer

        ctx = {}
        settings = _make_settings(fixer_round_cap=5)

        written_ctx: dict = {}
        mock_req_state = AsyncMock()

        async def _capture_ctx(*args, **updates):
            written_ctx.update(updates)
            for a in args:
                if isinstance(a, dict):
                    written_ctx.update(a)

        mock_req_state.update_context.side_effect = _capture_ctx

        captured_create_calls: list = []

        async def _capture_create(title=None, tags=None, **kwargs):
            captured_create_calls.append({"title": title, "tags": tags or []})
            issue = MagicMock()
            issue.id = "fixer-issue-1"
            return issue

        # BKDClient is used as `async with BKDClient(...) as bkd:`, so set
        # the side_effect on __aenter__().create_issue, not on the instance directly.
        mock_bkd_inner = AsyncMock()
        mock_bkd_inner.create_issue.side_effect = _capture_create
        mock_bkd_inst = AsyncMock()
        mock_bkd_inst.__aenter__ = AsyncMock(return_value=mock_bkd_inner)
        mock_bkd_cls = MagicMock(return_value=mock_bkd_inst)

        patches = _start_fixer_patches(settings, mock_req_state, mock_bkd_cls)
        with patches[0], patches[1], patches[2], patches[3]:
            await start_fixer(
                body=_make_body(),
                req_id="REQ-test",
                tags=["verifier", "REQ-test"],
                ctx=ctx,
            )

        assert written_ctx.get("fixer_round") == 1, (
            f"start_fixer must write fixer_round=1 for first call; "
            f"update_context received: {written_ctx!r}"
        )
        assert len(captured_create_calls) == 1, (
            f"start_fixer must call BKD create_issue once for first round; "
            f"called {len(captured_create_calls)} time(s)"
        )
        all_tags = captured_create_calls[0]["tags"]
        assert "round:1" in all_tags, (
            f"First fixer issue must include tag 'round:1'; got tags: {all_tags!r}"
        )

    async def test_frc_s2_round_2_increments_to_3_and_tags_issue(self):
        """
        FRC-S2: ctx.fixer_round=2 → start_fixer must write fixer_round=3 and tag 'round:3'.
        """
        from orchestrator.actions._verifier import start_fixer

        ctx = {"fixer_round": 2}
        settings = _make_settings(fixer_round_cap=5)

        written_ctx: dict = {}
        mock_req_state = AsyncMock()

        async def _capture_ctx(*args, **updates):
            written_ctx.update(updates)
            for a in args:
                if isinstance(a, dict):
                    written_ctx.update(a)

        mock_req_state.update_context.side_effect = _capture_ctx

        captured_tags: list = []

        async def _capture_create(title=None, tags=None, **kwargs):
            captured_tags.append(tags or [])
            issue = MagicMock()
            issue.id = "fixer-issue-3"
            return issue

        mock_bkd_inner = AsyncMock()
        mock_bkd_inner.create_issue.side_effect = _capture_create
        mock_bkd_inst = AsyncMock()
        mock_bkd_inst.__aenter__ = AsyncMock(return_value=mock_bkd_inner)
        mock_bkd_cls = MagicMock(return_value=mock_bkd_inst)

        patches = _start_fixer_patches(settings, mock_req_state, mock_bkd_cls)
        with patches[0], patches[1], patches[2], patches[3]:
            await start_fixer(
                body=_make_body(),
                req_id="REQ-test",
                tags=[],
                ctx=ctx,
            )

        assert written_ctx.get("fixer_round") == 3, (
            f"With fixer_round=2, must write fixer_round=3; "
            f"update_context received: {written_ctx!r}"
        )
        if captured_tags:
            assert "round:3" in captured_tags[0], (
                f"Issue must be tagged 'round:3'; got tags: {captured_tags[0]!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: escalate hard reason preservation  FRC-S6
# ─────────────────────────────────────────────────────────────────────────────


class TestEscalateHardReasonFRCS6:
    """Spec: 'fixer-round-cap' is a hard reason that ctx preserves over body.event."""

    def test_frc_s6_hard_reason_registered(self):
        """_HARD_REASONS must contain 'fixer-round-cap'."""
        import orchestrator.actions.escalate as escalate_mod

        hard_reasons = getattr(escalate_mod, "_HARD_REASONS", None)
        assert hard_reasons is not None, (
            "escalate must define _HARD_REASONS; attribute not found"
        )
        assert "fixer-round-cap" in hard_reasons, (
            f"'fixer-round-cap' must be in _HARD_REASONS; found: {hard_reasons!r}"
        )

    @pytest.mark.parametrize("body_event", ["watchdog.stuck", "session.failed"])
    def test_frc_s6_fixer_round_cap_not_transient_for_canonical_events(self, body_event):
        """
        FRC-S6: _is_transient(body_event, 'fixer-round-cap') must return False
        for canonical signal body events.
        """
        from orchestrator.actions.escalate import _is_transient

        result = _is_transient(body_event, "fixer-round-cap")
        assert result is False, (
            f"_is_transient({body_event!r}, 'fixer-round-cap') returned True; "
            "this would cause auto-resume of the fixer, defeating the cap"
        )

    def test_frc_s6_hard_and_canonical_signals_dont_overlap(self):
        """
        Structural: _HARD_REASONS and _CANONICAL_SIGNALS must not overlap.
        """
        import orchestrator.actions.escalate as escalate_mod

        canonical = getattr(escalate_mod, "_CANONICAL_SIGNALS", set())
        hard = getattr(escalate_mod, "_HARD_REASONS", set())

        if canonical and hard:
            overlap = hard & canonical
            assert not overlap, (
                f"_HARD_REASONS and _CANONICAL_SIGNALS must not overlap; "
                f"overlap: {overlap!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Part 5: watchdog defense-in-depth  FRC-S8, S9
# Signature: _check_and_escalate(row: dict) -> bool
# Row keys: req_id, project_id, state, context, stuck_sec, updated_at, intent_issue_id
# Patches: watchdog.settings, watchdog.req_state, watchdog.engine, watchdog.db
# ─────────────────────────────────────────────────────────────────────────────


class TestWatchdogDefenseFRCS8S9:
    """
    Spec: watchdog._check_and_escalate MUST write escalated_reason='fixer-round-cap'
    before SESSION_FAILED when state=FIXER_RUNNING and fixer_round>=cap.
    """

    def _make_watchdog_patches(self, settings, mock_req_state, mock_engine):
        from orchestrator import watchdog as wd
        return [
            patch.object(wd, "settings", settings),
            patch.object(wd, "req_state", mock_req_state),
            patch.object(wd, "engine", mock_engine),
            patch("orchestrator.store.db.get_pool", return_value=_make_mock_pool()),
        ]

    async def test_frc_s8_watchdog_marks_fixer_round_cap_when_cap_exceeded(self):
        """
        FRC-S8: state=FIXER_RUNNING, ctx.fixer_round=5, cap=5, stuck>threshold →
        req_state.update_context must be called with escalated_reason='fixer-round-cap'.
        """
        from orchestrator import watchdog as watchdog_mod
        from orchestrator.state import ReqState

        ctx = {"fixer_round": 5}
        settings = _make_settings(fixer_round_cap=5)

        written_ctx: dict = {}
        mock_req_state = AsyncMock()

        async def _capture_ctx(*args, **updates):
            written_ctx.update(updates)
            for a in args:
                if isinstance(a, dict):
                    written_ctx.update(a)

        mock_req_state.update_context.side_effect = _capture_ctx

        mock_engine = MagicMock()
        mock_engine.step = AsyncMock()

        state_str = (
            ReqState.FIXER_RUNNING.value
            if hasattr(ReqState.FIXER_RUNNING, "value")
            else str(ReqState.FIXER_RUNNING)
        )
        row = _make_req_row(state_val=state_str, ctx=ctx, stuck_secs=3600)

        p = self._make_watchdog_patches(settings, mock_req_state, mock_engine)
        with p[0], p[1], p[2], p[3]:
            await watchdog_mod._check_and_escalate(row)

        assert written_ctx.get("escalated_reason") == "fixer-round-cap", (
            f"watchdog must write escalated_reason='fixer-round-cap' when "
            f"FIXER_RUNNING and fixer_round(5) >= cap(5); "
            f"update_context received: {written_ctx!r}"
        )

    async def test_frc_s9_watchdog_does_not_mark_fixer_round_cap_when_below_cap(self):
        """
        FRC-S9: state=FIXER_RUNNING, ctx.fixer_round=2, cap=5, stuck>threshold →
        req_state.update_context must NOT write escalated_reason='fixer-round-cap'.
        """
        from orchestrator import watchdog as watchdog_mod
        from orchestrator.state import ReqState

        ctx = {"fixer_round": 2}
        settings = _make_settings(fixer_round_cap=5)

        written_ctx: dict = {}
        mock_req_state = AsyncMock()

        async def _capture_ctx(*args, **updates):
            written_ctx.update(updates)
            for a in args:
                if isinstance(a, dict):
                    written_ctx.update(a)

        mock_req_state.update_context.side_effect = _capture_ctx

        mock_engine = MagicMock()
        mock_engine.step = AsyncMock()

        state_str = (
            ReqState.FIXER_RUNNING.value
            if hasattr(ReqState.FIXER_RUNNING, "value")
            else str(ReqState.FIXER_RUNNING)
        )
        row = _make_req_row(state_val=state_str, ctx=ctx, stuck_secs=3600)

        p = self._make_watchdog_patches(settings, mock_req_state, mock_engine)
        with p[0], p[1], p[2], p[3]:
            await watchdog_mod._check_and_escalate(row)

        assert written_ctx.get("escalated_reason") != "fixer-round-cap", (
            f"watchdog must NOT write escalated_reason='fixer-round-cap' when "
            f"fixer_round(2) < cap(5); got: {written_ctx!r}"
        )
