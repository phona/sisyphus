"""Unit tests for orchestrator.gh_incident + escalate integration.

Coverage:
- GHI-S1..GHI-S5    open_incident unit tests (legacy issue-creation path) —
                    behavior preserved by the comment-on-pr refactor.
- COP-S1..COP-S7    comment_on_pr unit tests
                    (REQ-one-pr-per-req-1777218057).
- FPR-S1..FPR-S4    find_pr_for_branch unit tests
                    (REQ-one-pr-per-req-1777218057).
- GHI-S6..GHI-S15   escalate integration: comment-first happy paths,
                    idempotency, multi-repo, partial failure, layered
                    fallback resolver.
- ICP-S1..ICP-S3    escalate falls back to open_incident when no PR exists
                    (REQ-one-pr-per-req-1777218057).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _set_settings(monkeypatch, *, token: str = "ghp_xxx", labels=None,
                  gh_incident_repo: str = "",
                  default_involved_repos=None):
    """Override config.settings on the gh_incident module."""
    from orchestrator import gh_incident
    monkeypatch.setattr(gh_incident.settings, "github_token", token)
    monkeypatch.setattr(gh_incident.settings, "gh_incident_repo", gh_incident_repo)
    monkeypatch.setattr(
        gh_incident.settings, "gh_incident_labels",
        labels if labels is not None else ["sisyphus:incident"],
    )
    monkeypatch.setattr(
        gh_incident.settings, "default_involved_repos",
        default_involved_repos if default_involved_repos is not None else [],
    )


def _patch_client(monkeypatch, *,
                  post_status: int = 201,
                  post_json: dict | None = None,
                  post_raise: Exception | None = None,
                  get_status: int = 200,
                  get_json: list | dict | None = None,
                  get_raise: Exception | None = None):
    """Replace httpx.AsyncClient with a stub supporting GET (find_pr) + POST.

    Captures the (url, headers, params/json) of the last call on the returned
    `recorder` dict under keys `last_post` / `last_get`.
    """
    from orchestrator import gh_incident

    recorder: dict = {}

    async def _post(self, url, headers=None, json=None):
        recorder["last_post"] = {"url": url, "headers": headers, "json": json}
        if post_raise:
            raise post_raise
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = post_status
        resp.text = "" if post_json is None else str(post_json)
        resp.json = MagicMock(return_value=post_json or {})
        return resp

    async def _get(self, url, headers=None, params=None):
        recorder["last_get"] = {"url": url, "headers": headers, "params": params}
        if get_raise:
            raise get_raise
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = get_status
        resp.text = "" if get_json is None else str(get_json)
        resp.json = MagicMock(return_value=get_json if get_json is not None else [])
        return resp

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return None
        post = _post
        get = _get

    monkeypatch.setattr(gh_incident.httpx, "AsyncClient", _FakeClient)
    return recorder


# ─── GHI-S1..GHI-S5: open_incident unit tests (legacy path, unchanged) ─────


@pytest.mark.asyncio
async def test_open_incident_disabled_when_repo_empty(monkeypatch):
    """GHI-S1"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    rec = _patch_client(monkeypatch)

    out = await gh_incident.open_incident(
        repo="", req_id="REQ-1", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None
    assert rec == {}, "no HTTP request should be made when repo is empty"


@pytest.mark.asyncio
async def test_open_incident_disabled_when_token_empty(monkeypatch):
    """GHI-S2"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="")
    rec = _patch_client(monkeypatch)

    out = await gh_incident.open_incident(
        repo="phona/sisyphus", req_id="REQ-1", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None
    assert rec == {}


@pytest.mark.asyncio
async def test_open_incident_success_returns_html_url(monkeypatch):
    """GHI-S3"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    rec = _patch_client(
        monkeypatch, post_status=201,
        post_json={"html_url": "https://github.com/phona/sisyphus/issues/42"},
    )

    out = await gh_incident.open_incident(
        repo="phona/sisyphus",
        req_id="REQ-9", reason="verifier-decision-escalate", retry_count=0,
        intent_issue_id="intent-1", failed_issue_id="vfy-3", project_id="proj-A",
        state="executing",
    )
    assert out == "https://github.com/phona/sisyphus/issues/42"
    last = rec["last_post"]
    assert last["url"] == "https://api.github.com/repos/phona/sisyphus/issues"
    assert last["headers"]["Authorization"] == "Bearer ghp_xxx"
    assert last["headers"]["Accept"] == "application/vnd.github+json"


