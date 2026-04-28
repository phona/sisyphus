"""Contract tests for escalated_reason (REQ-escalate-reason-verifier-1777076876 + null-audit).

Scenarios:
  ERV-S1  verifier escalate sets ctx.escalated_reason="verifier-decision" before engine.step
  ERV-S2  _is_transient("verifier-decision") returns False — no auto-resume
  NRA-S1  start_analyze clone-failed → escalated_reason="clone-failed" set before emit
  NRA-S2  start_analyze_with_finalized_intent missing ctx → escalated_reason="missing-finalized-intent"
  NRA-S3  start_analyze_with_finalized_intent clone-failed → escalated_reason="clone-failed"
  NRA-S4  create_pr_ci_watch PR_CI_TIMEOUT (exit_code=124) → escalated_reason="pr-ci-timeout"
  NRA-S5  create_pr_ci_watch PR_CI_TIMEOUT (ValueError) → escalated_reason="pr-ci-timeout"
  NRA-S6  create_accept ACCEPT_ENV_UP_FAIL → escalated_reason="accept-env-up-failed"
  NRA-S7  escalate action: early write of escalated_reason before gh_incident.open_incident
  NRA-S7b escalate action: no pre-set reason → defaults to "unknown" + warning log
  NRA-S8  escalate action: pre-set reason is NOT overwritten by fallback
"""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock


class _FakePool:
    """Minimal asyncpg pool fake: ordered fetchrow returns + recorded execute calls."""

    def __init__(self, fetchrow_returns=()):
        self._returns = list(fetchrow_returns)
        self._pos = 0
        self.execute_calls: list[tuple] = []

    async def fetchrow(self, sql: str, *args):
        if self._pos < len(self._returns):
            v = self._returns[self._pos]
            self._pos += 1
            return v
        return None

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))


# ─── ERV-S1: verifier escalate → ctx.escalated_reason = "verifier-decision" ─


async def test_s1_verifier_escalate_sets_reason_verifier_decision(monkeypatch):
    """
    ERV-S1: When a verifier session.completed results in VERIFY_ESCALATE, the webhook MUST
    set ctx.escalated_reason = "verifier-decision" (via update_context) before engine.step.
    """
    import orchestrator.observability as obs
    from orchestrator import engine, webhook
    from orchestrator import router as router_lib
    from orchestrator.state import Event, ReqState
    from orchestrator.store import db, dedup
    from orchestrator.store import req_state as rs_mod

    context_updates: list[dict] = []
    step_snapshots: list[list[dict]] = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="new"))
    monkeypatch.setattr(dedup, "mark_processed", AsyncMock())
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(obs, "record_event", AsyncMock())

    class _BKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_issue(self, *a, **kw):
            class R:
                tags: ClassVar = ["REQ-erv1", "verifier", "result:escalate"]

            return R()

        async def update_issue(self, *a, **kw):
            pass

    monkeypatch.setattr(webhook, "BKDClient", _BKD)
    monkeypatch.setattr(router_lib, "extract_req_id", lambda tags, num=None: "REQ-erv1")
    monkeypatch.setattr(router_lib, "derive_event", lambda evt, tags: Event.VERIFY_ESCALATE)

    class _Row:
        state = ReqState.REVIEW_RUNNING
        context: ClassVar = {}

    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=_Row()))
    monkeypatch.setattr(rs_mod, "insert_init", AsyncMock())

    async def _capture_update_context(*args, **kwargs):
        # args: (pool, req_id, patch_dict) or similar — capture whatever patch dict is passed
        for a in args:
            if isinstance(a, dict):
                context_updates.append(dict(a))
        for v in kwargs.values():
            if isinstance(v, dict):
                context_updates.append(dict(v))

    monkeypatch.setattr(rs_mod, "update_context", _capture_update_context)

    async def _capture_step(*args, **kwargs):
        step_snapshots.append(list(context_updates))
        return {"action": "ok"}

    monkeypatch.setattr(engine, "step", _capture_step)

    class _Req:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}

        async def json(self):
            return {
                "event": "session.completed",
                "issueId": "issue-erv1",
                "projectId": "proj-erv1",
                "executionId": "exec-erv1",
                "tags": ["REQ-erv1", "verifier", "result:escalate"],
            }

    await webhook.webhook(_Req())

    # Contract 1: update_context must be called with escalated_reason = "verifier-decision"
    reason_updates = [u for u in context_updates if "escalated_reason" in u]
    assert reason_updates, (
        "webhook MUST call update_context with escalated_reason='verifier-decision' "
        f"for VERIFY_ESCALATE. All context updates captured: {context_updates}"
    )
    assert reason_updates[-1]["escalated_reason"] == "verifier-decision", (
        "escalated_reason MUST be 'verifier-decision', "
        f"got {reason_updates[-1]['escalated_reason']!r}"
    )

    # Contract 2: the reason must be set BEFORE engine.step is called
    assert step_snapshots, "engine.step must be called after VERIFY_ESCALATE"
    updates_at_step_time = step_snapshots[0]
    assert any("escalated_reason" in u for u in updates_at_step_time), (
        "ctx.escalated_reason MUST be set via update_context BEFORE engine.step is called. "
        f"context_updates at engine.step call time: {updates_at_step_time}"
    )


