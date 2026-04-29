"""Contract tests for REQ-user-feedback-loop-1777420881.

Scenarios:
  PR-WH-S1  approved review derives GH_PR_REVIEW_APPROVED
  PR-WH-S2  changes_requested review derives GH_PR_REVIEW_CHANGES_REQUESTED
  PR-WH-S3  commented review with "LGTM" derives GH_PR_REVIEW_APPROVED
  PR-WH-S4  commented review with "fix:" derives GH_PR_REVIEW_CHANGES_REQUESTED
  PR-WH-S5  commented review without keyword derives GH_PR_REVIEW_COMMENTED
  PR-WH-S6  unknown review state derives None
  PR-WH-S7  signature mismatch returns 401
  PR-WH-S8  req not in PENDING_USER_PR_REVIEW state is skipped
  PR-WH-S9  branch feat/REQ-xxx resolves req_id
  PR-WH-S10 apply_verify_pass routes pr_review → ARCHIVING + ARCHIVE_DONE
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

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