@pytest.mark.asyncio
async def test_open_incident_body_contains_context(monkeypatch):
    """GHI-S4"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    rec = _patch_client(
        monkeypatch, post_status=201,
        post_json={"html_url": "https://github.com/phona/sisyphus/issues/42"},
    )

    await gh_incident.open_incident(
        repo="phona/sisyphus",
        req_id="REQ-9", reason="fixer-round-cap", retry_count=0,
        intent_issue_id="intent-1", failed_issue_id="vfy-3", project_id="proj-A",
        state="fixer-running",
    )
    payload = rec["last_post"]["json"]
    assert "REQ-9" in payload["title"]
    assert "fixer-round-cap" in payload["title"]
    body = payload["body"]
    for needle in ("REQ-9", "fixer-round-cap", "intent-1", "vfy-3", "proj-A", "fixer-running"):
        assert needle in body, f"body should contain {needle!r}"
    assert "sisyphus:incident" in payload["labels"]
    assert "reason:fixer-round-cap" in payload["labels"]


@pytest.mark.asyncio
async def test_open_incident_http_503_returns_none(monkeypatch):
    """GHI-S5 — HTTP failure"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    _patch_client(monkeypatch, post_status=503,
                  post_json={"message": "service unavailable"})

    out = await gh_incident.open_incident(
        repo="phona/sisyphus",
        req_id="REQ-9", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None


@pytest.mark.asyncio
async def test_open_incident_network_error_returns_none(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    _patch_client(monkeypatch, post_raise=httpx.ConnectError("DNS failure"))

    out = await gh_incident.open_incident(
        repo="phona/sisyphus",
        req_id="REQ-9", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None


@pytest.mark.asyncio
async def test_open_incident_2xx_without_html_url_returns_none(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    _patch_client(monkeypatch, post_status=200, post_json={"unexpected": "shape"})

    out = await gh_incident.open_incident(
        repo="phona/sisyphus",
        req_id="REQ-9", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None


# ─── COP-S1..COP-S7: comment_on_pr unit tests ─────────────────────────────


@pytest.mark.asyncio
async def test_comment_on_pr_disabled_when_repo_empty(monkeypatch):
    """COP-S1"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    rec = _patch_client(monkeypatch)

    out = await gh_incident.comment_on_pr(
        repo="", pr_number=42, req_id="REQ-1", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None
    assert rec == {}


@pytest.mark.asyncio
async def test_comment_on_pr_disabled_when_token_empty(monkeypatch):
    """COP-S2"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="")
    rec = _patch_client(monkeypatch)

    out = await gh_incident.comment_on_pr(
        repo="phona/sisyphus", pr_number=42, req_id="REQ-1", reason="x",
        retry_count=0, intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None
    assert rec == {}


@pytest.mark.asyncio
async def test_comment_on_pr_disabled_when_pr_number_zero(monkeypatch):
    """COP-S3"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    rec = _patch_client(monkeypatch)

    out = await gh_incident.comment_on_pr(
        repo="phona/sisyphus", pr_number=0, req_id="REQ-1", reason="x",
        retry_count=0, intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None
    assert rec == {}


@pytest.mark.asyncio
async def test_comment_on_pr_success_returns_html_url(monkeypatch):
    """COP-S4"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    rec = _patch_client(
        monkeypatch, post_status=201,
        post_json={"html_url": "https://github.com/phona/sisyphus/pull/42#issuecomment-99"},
    )

    out = await gh_incident.comment_on_pr(
        repo="phona/sisyphus", pr_number=42,
        req_id="REQ-9", reason="verifier-decision-escalate", retry_count=0,
        intent_issue_id="intent-1", failed_issue_id="vfy-3", project_id="proj-A",
        state="executing",
    )
    assert out == "https://github.com/phona/sisyphus/pull/42#issuecomment-99"
    last = rec["last_post"]
    assert last["url"] == "https://api.github.com/repos/phona/sisyphus/issues/42/comments"
    assert last["headers"]["Authorization"] == "Bearer ghp_xxx"
    assert last["headers"]["Accept"] == "application/vnd.github+json"


@pytest.mark.asyncio
async def test_comment_on_pr_body_contains_context(monkeypatch):
    """COP-S5"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    rec = _patch_client(
        monkeypatch, post_status=201,
        post_json={"html_url": "https://github.com/phona/sisyphus/pull/42#issuecomment-99"},
    )

    await gh_incident.comment_on_pr(
        repo="phona/sisyphus", pr_number=42,
        req_id="REQ-9", reason="fixer-round-cap", retry_count=1,
        intent_issue_id="intent-1", failed_issue_id="vfy-3", project_id="proj-A",
        state="fixer-running",
    )
    payload = rec["last_post"]["json"]
    body = payload["body"]
    for needle in ("REQ-9", "fixer-round-cap", "intent-1", "vfy-3", "proj-A", "fixer-running"):
        assert needle in body, f"body should contain {needle!r}"


@pytest.mark.asyncio
async def test_comment_on_pr_http_503_returns_none(monkeypatch):
    """COP-S6"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    _patch_client(monkeypatch, post_status=503, post_json={"message": "boom"})

    out = await gh_incident.comment_on_pr(
        repo="phona/sisyphus", pr_number=42,
        req_id="REQ-9", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None


@pytest.mark.asyncio
async def test_comment_on_pr_network_error_returns_none(monkeypatch):
    """COP-S7"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    _patch_client(monkeypatch, post_raise=httpx.ConnectError("DNS failure"))

    out = await gh_incident.comment_on_pr(
        repo="phona/sisyphus", pr_number=42,
        req_id="REQ-9", reason="x", retry_count=0,
        intent_issue_id="i", failed_issue_id="i", project_id="p",
    )
    assert out is None


# ─── FPR-S1..FPR-S4: find_pr_for_branch unit tests ────────────────────────


@pytest.mark.asyncio
async def test_find_pr_disabled_when_repo_or_token_empty(monkeypatch):
    """FPR-S1"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="")
    rec = _patch_client(monkeypatch)

    out = await gh_incident.find_pr_for_branch(repo="phona/sisyphus", branch="feat/REQ-x")
    assert out is None
    assert rec == {}

    _set_settings(monkeypatch, token="ghp_xxx")
    rec = _patch_client(monkeypatch)
    out = await gh_incident.find_pr_for_branch(repo="", branch="feat/REQ-x")
    assert out is None
    assert rec == {}


@pytest.mark.asyncio
async def test_find_pr_returns_first_pr_number(monkeypatch):
    """FPR-S2"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    rec = _patch_client(
        monkeypatch, get_status=200,
        get_json=[{"number": 42, "html_url": "https://x"}, {"number": 39}],
    )

    out = await gh_incident.find_pr_for_branch(
        repo="phona/sisyphus", branch="feat/REQ-x",
    )
    assert out == 42
    last = rec["last_get"]
    assert last["url"] == "https://api.github.com/repos/phona/sisyphus/pulls"
    assert last["params"] == {"head": "phona:feat/REQ-x", "state": "all", "per_page": 5}
    assert last["headers"]["Authorization"] == "Bearer ghp_xxx"


@pytest.mark.asyncio
async def test_find_pr_returns_none_on_empty_list(monkeypatch):
    """FPR-S3"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    _patch_client(monkeypatch, get_status=200, get_json=[])

    out = await gh_incident.find_pr_for_branch(
        repo="phona/sisyphus", branch="feat/REQ-x",
    )
    assert out is None


@pytest.mark.asyncio
async def test_find_pr_returns_none_on_http_error(monkeypatch):
    """FPR-S4"""
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    _patch_client(monkeypatch, get_status=503, get_json={"message": "boom"})

    out = await gh_incident.find_pr_for_branch(
        repo="phona/sisyphus", branch="feat/REQ-x",
    )
    assert out is None


@pytest.mark.asyncio
async def test_find_pr_returns_none_on_network_error(monkeypatch):
    from orchestrator import gh_incident
    _set_settings(monkeypatch, token="ghp_xxx")
    _patch_client(monkeypatch, get_raise=httpx.ConnectError("DNS failure"))

    out = await gh_incident.find_pr_for_branch(
        repo="phona/sisyphus", branch="feat/REQ-x",
    )
    assert out is None


# ─── escalate-integration tests (GHI-S6..S15 + ICP-S1..S3) ────────────────


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


def _patch_settings(monkeypatch, *, gh_incident_repo: str = "",
                    default_involved_repos=None):
    """Stub settings on actions.escalate (NOT gh_incident — that's mocked separately)."""
    from orchestrator.actions import escalate as mod
    monkeypatch.setattr(mod.settings, "gh_incident_repo", gh_incident_repo)
    monkeypatch.setattr(
        mod.settings, "default_involved_repos",
        default_involved_repos if default_involved_repos is not None else [],
    )


def _make_body(*, issue_id="src-1", project_id="p", event="verify.escalate"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": event, "title": "", "tags": [], "issueNumber": None,
    })()


def _stub_req_state_get(monkeypatch):
    from orchestrator.store import req_state as rs

    class _Row:
        state = type("S", (), {"value": "executing"})()
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_Row()))


def _stub_pr_merge_probe_disabled(monkeypatch):
    """Stub _all_prs_merged_for_req → False so PR-merged shortcut never fires."""
    from orchestrator.actions import escalate as mod
    monkeypatch.setattr(mod, "_all_prs_merged_for_req", AsyncMock(return_value=False))


def _patch_gh(monkeypatch, *, find_pr=None, comment=None, open_inc=None):
    """Mock the three gh_incident helpers escalate now uses.

    `find_pr`: return value or callable(repo, branch) for per-call value.
    `comment`: return value or callable(**kwargs) for per-call value.
    `open_inc`: return value or callable(**kwargs) for per-call value.
    """
    from orchestrator.actions import escalate as mod

    def _wrap(spec, default):
        if spec is None:
            return AsyncMock(return_value=default)
        if callable(spec):
            return AsyncMock(side_effect=spec)
        return AsyncMock(return_value=spec)

    fp = _wrap(find_pr, None)
    co = _wrap(comment, None)
    oi = _wrap(open_inc, None)
    monkeypatch.setattr(mod.gh_incident, "find_pr_for_branch", fp)
    monkeypatch.setattr(mod.gh_incident, "comment_on_pr", co)
    monkeypatch.setattr(mod.gh_incident, "open_incident", oi)
    return fp, co, oi


# ─── GHI-S6: real-escalate single involved repo posts a PR comment ────────
@pytest.mark.asyncio
async def test_escalate_real_path_comments_on_pr(monkeypatch):
    """GHI-S6"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    fp, co, oi = _patch_gh(
        monkeypatch,
        find_pr=42,
        comment="https://github.com/phona/sisyphus/pull/42#issuecomment-99",
    )

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/sisyphus"],
        },
    )
    assert out["escalated"] is True

    fp.assert_awaited_once()
    fp_kw = fp.await_args.kwargs
    assert fp_kw["repo"] == "phona/sisyphus"
    assert fp_kw["branch"] == "feat/REQ-9"

    co.assert_awaited_once()
    co_kw = co.await_args.kwargs
    assert co_kw["repo"] == "phona/sisyphus"
    assert co_kw["pr_number"] == 42
    assert co_kw["req_id"] == "REQ-9"
    assert co_kw["reason"] == "verifier-decision-escalate"
    assert co_kw["intent_issue_id"] == "intent-1"
    assert co_kw["failed_issue_id"] == "vfy-3"
    assert co_kw["project_id"] == "p"

    oi.assert_not_awaited()

    final = patches[-1]
    assert final["gh_incident_urls"] == {
        "phona/sisyphus": "https://github.com/phona/sisyphus/pull/42#issuecomment-99",
    }
    assert final["gh_incident_kinds"] == {"phona/sisyphus": "comment"}
    assert final["gh_incident_url"] == "https://github.com/phona/sisyphus/pull/42#issuecomment-99"
    assert "gh_incident_opened_at" in final
    assert final["escalated_reason"] == "verifier-decision-escalate"

    fake_bkd.merge_tags_and_update.assert_awaited_once()
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "escalated" in add
    assert "reason:verifier-decision-escalate" in add
    assert "github-incident" in add


# ─── GHI-S7: idempotent: pre-existing url skips lookup + comment ──────────
@pytest.mark.asyncio
async def test_escalate_idempotent_when_ctx_has_url(monkeypatch):
    """GHI-S7"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    fp, co, oi = _patch_gh(monkeypatch, find_pr=42,
                            comment="https://example/should/not/be/called",
                            open_inc="https://example/should/not/be/called")

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/sisyphus"],
            "gh_incident_urls": {
                "phona/sisyphus": "https://github.com/phona/sisyphus/pull/42#issuecomment-99",
            },
        },
    )

    fp.assert_not_awaited()
    co.assert_not_awaited()
    oi.assert_not_awaited()

    final = patches[-1]
    # No new URL → ctx_patch must not REWRITE gh_incident_urls / gh_incident_url
    assert "gh_incident_urls" not in final
    assert "gh_incident_url" not in final
    assert "gh_incident_kinds" not in final
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "github-incident" in add