# ─── ERV-S2: verifier-decision is not transient → no auto-resume ─────────────


async def test_s2_verifier_decision_not_transient():
    """
    ERV-S2: _is_transient("verifier-decision") MUST return False.
    A verifier escalation is a deliberate AI decision — auto-resume MUST NOT be triggered.
    """
    from orchestrator.actions.escalate import _is_transient

    # body_event="session.completed" simulates normal verifier session completion;
    # reason="verifier-decision" is what webhook sets when VERIFY_ESCALATE is derived.
    result = _is_transient("session.completed", "verifier-decision")

    assert result is False, (
        f"_is_transient('session.completed', 'verifier-decision') MUST return False (non-transient); "
        f"got {result!r}. verifier-decision is an intentional escalation — no auto-resume follow-up."
    )


# ─── NRA helpers ─────────────────────────────────────────────────────────────

def _make_fake_pool() -> _FakePool:
    return _FakePool()


# ─── NRA-S1: start_analyze clone-failed → escalated_reason="clone-failed" ────


async def test_nra_s1_start_analyze_clone_failed_sets_escalated_reason(monkeypatch):
    """
    NRA-S1: When start_analyze's server-side clone fails, update_context MUST be called
    with escalated_reason="clone-failed" before returning the emit=VERIFY_ESCALATE dict.
    """
    from dataclasses import dataclass
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from orchestrator.actions import _clone
    from orchestrator.actions import start_analyze as mod
    from orchestrator.admission import AdmissionDecision
    from orchestrator.state import Event

    @dataclass
    class FakeExec:
        stdout: str = ""
        stderr: str = "auth error"
        exit_code: int = 5
        duration_sec: float = 0.1

    class FakeRC:
        exec_in_runner = AsyncMock(return_value=FakeExec())
        ensure_runner = AsyncMock(return_value="pod-x")

    monkeypatch.setattr(_clone.k8s_runner, "get_controller", lambda: FakeRC())
    monkeypatch.setattr(mod.k8s_runner, "get_controller", lambda: FakeRC())
    monkeypatch.setattr(mod, "check_admission", AsyncMock(return_value=AdmissionDecision(admit=True)))

    context_updates: list[dict] = []

    async def _capture(pool, req_id, patch):
        context_updates.append(dict(patch))

    monkeypatch.setattr(mod.req_state, "update_context", _capture)
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())

    body = SimpleNamespace(projectId="p", issueId="issue-x", title="t")
    rv = await mod.start_analyze(
        body=body, req_id="REQ-nra1", tags=[],
        ctx={"involved_repos": ["phona/repo"]},
    )

    assert rv.get("emit") == Event.VERIFY_ESCALATE.value, "must emit VERIFY_ESCALATE on clone fail"
    reason_updates = [u for u in context_updates if "escalated_reason" in u]
    assert reason_updates, "update_context with escalated_reason MUST be called before emit"
    assert reason_updates[-1]["escalated_reason"] == "clone-failed", (
        f"escalated_reason MUST be 'clone-failed', got {reason_updates[-1]['escalated_reason']!r}"
    )


