"""Unit tests for orchestrator.gh_incident.open_incident + escalate integration.

Covers GHI-S1..GHI-S10 from openspec/changes/REQ-impl-gh-incident-open-1777173133.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _set_settings(monkeypatch, *, repo: str = "phona/sisyphus", token: str = "ghp_xxx",
                  labels=None):
    """Override config.settings for the duration of one test."""
    from orchestrator import gh_incident
    monkeypatch.setattr(gh_incident.settings, "gh_incident_repo", repo)
    monkeypatch.setattr(gh_incident.settings, "github_token", token)
    monkeypatch.setattr(
        gh_incident.settings, "gh_incident_labels",
        labels if labels is not None else ["sisyphus:incident"],
    )


def _patch_client(monkeypatch, *, status_code: int = 201,
                  json_body: dict | None = None,
                  raise_exc: Exception | None = None):
    """Replace httpx.AsyncClient with a stub whose .post returns / raises predictably.

    Captures the (url, headers, json) of the last call on the returned `recorder` dict.
    """
    from orchestrator import gh_incident

    recorder: dict = {}

    async def _post(self, url, headers=None, json=None):
        recorder["url"] = url
        recorder["headers"] = headers
        recorder["json"] = json
        if raise_exc:
            raise raise_exc
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.text = "" if json_body is None else str(json_body)
        resp.json = MagicMock(return_value=json_body or {})
        return resp

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return None
        post = _post

    monkeypatch.setattr(gh_incident.httpx, "AsyncClient", _FakeClient)
    return recorder


# ─── GHI-S1: disabled when gh_incident_repo empty ─────────────────────────
@pytest.mark.asyncio
async def test_open_incident_disabled_when_repo_empty(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, repo="", token="ghp_xxx")
    rec = _patch_client(monkeypatch)  # would record if called

    out = await gh_incident.open_incident(
        req_id="REQ-1", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None
    assert rec == {}, "no HTTP request should be made when disabled"


# ─── GHI-S2: disabled when github_token empty ────────────────────────────
@pytest.mark.asyncio
async def test_open_incident_disabled_when_token_empty(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, repo="phona/sisyphus", token="")
    rec = _patch_client(monkeypatch)

    out = await gh_incident.open_incident(
        req_id="REQ-1", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None
    assert rec == {}


# ─── GHI-S3: success returns html_url + uses correct URL/headers ─────────
@pytest.mark.asyncio
async def test_open_incident_success_returns_html_url(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, repo="phona/sisyphus", token="ghp_xxx")
    rec = _patch_client(
        monkeypatch, status_code=201,
        json_body={"html_url": "https://github.com/phona/sisyphus/issues/42"},
    )

    out = await gh_incident.open_incident(
        req_id="REQ-9", reason="verifier-decision-escalate", retry_count=0,
        intent_issue_id="intent-1", failed_issue_id="vfy-3", project_id="proj-A",
        state="executing",
    )
    assert out == "https://github.com/phona/sisyphus/issues/42"
    assert rec["url"] == "https://api.github.com/repos/phona/sisyphus/issues"
    assert rec["headers"]["Authorization"] == "Bearer ghp_xxx"
    assert rec["headers"]["Accept"] == "application/vnd.github+json"


# ─── GHI-S4: request body contains REQ id, reason, BKD cross-references ──
@pytest.mark.asyncio
async def test_open_incident_body_contains_context(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, repo="phona/sisyphus", token="ghp_xxx")
    rec = _patch_client(
        monkeypatch, status_code=201,
        json_body={"html_url": "https://github.com/phona/sisyphus/issues/42"},
    )

    await gh_incident.open_incident(
        req_id="REQ-9", reason="fixer-round-cap", retry_count=0,
        intent_issue_id="intent-1", failed_issue_id="vfy-3", project_id="proj-A",
        state="fixer-running",
    )
    payload = rec["json"]
    assert "REQ-9" in payload["title"]
    assert "fixer-round-cap" in payload["title"]
    body = payload["body"]
    for needle in ("REQ-9", "fixer-round-cap", "intent-1", "vfy-3", "proj-A", "fixer-running"):
        assert needle in body, f"body should contain {needle!r}"
    # labels: base + reason:*
    assert "sisyphus:incident" in payload["labels"]
    assert "reason:fixer-round-cap" in payload["labels"]


# ─── GHI-S5: HTTP failure returns None and does not raise ─────────────────
@pytest.mark.asyncio
async def test_open_incident_http_503_returns_none(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, repo="phona/sisyphus", token="ghp_xxx")
    _patch_client(monkeypatch, status_code=503,
                  json_body={"message": "service unavailable"})

    out = await gh_incident.open_incident(
        req_id="REQ-9", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None


@pytest.mark.asyncio
async def test_open_incident_network_error_returns_none(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, repo="phona/sisyphus", token="ghp_xxx")
    _patch_client(monkeypatch, raise_exc=httpx.ConnectError("DNS failure"))

    out = await gh_incident.open_incident(
        req_id="REQ-9", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None


@pytest.mark.asyncio
async def test_open_incident_2xx_without_html_url_returns_none(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, repo="phona/sisyphus", token="ghp_xxx")
    _patch_client(monkeypatch, status_code=200, json_body={"unexpected": "shape"})

    out = await gh_incident.open_incident(
        req_id="REQ-9", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None


# ─── escalate-integration tests (GHI-S6..GHI-S10) ─────────────────────────
# Sister tests to test_actions_smoke.py — kept here to keep gh-incident
# scenarios in one file. The fixtures mirror the smoke-test style.


@dataclass
class _FakeIssue:
    id: str
    project_id: str = "p"
    issue_number: int = 0
    title: str = ""
    status_id: str = "todo"
    tags: list | None = None
    session_status: str | None = None
    description: str | None = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


def _make_fake_bkd():
    bkd = AsyncMock()
    bkd.merge_tags_and_update = AsyncMock(return_value=_FakeIssue(id="x"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    return bkd


def _patch_bkd(monkeypatch, fake):
    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake
    monkeypatch.setattr("orchestrator.actions.escalate.BKDClient", _ctx)


def _patch_db(monkeypatch):
    """Stub db.get_pool + req_state.update_context (capture patches)."""
    patches: list[dict] = []

    class _Pool:
        async def execute(self, *a, **kw): return None
        async def fetchrow(self, *a, **kw): return None

    monkeypatch.setattr("orchestrator.actions.escalate.db.get_pool", lambda: _Pool())

    from orchestrator.store import req_state as rs

    async def _upd(_pool, _req_id, patch):
        patches.append(dict(patch))

    monkeypatch.setattr(rs, "update_context", _upd)
    return patches


def _make_body(*, issue_id="src-1", project_id="p", event="verify.escalate"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": event, "title": "", "tags": [], "issueNumber": None,
    })()


@pytest.mark.asyncio
async def test_escalate_real_path_opens_gh_incident(monkeypatch):
    """GHI-S6: real-escalate calls open_incident, stores URL, appends github-incident tag."""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)

    from orchestrator.store import req_state as rs

    class _Row:
        state = type("S", (), {"value": "executing"})()
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_Row()))

    monkeypatch.setattr(mod.settings, "gh_incident_repo", "phona/sisyphus")
    open_inc = AsyncMock(return_value="https://github.com/phona/sisyphus/issues/42")
    monkeypatch.setattr(mod.gh_incident, "open_incident", open_inc)

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
        },
    )
    assert out["escalated"] is True

    # open_incident called once with correct kwargs
    open_inc.assert_awaited_once()
    kw = open_inc.await_args.kwargs
    assert kw["req_id"] == "REQ-9"
    assert kw["reason"] == "verifier-decision-escalate"
    assert kw["intent_issue_id"] == "intent-1"
    assert kw["failed_issue_id"] == "vfy-3"
    assert kw["project_id"] == "p"

    # ctx patched with gh_incident_url + opened_at
    final = patches[-1]
    assert final["gh_incident_url"] == "https://github.com/phona/sisyphus/issues/42"
    assert "gh_incident_opened_at" in final
    assert final["escalated_reason"] == "verifier-decision-escalate"

    # BKD tag merge includes github-incident
    fake_bkd.merge_tags_and_update.assert_awaited_once()
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "escalated" in add
    assert "reason:verifier-decision-escalate" in add
    assert "github-incident" in add


@pytest.mark.asyncio
async def test_escalate_idempotent_when_ctx_has_url(monkeypatch):
    """GHI-S7: ctx.gh_incident_url already set → no second POST."""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)

    from orchestrator.store import req_state as rs

    class _Row:
        state = type("S", (), {"value": "executing"})()
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_Row()))

    open_inc = AsyncMock(return_value="https://example/should/not/be/called")
    monkeypatch.setattr(mod.gh_incident, "open_incident", open_inc)

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "gh_incident_url": "https://github.com/phona/sisyphus/issues/42",
        },
    )

    open_inc.assert_not_awaited()
    # ctx_patch must not RE-write gh_incident_url (idempotent: leave existing alone)
    final = patches[-1]
    assert "gh_incident_url" not in final
    # tag still includes github-incident (existing URL → still annotate BKD)
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "github-incident" in add


@pytest.mark.asyncio
async def test_escalate_auto_resume_does_not_open_incident(monkeypatch):
    """GHI-S8: transient + budget remaining → auto-resume; no GH POST."""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch)

    open_inc = AsyncMock(return_value="https://example/should/not/be/called")
    monkeypatch.setattr(mod.gh_incident, "open_incident", open_inc)

    body = _make_body(issue_id="src-1", event="session.failed")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["intent:analyze"],
        ctx={"intent_issue_id": "intent-1", "auto_retry_count": 0},
    )
    assert out["auto_resumed"] is True
    open_inc.assert_not_awaited()
    fake_bkd.follow_up_issue.assert_awaited_once()
    fake_bkd.merge_tags_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_escalate_gh_failure_does_not_abort(monkeypatch):
    """GHI-S9: open_incident returns None → escalate still completes; no gh_incident_url written."""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)

    from orchestrator.store import req_state as rs

    class _Row:
        state = type("S", (), {"value": "executing"})()
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_Row()))

    open_inc = AsyncMock(return_value=None)  # GH outage / disabled
    monkeypatch.setattr(mod.gh_incident, "open_incident", open_inc)

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
        },
    )
    assert out["escalated"] is True
    fake_bkd.merge_tags_and_update.assert_awaited_once()
    final = patches[-1]
    assert "gh_incident_url" not in final
    # github-incident tag NOT appended when no URL was minted
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "github-incident" not in add


@pytest.mark.asyncio
async def test_escalate_disabled_default_keeps_old_behavior(monkeypatch):
    """GHI-S10: settings.gh_incident_repo='' (default) → behavior unchanged."""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)

    from orchestrator.store import req_state as rs

    class _Row:
        state = type("S", (), {"value": "executing"})()
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_Row()))

    # Don't mock open_incident — let it run with real (empty) settings
    monkeypatch.setattr(mod.gh_incident.settings, "gh_incident_repo", "")
    monkeypatch.setattr(mod.gh_incident.settings, "github_token", "")

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
        },
    )
    assert out["escalated"] is True
    final = patches[-1]
    assert "gh_incident_url" not in final
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "github-incident" not in add
    assert "escalated" in add
    assert "reason:verifier-decision-escalate" in add