# ─── GHI-S8: auto-resume branch does not interact with GitHub ─────────────
@pytest.mark.asyncio
async def test_escalate_auto_resume_does_not_open_incident(monkeypatch):
    """GHI-S8"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    fp, co, oi = _patch_gh(monkeypatch, find_pr=42, comment="x", open_inc="x")

    body = _make_body(issue_id="src-1", event="session.failed")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["intent:analyze"],
        ctx={"intent_issue_id": "intent-1", "auto_retry_count": 0},
    )
    assert out["auto_resumed"] is True
    fp.assert_not_awaited()
    co.assert_not_awaited()
    oi.assert_not_awaited()
    fake_bkd.follow_up_issue.assert_awaited_once()
    fake_bkd.merge_tags_and_update.assert_not_awaited()


# ─── GHI-S9: GH comment failure does not abort escalate ───────────────────
@pytest.mark.asyncio
async def test_escalate_gh_comment_failure_does_not_abort(monkeypatch):
    """GHI-S9"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    _fp, co, oi = _patch_gh(monkeypatch, find_pr=42, comment=None, open_inc=None)

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/sisyphus"],
        },
    )
    assert out["escalated"] is True
    co.assert_awaited_once()
    oi.assert_not_awaited()  # PR was found → no fallback to issue
    fake_bkd.merge_tags_and_update.assert_awaited_once()
    final = patches[-1]
    assert "gh_incident_url" not in final
    assert "gh_incident_urls" not in final
    assert "gh_incident_kinds" not in final
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "github-incident" not in add