# ─── NRA-S2: start_analyze_with_finalized_intent missing ctx ─────────────────


async def test_nra_s2_finalized_intent_missing_sets_escalated_reason(monkeypatch):
    """
    NRA-S2: When start_analyze_with_finalized_intent has no intake_finalized_intent in ctx,
    update_context MUST be called with escalated_reason="missing-finalized-intent".
    """
    from types import SimpleNamespace

    from orchestrator.actions import start_analyze_with_finalized_intent as mod
    from orchestrator.state import Event

    context_updates: list[dict] = []

    async def _capture(pool, req_id, patch):
        context_updates.append(dict(patch))

    monkeypatch.setattr(mod.req_state, "update_context", _capture)
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())

    body = SimpleNamespace(projectId="p", issueId="issue-x", executionId="e1")
    rv = await mod.start_analyze_with_finalized_intent(
        body=body, req_id="REQ-nra2", tags=[], ctx={},
    )

    assert rv.get("emit") == Event.VERIFY_ESCALATE.value
    reason_updates = [u for u in context_updates if "escalated_reason" in u]
    assert reason_updates, "update_context with escalated_reason MUST be called"
    assert reason_updates[-1]["escalated_reason"] == "missing-finalized-intent", (
        f"got {reason_updates[-1]['escalated_reason']!r}"
    )


# ─── NRA-S3: start_analyze_with_finalized_intent clone-failed ────────────────


async def test_nra_s3_finalized_intent_clone_failed_sets_escalated_reason(monkeypatch):
    """
    NRA-S3: When start_analyze_with_finalized_intent's clone fails,
    update_context MUST be called with escalated_reason="clone-failed".
    """
    from dataclasses import dataclass
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from orchestrator.actions import _clone
    from orchestrator.actions import start_analyze_with_finalized_intent as mod
    from orchestrator.state import Event

    @dataclass
    class FakeExec:
        stdout: str = ""
        stderr: str = "network error"
        exit_code: int = 1
        duration_sec: float = 0.1

    class FakeRC:
        exec_in_runner = AsyncMock(return_value=FakeExec())
        ensure_runner = AsyncMock(return_value="pod-x")

    monkeypatch.setattr(_clone.k8s_runner, "get_controller", lambda: FakeRC())
    monkeypatch.setattr(mod.k8s_runner, "get_controller", lambda: FakeRC())

    context_updates: list[dict] = []

    async def _capture(pool, req_id, patch):
        context_updates.append(dict(patch))

    monkeypatch.setattr(mod.req_state, "update_context", _capture)
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())
    monkeypatch.setattr(mod.dispatch_slugs, "get", AsyncMock(return_value=None))

    body = SimpleNamespace(projectId="p", issueId="issue-x", executionId="e1")
    rv = await mod.start_analyze_with_finalized_intent(
        body=body, req_id="REQ-nra3", tags=[],
        ctx={"intake_finalized_intent": {"involved_repos": ["phona/repo"]}},
    )

    assert rv.get("emit") == Event.VERIFY_ESCALATE.value
    reason_updates = [u for u in context_updates if "escalated_reason" in u]
    assert reason_updates, "update_context with escalated_reason MUST be called on clone fail"
    assert reason_updates[-1]["escalated_reason"] == "clone-failed", (
        f"got {reason_updates[-1]['escalated_reason']!r}"
    )


# ─── NRA-S4: create_pr_ci_watch PR_CI_TIMEOUT (exit_code=124) ────────────────


