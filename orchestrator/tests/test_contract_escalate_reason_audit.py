"""Contract tests for REQ-escalate-reason-audit-1777084279: ctx.escalated_reason pre-population.

Black-box behavioral contract verification written by challenger-agent.
Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered per spec.md:
  ESC-S1  INTAKE_FAIL → engine pre-populates ctx.escalated_reason = "intake-fail"
  ESC-S2  PR_CI_TIMEOUT → engine pre-populates ctx.escalated_reason = "pr-ci-timeout"
  ESC-S3  ACCEPT_ENV_UP_FAIL → engine pre-populates ctx.escalated_reason = "accept-env-up-fail"
  ESC-S4  VERIFY_ESCALATE → engine pre-populates ctx.escalated_reason = "verifier-decision-escalate"
  ESC-S5  SESSION_FAILED → engine must NOT pre-populate (escalate.py handles this path)
  ESC-S6  existing "action-error:..." ctx value must not be overwritten by engine
  ESC-S7  escalate.py uses pre-filled ctx.escalated_reason as final_reason
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from orchestrator.state import Event, ReqState


# ─── Contract 1: _EVENT_TO_ESCALATE_REASON mapping struct ───────────────────


class TestEventToEscalateReasonMapping:
    """Spec: _EVENT_TO_ESCALATE_REASON dict must exist in orchestrator.engine with 4 exact entries."""

    def test_symbol_exists_and_is_dict(self):
        from orchestrator.engine import _EVENT_TO_ESCALATE_REASON

        assert isinstance(_EVENT_TO_ESCALATE_REASON, dict), (
            "_EVENT_TO_ESCALATE_REASON must be a dict in orchestrator.engine"
        )

    def test_has_exactly_four_entries(self):
        from orchestrator.engine import _EVENT_TO_ESCALATE_REASON

        assert len(_EVENT_TO_ESCALATE_REASON) == 4, (
            f"_EVENT_TO_ESCALATE_REASON must have exactly 4 entries, "
            f"got {len(_EVENT_TO_ESCALATE_REASON)}: {list(_EVENT_TO_ESCALATE_REASON)}"
        )

    def test_intake_fail_maps_to_intake_fail_slug(self):
        from orchestrator.engine import _EVENT_TO_ESCALATE_REASON

        assert _EVENT_TO_ESCALATE_REASON[Event.INTAKE_FAIL] == "intake-fail", (
            f"INTAKE_FAIL must map to 'intake-fail', "
            f"got {_EVENT_TO_ESCALATE_REASON.get(Event.INTAKE_FAIL)!r}"
        )

    def test_pr_ci_timeout_maps_to_pr_ci_timeout_slug(self):
        from orchestrator.engine import _EVENT_TO_ESCALATE_REASON

        assert _EVENT_TO_ESCALATE_REASON[Event.PR_CI_TIMEOUT] == "pr-ci-timeout", (
            f"PR_CI_TIMEOUT must map to 'pr-ci-timeout', "
            f"got {_EVENT_TO_ESCALATE_REASON.get(Event.PR_CI_TIMEOUT)!r}"
        )

    def test_accept_env_up_fail_maps_to_accept_env_up_fail_slug(self):
        from orchestrator.engine import _EVENT_TO_ESCALATE_REASON

        assert _EVENT_TO_ESCALATE_REASON[Event.ACCEPT_ENV_UP_FAIL] == "accept-env-up-fail", (
            f"ACCEPT_ENV_UP_FAIL must map to 'accept-env-up-fail', "
            f"got {_EVENT_TO_ESCALATE_REASON.get(Event.ACCEPT_ENV_UP_FAIL)!r}"
        )

    def test_verify_escalate_maps_to_verifier_decision_escalate_slug(self):
        from orchestrator.engine import _EVENT_TO_ESCALATE_REASON

        assert _EVENT_TO_ESCALATE_REASON[Event.VERIFY_ESCALATE] == "verifier-decision-escalate", (
            f"VERIFY_ESCALATE must map to 'verifier-decision-escalate', "
            f"got {_EVENT_TO_ESCALATE_REASON.get(Event.VERIFY_ESCALATE)!r}"
        )

    def test_session_failed_not_in_mapping(self):
        """SESSION_FAILED is handled by escalate.py canonical signals; must NOT be pre-populated."""
        from orchestrator.engine import _EVENT_TO_ESCALATE_REASON

        assert Event.SESSION_FAILED not in _EVENT_TO_ESCALATE_REASON, (
            "SESSION_FAILED must NOT be in _EVENT_TO_ESCALATE_REASON — "
            "escalate.py handles it via body.event canonical signals (session.failed/watchdog.stuck)"
        )

    def test_mapping_values_are_all_strings(self):
        from orchestrator.engine import _EVENT_TO_ESCALATE_REASON

        for event, slug in _EVENT_TO_ESCALATE_REASON.items():
            assert isinstance(slug, str), (
                f"All slugs must be str; {event} maps to {slug!r}"
            )

    def test_slugs_use_hyphen_not_underscore(self):
        """Canonical slugs per spec use hyphens, not underscores."""
        from orchestrator.engine import _EVENT_TO_ESCALATE_REASON

        for event, slug in _EVENT_TO_ESCALATE_REASON.items():
            assert "_" not in slug, (
                f"Canonical slug must use hyphens, not underscores; {event} → {slug!r}"
            )


# ─── Shared test helper ──────────────────────────────────────────────────────


def _make_body(event_type: str = "session.completed") -> Any:
    body = MagicMock()
    body.event = event_type
    body.issue_id = "issue-test"
    body.project_id = "proj-test"
    body.execution_id = "exec-test"
    return body


def _setup_engine_mocks(monkeypatch) -> dict[str, AsyncMock]:
    """Patch all engine.step dependencies except the action under test.

    Returns dict of mocks for assertion.
    """
    import orchestrator.actions as actions_mod
    import orchestrator.k8s_runner as k8s_mod
    import orchestrator.observability as obs_mod
    from orchestrator.store import req_state as rs_mod, stage_runs as sr_mod

    mocks: dict[str, AsyncMock] = {}

    # CAS always succeeds
    cas = AsyncMock(return_value=True)
    monkeypatch.setattr(rs_mod, "cas_transition", cas)
    mocks["cas_transition"] = cas

    # update_context: capture calls for assertion
    uc = AsyncMock(return_value=None)
    monkeypatch.setattr(rs_mod, "update_context", uc)
    mocks["update_context"] = uc

    # stage_runs: record lifecycle events
    sr_start = AsyncMock(return_value=None)
    sr_end = AsyncMock(return_value=None)
    monkeypatch.setattr(sr_mod, "record_start", sr_start)
    monkeypatch.setattr(sr_mod, "record_end", sr_end)
    mocks["record_start"] = sr_start
    mocks["record_end"] = sr_end

    # K8s runner cleanup (called on terminal states like ESCALATED)
    kr = AsyncMock(return_value=None)
    monkeypatch.setattr(k8s_mod, "cleanup_runner", kr)
    mocks["cleanup_runner"] = kr

    # observability
    obs = AsyncMock(return_value=None)
    monkeypatch.setattr(obs_mod, "record_event", obs)
    mocks["record_event"] = obs

    # Replace REGISTRY["escalate"] with mock that returns empty result
    escalate_mock = AsyncMock(return_value={})
    monkeypatch.setitem(actions_mod.REGISTRY, "escalate", escalate_mock)
    mocks["escalate_action"] = escalate_mock

    return mocks


# ─── Contract 2: engine.step pre-populates ctx.escalated_reason ─────────────


class TestEngineStepPrePopulatesEscalatedReason:
    """ESC-S1 through S4: engine.step must call update_context with escalated_reason slug
    before dispatching the escalate action for mapped events."""

    @pytest.mark.parametrize("cur_state,event,expected_slug", [
        (ReqState.INTAKING,       Event.INTAKE_FAIL,       "intake-fail"),
        (ReqState.PR_CI_RUNNING,  Event.PR_CI_TIMEOUT,     "pr-ci-timeout"),
        (ReqState.ACCEPT_RUNNING, Event.ACCEPT_ENV_UP_FAIL, "accept-env-up-fail"),
        (ReqState.REVIEW_RUNNING, Event.VERIFY_ESCALATE,   "verifier-decision-escalate"),
    ])
    async def test_pre_populates_ctx_before_escalate(
        self, monkeypatch, cur_state, event, expected_slug
    ):
        """ESC-S1/S2/S3/S4: ctx.escalated_reason must be written to DB before escalate runs."""
        from orchestrator import engine

        mocks = _setup_engine_mocks(monkeypatch)
        pool = MagicMock()

        await engine.step(
            pool,
            body=_make_body(),
            req_id="REQ-test-esc",
            project_id="proj-test",
            tags=["REQ-test-esc"],
            cur_state=cur_state,
            ctx={},
            event=event,
        )

        # update_context must have been called with the canonical reason slug
        uc: AsyncMock = mocks["update_context"]
        reason_update_calls = [
            c for c in uc.call_args_list
            if c.args[2].get("escalated_reason") == expected_slug
            if len(c.args) >= 3 and isinstance(c.args[2], dict)
        ]
        assert reason_update_calls, (
            f"engine.step({cur_state.value}, {event.value}) must call "
            f"store.req_state.update_context(..., {{'escalated_reason': {expected_slug!r}}}) "
            f"before dispatching escalate. "
            f"Actual update_context calls: {uc.call_args_list}"
        )

    @pytest.mark.parametrize("cur_state,event,expected_slug", [
        (ReqState.INTAKING,       Event.INTAKE_FAIL,       "intake-fail"),
        (ReqState.PR_CI_RUNNING,  Event.PR_CI_TIMEOUT,     "pr-ci-timeout"),
        (ReqState.ACCEPT_RUNNING, Event.ACCEPT_ENV_UP_FAIL, "accept-env-up-fail"),
        (ReqState.REVIEW_RUNNING, Event.VERIFY_ESCALATE,   "verifier-decision-escalate"),
    ])
    async def test_escalate_action_receives_populated_ctx(
        self, monkeypatch, cur_state, event, expected_slug
    ):
        """ESC-S1/S2/S3/S4: the escalate action must receive ctx with escalated_reason already set."""
        from orchestrator import engine

        captured_ctx: list[dict] = []

        async def _capture_escalate(**kwargs):
            captured_ctx.append(dict(kwargs.get("ctx", {})))
            return {}

        import orchestrator.actions as actions_mod
        import orchestrator.k8s_runner as k8s_mod
        import orchestrator.observability as obs_mod
        from orchestrator.store import req_state as rs_mod, stage_runs as sr_mod

        monkeypatch.setattr(rs_mod, "cas_transition", AsyncMock(return_value=True))
        monkeypatch.setattr(rs_mod, "update_context", AsyncMock(return_value=None))
        monkeypatch.setattr(sr_mod, "record_start", AsyncMock(return_value=None))
        monkeypatch.setattr(sr_mod, "record_end", AsyncMock(return_value=None))
        monkeypatch.setattr(k8s_mod, "cleanup_runner", AsyncMock(return_value=None))
        monkeypatch.setattr(obs_mod, "record_event", AsyncMock(return_value=None))
        monkeypatch.setitem(actions_mod.REGISTRY, "escalate", _capture_escalate)

        await engine.step(
            MagicMock(),
            body=_make_body(),
            req_id="REQ-test-ctx",
            project_id="proj-test",
            tags=["REQ-test-ctx"],
            cur_state=cur_state,
            ctx={},
            event=event,
        )

        assert captured_ctx, (
            f"escalate action must have been called for {cur_state.value}/{event.value}"
        )
        received_reason = captured_ctx[0].get("escalated_reason")
        assert received_reason == expected_slug, (
            f"escalate action must receive ctx.escalated_reason == {expected_slug!r}, "
            f"got {received_reason!r}. Full ctx: {captured_ctx[0]}"
        )


# ─── Contract 3: SESSION_FAILED must NOT be pre-populated (ESC-S5) ──────────


class TestSessionFailedNoPrePopulation:
    """ESC-S5: engine.step with SESSION_FAILED must NOT pre-populate ctx.escalated_reason.
    escalate.py handles this path via body.event canonical signals."""

    @pytest.mark.parametrize("cur_state", [
        ReqState.STAGING_TEST_RUNNING,
        ReqState.PR_CI_RUNNING,
        ReqState.REVIEW_RUNNING,
        ReqState.ANALYZING,
    ])
    async def test_session_failed_does_not_write_escalated_reason(
        self, monkeypatch, cur_state
    ):
        """ESC-S5: no update_context call with escalated_reason for SESSION_FAILED path."""
        from orchestrator import engine

        mocks = _setup_engine_mocks(monkeypatch)

        await engine.step(
            MagicMock(),
            body=_make_body(event_type="session.failed"),
            req_id="REQ-test-sf",
            project_id="proj-test",
            tags=["REQ-test-sf"],
            cur_state=cur_state,
            ctx={},
            event=Event.SESSION_FAILED,
        )

        uc: AsyncMock = mocks["update_context"]
        # Must not write escalated_reason from the engine pre-populate path
        pre_populate_calls = [
            c for c in uc.call_args_list
            if len(c.args) >= 3 and isinstance(c.args[2], dict)
            and "escalated_reason" in c.args[2]
            and not c.args[2]["escalated_reason"].startswith("action-error:")
            # session.failed and watchdog.stuck are the canonical slugs from escalate.py —
            # those are written by escalate.py itself, not by engine pre-population.
            and c.args[2]["escalated_reason"] not in ("session-failed", "watchdog-stuck")
        ]
        assert not pre_populate_calls, (
            f"engine.step(SESSION_FAILED) must NOT call update_context with an "
            f"engine-derived escalated_reason slug. Got pre-populate calls: {pre_populate_calls}"
        )


# ─── Contract 4: action-error prefix preserved (ESC-S6) ─────────────────────


class TestActionErrorPrefixPreserved:
    """ESC-S6: if ctx.escalated_reason already starts with 'action-error:', engine must not overwrite."""

    async def test_action_error_ctx_not_overwritten_by_engine(self, monkeypatch):
        """ESC-S6: pre-existing action-error:... reason must survive through engine.step."""
        from orchestrator import engine

        existing_reason = "action-error:RuntimeError: pod not ready"
        captured_ctx: list[dict] = []

        async def _capture_escalate(**kwargs):
            captured_ctx.append(dict(kwargs.get("ctx", {})))
            return {}

        import orchestrator.actions as actions_mod
        import orchestrator.k8s_runner as k8s_mod
        import orchestrator.observability as obs_mod
        from orchestrator.store import req_state as rs_mod, stage_runs as sr_mod

        monkeypatch.setattr(rs_mod, "cas_transition", AsyncMock(return_value=True))
        monkeypatch.setattr(rs_mod, "update_context", AsyncMock(return_value=None))
        monkeypatch.setattr(sr_mod, "record_start", AsyncMock(return_value=None))
        monkeypatch.setattr(sr_mod, "record_end", AsyncMock(return_value=None))
        monkeypatch.setattr(k8s_mod, "cleanup_runner", AsyncMock(return_value=None))
        monkeypatch.setattr(obs_mod, "record_event", AsyncMock(return_value=None))
        monkeypatch.setitem(actions_mod.REGISTRY, "escalate", _capture_escalate)

        # SESSION_FAILED with existing action-error: prefix in ctx
        await engine.step(
            MagicMock(),
            body=_make_body(event_type="session.failed"),
            req_id="REQ-test-ae",
            project_id="proj-test",
            tags=["REQ-test-ae"],
            cur_state=ReqState.STAGING_TEST_RUNNING,
            ctx={"escalated_reason": existing_reason},
            event=Event.SESSION_FAILED,
        )

        assert captured_ctx, "escalate action must have been called"
        received_reason = captured_ctx[0].get("escalated_reason")
        assert received_reason == existing_reason, (
            f"engine must NOT overwrite existing action-error: reason. "
            f"Expected {existing_reason!r}, got {received_reason!r}"
        )

    async def test_action_error_check_is_prefix_based(self, monkeypatch):
        """ESC-S6: any value starting with 'action-error:' (not just a specific one) must be preserved."""
        from orchestrator import engine

        for variant_reason in [
            "action-error:TimeoutError: k8s timeout",
            "action-error:ConnectionRefusedError",
            "action-error:",  # degenerate edge case
        ]:
            captured_ctx: list[dict] = []

            async def _capture(ctx_val=captured_ctx, **kwargs):
                ctx_val.append(dict(kwargs.get("ctx", {})))
                return {}

            import orchestrator.actions as actions_mod
            import orchestrator.k8s_runner as k8s_mod
            import orchestrator.observability as obs_mod
            from orchestrator.store import req_state as rs_mod, stage_runs as sr_mod

            monkeypatch.setattr(rs_mod, "cas_transition", AsyncMock(return_value=True))
            monkeypatch.setattr(rs_mod, "update_context", AsyncMock(return_value=None))
            monkeypatch.setattr(sr_mod, "record_start", AsyncMock(return_value=None))
            monkeypatch.setattr(sr_mod, "record_end", AsyncMock(return_value=None))
            monkeypatch.setattr(k8s_mod, "cleanup_runner", AsyncMock(return_value=None))
            monkeypatch.setattr(obs_mod, "record_event", AsyncMock(return_value=None))
            monkeypatch.setitem(actions_mod.REGISTRY, "escalate", _capture)

            await engine.step(
                MagicMock(),
                body=_make_body(event_type="session.failed"),
                req_id="REQ-test-ae2",
                project_id="proj-test",
                tags=["REQ-test-ae2"],
                cur_state=ReqState.STAGING_TEST_RUNNING,
                ctx={"escalated_reason": variant_reason},
                event=Event.SESSION_FAILED,
            )

            assert captured_ctx, f"escalate not called for variant_reason={variant_reason!r}"
            received = captured_ctx[0].get("escalated_reason")
            assert received == variant_reason, (
                f"action-error prefix variant {variant_reason!r} must be preserved, got {received!r}"
            )


# ─── Contract 5: escalate.py uses pre-filled ctx value (ESC-S7) ─────────────


class TestEscalateActionUsesPrefilledReason:
    """ESC-S7: when ctx.escalated_reason is pre-filled, escalate.py must use it as final_reason
    and persist it back to ctx + add the matching reason: tag to BKD intent issue."""

    async def test_escalate_uses_ctx_reason_as_final_reason(self, monkeypatch):
        """ESC-S7: body.event='session.completed' + ctx.escalated_reason set → final_reason equals slug."""
        import orchestrator.observability as obs_mod
        from orchestrator.actions import escalate as escalate_mod
        from orchestrator.store import req_state as rs_mod

        # Track update_context calls to verify persistence
        ctx_updates: list[dict] = []

        async def _fake_update_context(pool, req_id, patch):
            ctx_updates.append(patch)

        monkeypatch.setattr(rs_mod, "update_context", _fake_update_context)
        monkeypatch.setattr(obs_mod, "record_event", AsyncMock(return_value=None))

        # BKD mock: captures which tags are added
        added_tags: list[str] = []

        class _FakeBKD:
            def __init__(self, *a, **kw): pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get_issue(self, issue_id):
                class _Issue:
                    tags = ["REQ-test-esc-s7", "analyze"]
                return _Issue()

            async def update_issue(self, issue_id, *, tags, **kw):
                added_tags.extend(tags)

        import orchestrator.bkd as bkd_mod
        monkeypatch.setattr(bkd_mod, "BKDClient", _FakeBKD)

        pre_filled_reason = "verifier-decision-escalate"
        body = _make_body(event_type="session.completed")
        body.issue_id = "bkd-intent-issue-id"

        # Call escalate directly with pre-filled ctx (simulating engine pre-population)
        await escalate_mod.escalate(
            pool=MagicMock(),
            body=body,
            req_id="REQ-test-esc-s7",
            tags=["REQ-test-esc-s7"],
            ctx={
                "escalated_reason": pre_filled_reason,
                "auto_retry_count": 0,
                "bkd_intent_issue_id": "bkd-intent-issue-id",
            },
        )

        # ESC-S7 contract: final ctx must persist the pre-filled reason
        reason_persisted = any(
            p.get("escalated_reason") == pre_filled_reason
            for p in ctx_updates
        )
        assert reason_persisted, (
            f"escalate.py must persist ctx.escalated_reason={pre_filled_reason!r}. "
            f"Actual update_context calls: {ctx_updates}"
        )

        # ESC-S7 contract: BKD intent issue must receive reason:<slug> tag
        expected_tag = f"reason:{pre_filled_reason}"
        assert expected_tag in added_tags, (
            f"BKD intent issue must receive tag {expected_tag!r}. "
            f"Actual tags in update_issue call: {added_tags}"
        )

    async def test_session_completed_non_canonical_uses_ctx_reason(self, monkeypatch):
        """ESC-S7 variant: body.event='issue.updated' (non-canonical) also falls through to ctx reason."""
        import orchestrator.observability as obs_mod
        from orchestrator.actions import escalate as escalate_mod
        from orchestrator.store import req_state as rs_mod

        ctx_updates: list[dict] = []

        async def _fake_update_context(pool, req_id, patch):
            ctx_updates.append(patch)

        monkeypatch.setattr(rs_mod, "update_context", _fake_update_context)
        monkeypatch.setattr(obs_mod, "record_event", AsyncMock(return_value=None))

        added_tags: list[str] = []

        class _FakeBKD:
            def __init__(self, *a, **kw): pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get_issue(self, issue_id):
                class _Issue:
                    tags = ["REQ-test-esc-s7b", "pr-ci"]
                return _Issue()

            async def update_issue(self, issue_id, *, tags, **kw):
                added_tags.extend(tags)

        import orchestrator.bkd as bkd_mod
        monkeypatch.setattr(bkd_mod, "BKDClient", _FakeBKD)

        pre_filled = "pr-ci-timeout"
        body = _make_body(event_type="issue.updated")
        body.issue_id = "bkd-intent-issue-id"

        await escalate_mod.escalate(
            pool=MagicMock(),
            body=body,
            req_id="REQ-test-esc-s7b",
            tags=["REQ-test-esc-s7b"],
            ctx={
                "escalated_reason": pre_filled,
                "auto_retry_count": 0,
                "bkd_intent_issue_id": "bkd-intent-issue-id",
            },
        )

        reason_persisted = any(
            p.get("escalated_reason") == pre_filled
            for p in ctx_updates
        )
        assert reason_persisted, (
            f"escalate.py must use pre-filled ctx.escalated_reason={pre_filled!r} "
            f"for non-canonical body.event. Actual updates: {ctx_updates}"
        )

        expected_tag = f"reason:{pre_filled}"
        assert expected_tag in added_tags, (
            f"BKD tag {expected_tag!r} must be set. Actual: {added_tags}"
        )