# ─── GHI-S10: disabled (no involved repos and no fallback) ────────────────
@pytest.mark.asyncio
async def test_escalate_disabled_default_keeps_old_behavior(monkeypatch):
    """GHI-S10"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    fp, co, oi = _patch_gh(monkeypatch)
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
    fp.assert_not_awaited()
    co.assert_not_awaited()
    oi.assert_not_awaited()
    final = patches[-1]
    assert "gh_incident_url" not in final
    assert "gh_incident_urls" not in final
    assert "gh_incident_kinds" not in final
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "github-incident" not in add
    assert "escalated" in add
    assert "reason:verifier-decision-escalate" in add


# ─── GHI-S11: multi-repo REQ posts one comment per involved repo ──────────
@pytest.mark.asyncio
async def test_escalate_multi_repo_comments_per_repo(monkeypatch):
    """GHI-S11"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    pr_lookup = {"phona/repo-a": 7, "phona/repo-b": 3}
    comments = {
        ("phona/repo-a", 7): "https://github.com/phona/repo-a/pull/7#issuecomment-1",
        ("phona/repo-b", 3): "https://github.com/phona/repo-b/pull/3#issuecomment-2",
    }

    async def _find(*, repo, branch):
        return pr_lookup.get(repo)

    async def _comment(*, repo, pr_number, **_):
        return comments.get((repo, pr_number))

    _fp, co, oi = _patch_gh(monkeypatch, find_pr=_find, comment=_comment)

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
        },
    )

    assert co.await_count == 2
    repos_called = sorted(c.kwargs["repo"] for c in co.await_args_list)
    assert repos_called == ["phona/repo-a", "phona/repo-b"]
    oi.assert_not_awaited()

    final = patches[-1]
    assert final["gh_incident_urls"] == {
        "phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1",
        "phona/repo-b": "https://github.com/phona/repo-b/pull/3#issuecomment-2",
    }
    assert final["gh_incident_kinds"] == {
        "phona/repo-a": "comment",
        "phona/repo-b": "comment",
    }
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert add.count("github-incident") == 1


