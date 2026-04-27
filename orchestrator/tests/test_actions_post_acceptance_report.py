"""Tests for post_acceptance_report action.

REQ-bkd-acceptance-feedback-loop-1777278984 — covers spec scenarios USER-S9..S11
in openspec/changes/REQ-bkd-acceptance-feedback-loop-1777278984/specs/user-acceptance-gate/spec.md
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest


@dataclass
class FakeIssue:
    id: str
    project_id: str = "p"
    issue_number: int = 0
    title: str = ""
    status_id: str = "working"
    tags: list = field(default_factory=list)
    session_status: str | None = None
    description: str | None = None


def _make_fake_bkd(*, current_tags: list[str] | None = None) -> AsyncMock:
    issue = FakeIssue(id="intent-7", tags=list(current_tags or []))
    bkd = AsyncMock()
    bkd.get_issue = AsyncMock(return_value=issue)
    bkd.update_issue = AsyncMock(return_value=issue)
    bkd.follow_up_issue = AsyncMock(return_value={})
    bkd.merge_tags_and_update = AsyncMock(return_value=issue)
    return bkd


def _patch_bkd(monkeypatch, fake):
    @asynccontextmanager
    async def _ctx(*_a, **_kw):
        yield fake

    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.BKDClient", _ctx,
    )


def _patch_db(monkeypatch):
    captured: list = []

    class P:
        async def execute(self, sql, *args):
            captured.append((sql.strip()[:60], args))

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(
        "orchestrator.actions.post_acceptance_report.db.get_pool",
        lambda: P(),
    )
    return captured


def _make_body(issue_id="src-x", project_id="p"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id, "event": "issue.updated",
        "title": "", "tags": [], "issueNumber": None,
    })()


# ── USER-S9: first invocation tags + follow_up; never touches statusId/title ──


@pytest.mark.asyncio
async def test_USER_S9_tag_and_follow_up_no_status_or_title_change(monkeypatch):
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_fake_bkd(current_tags=["analyze", "REQ-x", "repo:phona/sisyphus"])
    _patch_bkd(monkeypatch, bkd)
    _patch_db(monkeypatch)

    body = _make_body()
    ctx = {
        "intent_issue_id": "intent-7",
        "pr_urls": {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/200"},
    }
    out = await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"], ctx=ctx,
    )

    assert out["acceptance_reported"] is True
    assert out["bkd_ok"] is True

    # tag added (idempotent merge), no statusId / title PATCH
    assert bkd.merge_tags_and_update.await_count == 1
    _, kwargs = bkd.merge_tags_and_update.call_args
    assert kwargs.get("add") == [mod.ACCEPTANCE_PENDING_TAG]
    # merge_tags_and_update is called on the intent issue, not body.issueId
    args, _kw = bkd.merge_tags_and_update.call_args
    assert args[0] == "p"
    assert args[1] == "intent-7"
    # status_id should NOT be passed (we leave statusId for the user)
    assert kwargs.get("status_id") is None

    # follow_up_issue posted message containing PR URL + statusId 操作说明
    bkd.follow_up_issue.assert_awaited_once()
    _, fkw = bkd.follow_up_issue.call_args
    assert fkw["project_id"] == "p"
    assert fkw["issue_id"] == "intent-7"
    msg = fkw["prompt"]
    assert "https://github.com/phona/sisyphus/pull/200" in msg
    assert "statusId" in msg
    assert "done" in msg
    assert "review" in msg
    assert "blocked" in msg

    # update_issue (full PATCH) MUST NOT be called directly with title/statusId
    bkd.update_issue.assert_not_called()


# ── USER-S10: rerun is safe (merge_tags_and_update is idempotent for same tag) ──


@pytest.mark.asyncio
async def test_USER_S10_rerun_idempotent_tag(monkeypatch):
    """Tag already present → merge_tags_and_update is a no-op for that tag.

    The follow_up message would be re-posted; this is acceptable per the
    action's docstring (state machine guards re-entry at the CAS layer).
    """
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_fake_bkd(
        current_tags=["analyze", "REQ-x", mod.ACCEPTANCE_PENDING_TAG],
    )
    _patch_bkd(monkeypatch, bkd)
    _patch_db(monkeypatch)

    body = _make_body()
    ctx = {"intent_issue_id": "intent-7", "pr_urls": {}}
    out = await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"], ctx=ctx,
    )

    # 2nd invocation: still attempts merge_tags_and_update; the bkd helper
    # internally de-dupes the tag so the resulting tags list is unchanged.
    assert out["acceptance_reported"] is True
    assert bkd.merge_tags_and_update.await_count == 1


# ── USER-S11: missing intent_issue_id → noop, no BKD calls ─────────────────


@pytest.mark.asyncio
async def test_USER_S11_missing_intent_issue_id_no_bkd_calls(monkeypatch):
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, bkd)
    _patch_db(monkeypatch)

    body = _make_body()
    out = await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"], ctx={},
    )

    assert out["acceptance_reported"] is False
    assert "no intent_issue_id" in out["reason"]
    bkd.merge_tags_and_update.assert_not_called()
    bkd.follow_up_issue.assert_not_called()
    bkd.update_issue.assert_not_called()


# ── BKD failure does not propagate; ctx still updated, action returns ok ──


@pytest.mark.asyncio
async def test_post_acceptance_report_bkd_failure_logged_not_raised(monkeypatch):
    from orchestrator.actions import post_acceptance_report as mod

    bkd = _make_fake_bkd()
    bkd.merge_tags_and_update.side_effect = RuntimeError("BKD 503")
    _patch_bkd(monkeypatch, bkd)
    _patch_db(monkeypatch)

    body = _make_body()
    out = await mod.post_acceptance_report(
        body=body, req_id="REQ-x", tags=["REQ-x"],
        ctx={"intent_issue_id": "intent-7", "pr_urls": {}},
    )

    # action returns success-ish dict (doesn't raise); bkd_ok=False signals failure
    assert out["acceptance_reported"] is True
    assert out["bkd_ok"] is False
    bkd.follow_up_issue.assert_not_called()  # short-circuited on tag failure
