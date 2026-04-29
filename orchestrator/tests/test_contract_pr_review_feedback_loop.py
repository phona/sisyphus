"""Contract tests for REQ-user-feedback-loop-1777420881.

Scenarios:
  PR-WH-S1   approved review derives GH_PR_REVIEW_APPROVED
  PR-WH-S2   changes_requested review derives GH_PR_REVIEW_CHANGES_REQUESTED
  PR-WH-S3   commented review with "LGTM" derives GH_PR_REVIEW_APPROVED
  PR-WH-S4   commented review with "fix:" derives GH_PR_REVIEW_CHANGES_REQUESTED
  PR-WH-S5   commented review without keyword derives GH_PR_REVIEW_COMMENTED
  PR-WH-S6   unknown review state derives None
  PR-WH-S7   signature mismatch returns 401
  PR-WH-S8   req not in PENDING_USER_PR_REVIEW state is skipped
  PR-WH-S9   branch feat/REQ-xxx resolves req_id
  PR-VR-S10  pr_review fail invokes verifier with review body
  PR-WH-S10  apply_verify_pass routes pr_review → ARCHIVING + ARCHIVE_DONE
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from unittest.mock import AsyncMock

from orchestrator import webhook
from orchestrator.actions._verifier import _PASS_ROUTING
from orchestrator.state import Event, ReqState, decide


# ── _derive_event_from_review_state ───────────────────────────────────────────


@pytest.mark.parametrize(
    "review_state,body,expected",
    [
        ("approved", "", Event.GH_PR_REVIEW_APPROVED),
        ("changes_requested", "fix the bug", Event.GH_PR_REVIEW_CHANGES_REQUESTED),
        ("commented", "LGTM", Event.GH_PR_REVIEW_APPROVED),
        ("commented", "looks good to me", Event.GH_PR_REVIEW_APPROVED),
        ("commented", "fix: rename variable", Event.GH_PR_REVIEW_CHANGES_REQUESTED),
        ("commented", "fix variable name", Event.GH_PR_REVIEW_CHANGES_REQUESTED),
        ("commented", "nice work", Event.GH_PR_REVIEW_COMMENTED),
    ],
)
def test_PR_WH_S1_S5_event_derivation(review_state, body, expected):
    """PR-WH-S1..S5: review state + body content correctly maps to Event."""
    result = webhook._derive_event_from_review_state(review_state, body)
    assert result == expected


def test_PR_WH_S6_unknown_review_state():
    """PR-WH-S6: unknown review state returns None."""
    assert webhook._derive_event_from_review_state("dismissed", "") is None


# ── _verify_github_signature ─────────────────────────────────────────────────


def test_PR_WH_S7_valid_signature(monkeypatch):
    """PR-WH-S7: valid signature passes verification."""
    monkeypatch.setattr(webhook.settings, "github_webhook_secret", "secret123")
    payload = b'{"action":"submitted"}'
    sig = "sha256=" + hmac.new(b"secret123", payload, hashlib.sha256).hexdigest()
    assert webhook._verify_github_signature(payload, sig) is True


def test_PR_WH_S7_invalid_signature(monkeypatch):
    """PR-WH-S7: invalid signature fails verification."""
    monkeypatch.setattr(webhook.settings, "github_webhook_secret", "secret123")
    assert webhook._verify_github_signature(b"test", "sha256=bad") is False


def test_PR_WH_S7_missing_secret(monkeypatch):
    """PR-WH-S7: empty secret fails verification."""
    monkeypatch.setattr(webhook.settings, "github_webhook_secret", "")
    sig = "sha256=" + "a" * 64
    assert webhook._verify_github_signature(b"test", sig) is False


# ── _resolve_req_id_from_pr (async) ──────────────────────────────────────────


class _FakeRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.mark.asyncio
async def test_PR_WH_S9_resolve_from_branch_name(monkeypatch):
    """PR-WH-S9: branch feat/REQ-xxx resolves req_id directly."""
    fake_row = _FakeRow(
        req_id="REQ-test", project_id="p", state=ReqState.PENDING_USER_PR_REVIEW,
        history=[], context={"branch": "feat/REQ-test"},
        created_at=None, updated_at=None,
    )

    async def fake_get(_pool, req_id):
        return fake_row

    monkeypatch.setattr(webhook.req_state, "get", fake_get)

    req_id, row = await webhook._resolve_req_id_from_pr(None, "feat/REQ-test", "http://pr")
    assert req_id == "REQ-test"
    assert row is not None


@pytest.mark.asyncio
async def test_PR_WH_S9_resolve_fallback_by_branch(monkeypatch):
    """PR-WH-S9: fallback query by branch when direct extraction fails."""
    fake_rows = [
        {
            "req_id": "REQ-fallback",
            "project_id": "p",
            "state": "pending-user-pr-review",
            "history": "[]",
            "context": '{"branch": "feature/some-branch"}',
            "created_at": None,
            "updated_at": None,
        }
    ]

    class FakePool:
        async def fetch(self, *_a, **_kw):
            return fake_rows

    monkeypatch.setattr(webhook.req_state, "get", lambda _p, _r: None)

    req_id, row = await webhook._resolve_req_id_from_pr(FakePool(), "feature/some-branch", "http://pr")
    assert req_id == "REQ-fallback"
    assert row is not None
    assert row.state == ReqState.PENDING_USER_PR_REVIEW


# ── State machine: PENDING_USER_PR_REVIEW transitions ─────────────────────────


def test_pending_pr_review_approved():
    """approved → ARCHIVING + done_archive."""
    t = decide(ReqState.PENDING_USER_PR_REVIEW, Event.GH_PR_REVIEW_APPROVED)
    assert t.next_state == ReqState.ARCHIVING
    assert t.action == "done_archive"


def test_pending_pr_review_changes():
    """changes_requested → REVIEW_RUNNING + invoke_verifier_for_pr_review_fail."""
    t = decide(ReqState.PENDING_USER_PR_REVIEW, Event.GH_PR_REVIEW_CHANGES_REQUESTED)
    assert t.next_state == ReqState.REVIEW_RUNNING
    assert t.action == "invoke_verifier_for_pr_review_fail"


def test_pending_pr_review_comment():
    """commented → REVIEW_RUNNING + invoke_verifier_for_pr_review_comment."""
    t = decide(ReqState.PENDING_USER_PR_REVIEW, Event.GH_PR_REVIEW_COMMENTED)
    assert t.next_state == ReqState.REVIEW_RUNNING
    assert t.action == "invoke_verifier_for_pr_review_comment"


def test_pending_pr_review_pr_merged():
    """PR_MERGED from PENDING_USER_PR_REVIEW → ARCHIVING."""
    t = decide(ReqState.PENDING_USER_PR_REVIEW, Event.PR_MERGED)
    assert t.next_state == ReqState.ARCHIVING
    assert t.action == "done_archive"


def test_pending_pr_review_illegal_events():
    """Illegal events from PENDING_USER_PR_REVIEW return None."""
    for ev in (Event.USER_REVIEW_PASS, Event.USER_REVIEW_FIX, Event.SESSION_FAILED):
        assert decide(ReqState.PENDING_USER_PR_REVIEW, ev) is None


# ── apply_verify_pass routing for pr_review ───────────────────────────────────


def test_PR_WH_S10_pr_review_pass_routing():
    """PR-WH-S10: pr_review decision=pass routes to ARCHIVING + ARCHIVE_DONE."""
    route = _PASS_ROUTING.get("pr_review")
    assert route is not None
    assert route[0] == ReqState.ARCHIVING
    assert route[1] == Event.ARCHIVE_DONE


# ── PR-WH-S8: state mismatch skips ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_PR_WH_S8_state_mismatch_skips(monkeypatch):
    """PR-WH-S8: req not in PENDING_USER_PR_REVIEW state returns skip."""
    monkeypatch.setattr(webhook.settings, "github_webhook_secret", "secret123")

    payload = b'{"action":"submitted","review":{"state":"approved","id":123},"pull_request":{"head":{"ref":"feat/REQ-test"},"html_url":"http://pr"}}'
    sig = "sha256=" + hmac.new(b"secret123", payload, hashlib.sha256).hexdigest()

    fake_row = _FakeRow(
        req_id="REQ-test", project_id="p", state=ReqState.ARCHIVING,
        history=[], context={"branch": "feat/REQ-test"},
        created_at=None, updated_at=None,
    )

    async def fake_resolve(_pool, _branch, _pr_url):
        return "REQ-test", fake_row

    monkeypatch.setattr(webhook, "_resolve_req_id_from_pr", fake_resolve)
    monkeypatch.setattr(webhook, "_verify_github_signature", lambda _raw, _sig: True)

    request = AsyncMock()
    request.body = AsyncMock(return_value=payload)
    request.headers = {
        "x-hub-signature-256": sig,
        "x-github-event": "pull_request_review",
    }

    result = await webhook.github_webhook(request)
    assert result["action"] == "skip"
    assert "not pending-user-pr-review" in result["reason"]


# ── PR-VR-S10: pr_review fail invokes verifier with review body ───────────────


@pytest.mark.asyncio
async def test_PR_VR_S10_pr_review_fail_invokes_verifier_with_body(monkeypatch):
    """PR-VR-S10: invoke_verifier_for_pr_review_fail passes review body as stderr_tail."""
    calls = []

    async def fake_invoke_verifier(*, stage, trigger, req_id, project_id, stderr_tail, ctx, **kwargs):
        calls.append({"stage": stage, "trigger": trigger, "stderr_tail": stderr_tail})
        return {"verifier_issue_id": "v-1", "stage": stage, "trigger": trigger}

    monkeypatch.setattr("orchestrator.actions._verifier.invoke_verifier", fake_invoke_verifier)

    from orchestrator.actions._verifier import invoke_verifier_for_pr_review_fail

    result = await invoke_verifier_for_pr_review_fail(
        body=type("B", (), {"projectId": "p"})(),
        req_id="REQ-test",
        tags=["REQ-test"],
        ctx={"gh_pr_review_body": "fix the naming"},
    )

    assert len(calls) == 1
    assert calls[0]["stage"] == "pr_review"
    assert calls[0]["trigger"] == "fail"
    assert calls[0]["stderr_tail"] == "fix the naming"
    assert result["verifier_issue_id"] == "v-1"


# ── PR-WD-S11: watchdog ignores pending PR review ─────────────────────────────


def test_PR_WD_S11_pending_user_pr_review_in_no_watchdog_states():
    """PR-WD-S11: watchdog._NO_WATCHDOG_STATES must contain PENDING_USER_PR_REVIEW.

    Spec contract:
      PENDING_USER_PR_REVIEW is a human-in-loop state (waiting for GitHub PR review).
      The watchdog MUST NOT emit SESSION_FAILED or escalate REQs in this state.
      _NO_WATCHDOG_STATES is the canonical container for this exemption.
    """
    from orchestrator import watchdog
    assert ReqState.PENDING_USER_PR_REVIEW in watchdog._NO_WATCHDOG_STATES, (
        "PENDING_USER_PR_REVIEW must be in watchdog._NO_WATCHDOG_STATES "
        "(human-in-loop state waiting for GitHub PR review)"
    )