# ─── GHI-S12: partial failure isolated ────────────────────────────────────
@pytest.mark.asyncio
async def test_escalate_partial_failure_isolated(monkeypatch):
    """GHI-S12"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    pr_lookup = {"phona/repo-a": 7, "phona/repo-b": 3}

    async def _find(*, repo, branch):
        return pr_lookup.get(repo)

    async def _comment(*, repo, pr_number, **_):
        return None if repo == "phona/repo-a" else "https://github.com/phona/repo-b/pull/3#issuecomment-2"

    _fp, _co, _oi = _patch_gh(monkeypatch, find_pr=_find, comment=_comment)

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
        },
    )
    assert out["escalated"] is True

    final = patches[-1]
    assert final["gh_incident_urls"] == {
        "phona/repo-b": "https://github.com/phona/repo-b/pull/3#issuecomment-2",
    }
    assert final["gh_incident_kinds"] == {"phona/repo-b": "comment"}
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "github-incident" in add


# ─── GHI-S13: idempotent across multi-repo on re-entry ────────────────────
@pytest.mark.asyncio
async def test_escalate_idempotent_per_repo_only_missing_posted(monkeypatch):
    """GHI-S13"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    _fp, co, oi = _patch_gh(
        monkeypatch, find_pr=3,
        comment="https://github.com/phona/repo-b/pull/3#issuecomment-2",
    )

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
            "gh_incident_urls": {
                "phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1",
            },
        },
    )

    co.assert_awaited_once()
    assert co.await_args.kwargs["repo"] == "phona/repo-b"
    assert co.await_args.kwargs["pr_number"] == 3
    oi.assert_not_awaited()

    final = patches[-1]
    assert final["gh_incident_urls"] == {
        "phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1",
        "phona/repo-b": "https://github.com/phona/repo-b/pull/3#issuecomment-2",
    }