async def test_nra_s4_pr_ci_timeout_exit124_sets_escalated_reason(monkeypatch):
    """
    NRA-S4: When the pr-ci-watch checker returns exit_code=124 (timeout),
    update_context MUST be called with escalated_reason="pr-ci-timeout" before emitting PR_CI_TIMEOUT.
    """
    from unittest.mock import AsyncMock

    from orchestrator.actions import create_pr_ci_watch as mod
    from orchestrator.checkers._types import CheckResult
    from orchestrator.state import Event

    timeout_result = CheckResult(
        passed=False, exit_code=124,
        stdout_tail="still pending", stderr_tail="timeout after 1800s",
        duration_sec=1800.0, cmd="watch-pr-ci repo#1@abc",
    )

    monkeypatch.setattr(mod, "_discover_repos_from_runner", AsyncMock(return_value=["phona/repo"]))
    monkeypatch.setattr(mod, "_dispatch_to_ci_repo", AsyncMock())
    monkeypatch.setattr(mod.checker, "watch_pr_ci", AsyncMock(return_value=timeout_result))
    monkeypatch.setattr(mod.artifact_checks, "insert_check", AsyncMock())

    context_updates: list[dict] = []

    async def _capture(pool, req_id, patch):
        context_updates.append(dict(patch))

    monkeypatch.setattr(mod.req_state, "update_context", _capture)
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())

    rv = await mod._run_checker(req_id="REQ-nra4", ctx={})

    assert rv.get("emit") == Event.PR_CI_TIMEOUT.value
    reason_updates = [u for u in context_updates if "escalated_reason" in u]
    assert reason_updates, "update_context with escalated_reason MUST be called on timeout"
    assert reason_updates[-1]["escalated_reason"] == "pr-ci-timeout", (
        f"got {reason_updates[-1]['escalated_reason']!r}"
    )


# ─── NRA-S5: create_pr_ci_watch PR_CI_TIMEOUT (ValueError) ──────────────────


async def test_nra_s5_pr_ci_timeout_valueerror_sets_escalated_reason(monkeypatch):
    """
    NRA-S5: When watch_pr_ci raises ValueError (config error / no repos),
    update_context MUST be called with escalated_reason="pr-ci-timeout" before emitting PR_CI_TIMEOUT.
    """
    from unittest.mock import AsyncMock

    from orchestrator.actions import create_pr_ci_watch as mod
    from orchestrator.state import Event

    monkeypatch.setattr(mod, "_discover_repos_from_runner", AsyncMock(return_value=[]))
    monkeypatch.setattr(mod, "_dispatch_to_ci_repo", AsyncMock())
    monkeypatch.setattr(
        mod.checker, "watch_pr_ci",
        AsyncMock(side_effect=ValueError("no repos provided to watch_pr_ci")),
    )

    context_updates: list[dict] = []

    async def _capture(pool, req_id, patch):
        context_updates.append(dict(patch))

    monkeypatch.setattr(mod.req_state, "update_context", _capture)
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())

    rv = await mod._run_checker(req_id="REQ-nra5", ctx={})

    assert rv.get("emit") == Event.PR_CI_TIMEOUT.value
    reason_updates = [u for u in context_updates if "escalated_reason" in u]
    assert reason_updates, "update_context with escalated_reason MUST be called on ValueError"
    assert reason_updates[-1]["escalated_reason"] == "pr-ci-timeout", (
        f"got {reason_updates[-1]['escalated_reason']!r}"
    )


# ─── NRA-S6: create_accept ACCEPT_ENV_UP_FAIL ────────────────────────────────