# ─── GHI-S14: falls back to settings.gh_incident_repo (legacy single inbox) ─
@pytest.mark.asyncio
async def test_escalate_falls_back_to_settings_gh_incident_repo(monkeypatch):
    """GHI-S14"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch, gh_incident_repo="phona/sisyphus")

    # gh_incident_repo is a triage-inbox repo with no per-REQ PR — find_pr returns None,
    # falls through to open_incident.
    fp, co, oi = _patch_gh(
        monkeypatch, find_pr=None, comment=None,
        open_inc="https://github.com/phona/sisyphus/issues/99",
    )

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
        },
    )

    fp.assert_awaited_once()
    assert fp.await_args.kwargs["repo"] == "phona/sisyphus"
    co.assert_not_awaited()
    oi.assert_awaited_once()
    assert oi.await_args.kwargs["repo"] == "phona/sisyphus"

    final = patches[-1]
    assert final["gh_incident_urls"] == {
        "phona/sisyphus": "https://github.com/phona/sisyphus/issues/99",
    }
    assert final["gh_incident_kinds"] == {"phona/sisyphus": "issue"}
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "github-incident" in add


# ─── GHI-S15: layers 1-4 take precedence over settings.gh_incident_repo ───
@pytest.mark.asyncio
async def test_escalate_involved_repos_take_precedence_over_fallback(monkeypatch):
    """GHI-S15"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch, gh_incident_repo="phona/sisyphus")

    _fp, co, oi = _patch_gh(
        monkeypatch, find_pr=1,
        comment="https://github.com/phona/repo-a/pull/1#issuecomment-1",
    )

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/repo-a"],
        },
    )

    co.assert_awaited_once()
    assert co.await_args.kwargs["repo"] == "phona/repo-a"
    oi.assert_not_awaited()

    final = patches[-1]
    assert set(final["gh_incident_urls"].keys()) == {"phona/repo-a"}


# ─── ICP-S1: falls back to issue when no PR exists for feat/{REQ} ─────────
@pytest.mark.asyncio
async def test_escalate_falls_back_to_issue_when_no_pr(monkeypatch):
    """ICP-S1"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    fp, co, oi = _patch_gh(
        monkeypatch, find_pr=None, comment=None,
        open_inc="https://github.com/phona/sisyphus/issues/42",
    )

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/sisyphus"],
        },
    )

    fp.assert_awaited_once()
    assert fp.await_args.kwargs["repo"] == "phona/sisyphus"
    assert fp.await_args.kwargs["branch"] == "feat/REQ-9"
    co.assert_not_awaited()
    oi.assert_awaited_once()
    oi_kw = oi.await_args.kwargs
    assert oi_kw["repo"] == "phona/sisyphus"
    assert oi_kw["req_id"] == "REQ-9"
    assert oi_kw["reason"] == "verifier-decision-escalate"

    final = patches[-1]
    assert final["gh_incident_urls"] == {
        "phona/sisyphus": "https://github.com/phona/sisyphus/issues/42",
    }
    assert final["gh_incident_kinds"] == {"phona/sisyphus": "issue"}
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert "github-incident" in add


# ─── ICP-S2: mixed multi-repo: comment for one, issue fallback for other ──
@pytest.mark.asyncio
async def test_escalate_mixed_multi_repo_comment_and_issue(monkeypatch):
    """ICP-S2"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    pr_lookup = {"phona/repo-a": 7}  # repo-b absent → no PR

    async def _find(*, repo, branch):
        return pr_lookup.get(repo)

    async def _comment(*, repo, pr_number, **_):
        if repo == "phona/repo-a" and pr_number == 7:
            return "https://github.com/phona/repo-a/pull/7#issuecomment-1"
        return None

    async def _open(*, repo, **_):
        if repo == "phona/repo-b":
            return "https://github.com/phona/repo-b/issues/42"
        return None

    _fp, co, oi = _patch_gh(monkeypatch, find_pr=_find, comment=_comment, open_inc=_open)

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
        },
    )

    co.assert_awaited_once()
    assert co.await_args.kwargs["repo"] == "phona/repo-a"
    oi.assert_awaited_once()
    assert oi.await_args.kwargs["repo"] == "phona/repo-b"

    final = patches[-1]
    assert final["gh_incident_urls"] == {
        "phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1",
        "phona/repo-b": "https://github.com/phona/repo-b/issues/42",
    }
    assert final["gh_incident_kinds"] == {
        "phona/repo-a": "comment",
        "phona/repo-b": "issue",
    }
    add = fake_bkd.merge_tags_and_update.await_args.kwargs["add"]
    assert add.count("github-incident") == 1


# ─── ICP-S3: PR-lookup HTTP error (find_pr returns None) → falls back to issue ─
@pytest.mark.asyncio
async def test_escalate_find_pr_none_falls_back_to_issue(monkeypatch):
    """ICP-S3"""
    from orchestrator.actions import escalate as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    patches = _patch_db(monkeypatch)
    _stub_req_state_get(monkeypatch)
    _stub_pr_merge_probe_disabled(monkeypatch)
    _patch_settings(monkeypatch)

    # find_pr returning None covers both the "really no PR" and "API error"
    # cases — find_pr_for_branch absorbs HTTP errors internally and yields None.
    _fp, co, oi = _patch_gh(
        monkeypatch, find_pr=None, comment=None,
        open_inc="https://github.com/phona/sisyphus/issues/42",
    )

    body = _make_body(issue_id="vfy-3", event="verify.escalate")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
            "involved_repos": ["phona/sisyphus"],
        },
    )
    assert out["escalated"] is True

    co.assert_not_awaited()
    oi.assert_awaited_once()

    final = patches[-1]
    assert final["gh_incident_kinds"]["phona/sisyphus"] == "issue"