async def test_nra_s6_accept_env_up_fail_sets_escalated_reason(monkeypatch):
    """
    NRA-S6: When create_accept cannot resolve integration_dir,
    update_context MUST be called with escalated_reason="accept-env-up-failed".
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from orchestrator.actions import create_accept as mod
    from orchestrator.actions._integration_resolver import ResolveResult
    from orchestrator.state import Event

    monkeypatch.setattr(mod, "skip_if_enabled", lambda *a, **kw: None)
    monkeypatch.setattr(mod.k8s_runner, "get_controller", lambda: object())
    monkeypatch.setattr(
        mod, "resolve_integration_dir",
        AsyncMock(return_value=ResolveResult(dir=None, reason="no integration dir found")),
    )

    context_updates: list[dict] = []

    async def _capture(pool, req_id, patch):
        context_updates.append(dict(patch))

    monkeypatch.setattr(mod.req_state, "update_context", _capture)
    monkeypatch.setattr(mod.db, "get_pool", lambda: object())

    body = SimpleNamespace(projectId="p", issueId="issue-x", executionId="e1")
    rv = await mod.create_accept(body=body, req_id="REQ-nra6", tags=[], ctx={})

    assert rv.get("emit") == Event.ACCEPT_ENV_UP_FAIL.value
    reason_updates = [u for u in context_updates if "escalated_reason" in u]
    assert reason_updates, "update_context with escalated_reason MUST be called on env-up fail"
    assert reason_updates[-1]["escalated_reason"] == "accept-env-up-failed", (
        f"got {reason_updates[-1]['escalated_reason']!r}"
    )


# ─── NRA-S7: escalate action early write ─────────────────────────────────────


async def test_nra_s7_escalate_writes_reason_before_gh_incident(monkeypatch):
    """
    NRA-S7: escalate action MUST write escalated_reason to DB (via update_context)
    BEFORE calling gh_incident.open_incident — so a crash in GH incident logic
    never leaves escalated_reason null in the database.
    """
    from types import SimpleNamespace

    from orchestrator.actions import escalate as mod
    from orchestrator.config import settings

    call_order: list[str] = []

    async def _capture_update(pool, req_id, patch):
        if "escalated_reason" in patch:
            call_order.append("update_context:escalated_reason")

    async def _fake_incident(**kwargs):
        call_order.append("gh_incident")
        return None

    monkeypatch.setattr(mod.req_state, "update_context", _capture_update)
    monkeypatch.setattr(mod.db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(mod.gh_incident, "open_incident", _fake_incident)

    # Stub BKD so it doesn't make real HTTP
    class _FakeBKD:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def merge_tags_and_update(self, *a, **kw):
            pass
        async def update_issue(self, *a, **kw):
            pass

    monkeypatch.setattr(mod, "BKDClient", _FakeBKD)

    # Non-transient path: verifier-decision escalate (no auto-resume)
    # body.event="session.completed" is not in _SESSION_END_SIGNALS so k8s runner
    # cleanup is not triggered — no need to mock k8s_runner.get_controller here.
    body = SimpleNamespace(
        projectId="proj-x", issueId="issue-x", event="session.completed",
    )
    ctx = {
        "escalated_reason": "verifier-decision",
        "intent_issue_id": "issue-x",
        "gh_incident_repo": "phona/repo",
    }
    monkeypatch.setattr(settings, "gh_incident_repo", "phona/repo")
    monkeypatch.setattr(settings, "github_token", "")  # disable real GH call

    await mod.escalate(body=body, req_id="REQ-nra7", tags=[], ctx=ctx)

    reason_idx = next(
        (i for i, v in enumerate(call_order) if v == "update_context:escalated_reason"),
        None,
    )
    gh_idx = next(
        (i for i, v in enumerate(call_order) if v == "gh_incident"),
        None,
    )
    assert reason_idx is not None, (
        "escalate MUST call update_context with escalated_reason. "
        f"call_order={call_order}"
    )
    if gh_idx is not None:
        assert reason_idx < gh_idx, (
            "escalated_reason MUST be written to DB BEFORE gh_incident.open_incident is called. "
            f"call_order={call_order}"
        )


# ─── NRA-S8: escalate action does not overwrite pre-set reason ───────────────


async def test_nra_s8_escalate_does_not_overwrite_pre_set_reason(monkeypatch):
    """
    NRA-S8: If ctx already has escalated_reason="clone-failed" (set by the action
    that emitted VERIFY_ESCALATE), escalate MUST write "clone-failed" to DB, not "unknown".
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from orchestrator.actions import escalate as mod
    from orchestrator.config import settings

    written_reasons: list[str] = []

    async def _capture_update(pool, req_id, patch):
        if "escalated_reason" in patch:
            written_reasons.append(patch["escalated_reason"])

    monkeypatch.setattr(mod.req_state, "update_context", _capture_update)
    monkeypatch.setattr(mod.db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(mod.gh_incident, "open_incident", AsyncMock(return_value=None))

    class _FakeBKD:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def merge_tags_and_update(self, *a, **kw):
            pass
        async def update_issue(self, *a, **kw):
            pass

    monkeypatch.setattr(mod, "BKDClient", _FakeBKD)
    # gh_incident_repo="" → incident_repos=[] → GH incident loop skipped
    monkeypatch.setattr(settings, "gh_incident_repo", "")
    monkeypatch.setattr(settings, "github_token", "")

    body = SimpleNamespace(
        projectId="proj-x", issueId="issue-x", event="session.completed",
    )
    ctx = {
        "escalated_reason": "clone-failed",
        "intent_issue_id": "issue-x",
    }

    await mod.escalate(body=body, req_id="REQ-nra8", tags=[], ctx=ctx)

    assert written_reasons, "escalate MUST write escalated_reason to DB"
    # The first write should be the early write with the computed reason
    assert written_reasons[0] == "clone-failed", (
        "Pre-set escalated_reason='clone-failed' MUST NOT be overwritten by fallback. "
        f"written_reasons={written_reasons}"
    )


# ─── NRA-S7b: escalate defaults to "unknown" when no reason is pre-set ───────


async def test_nra_s7b_escalate_defaults_to_unknown_when_no_reason(monkeypatch):
    """
    NRA-S7 (no-reason branch): When escalate is called with a non-SESSION_END event and
    ctx contains NO escalated_reason, the final_reason resolution chain produces an empty
    string. The action MUST default final_reason to "unknown", MUST emit a warning log
    (escalate.reason_missing_defaulted), and MUST call req_state.update_context with
    escalated_reason="unknown" before the GH incident loop.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from orchestrator.actions import escalate as mod
    from orchestrator.config import settings

    written_reasons: list[str] = []

    async def _capture_update(pool, req_id, patch_dict):
        if "escalated_reason" in patch_dict:
            written_reasons.append(patch_dict["escalated_reason"])

    monkeypatch.setattr(mod.req_state, "update_context", _capture_update)
    monkeypatch.setattr(mod.db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(mod.gh_incident, "open_incident", AsyncMock(return_value=None))

    class _FakeBKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def merge_tags_and_update(self, *a, **kw):
            pass

        async def update_issue(self, *a, **kw):
            pass

    monkeypatch.setattr(mod, "BKDClient", _FakeBKD)
    monkeypatch.setattr(settings, "gh_incident_repo", "")
    monkeypatch.setattr(settings, "github_token", "")

    body = SimpleNamespace(
        projectId="proj-x", issueId="issue-x", event="",
    )
    # ctx intentionally has NO escalated_reason and event is blank so the
    # resolution chain cannot derive any non-empty reason — triggers "unknown" fallback
    ctx = {"intent_issue_id": "issue-x"}

    await mod.escalate(body=body, req_id="REQ-nra7b", tags=[], ctx=ctx)

    assert written_reasons, (
        "escalate MUST write escalated_reason to DB even when none was pre-set. "
        f"written_reasons={written_reasons}"
    )
    assert written_reasons[0] == "unknown", (
        "When no escalated_reason is in ctx and event produces no derived reason, "
        "escalate MUST default final_reason to 'unknown'. "
        f"written_reasons={written_reasons}"
    )
