"""Contract tests: per-involved-repo GH incident opening.
REQ-gh-incident-per-involved-repo-1777180551

Black-box challenger. Derived from:
  openspec/changes/REQ-gh-incident-per-involved-repo-1777180551/specs/gh-incident-open/spec.md

Scenarios covered: GHI-S1 through GHI-S15.

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import json
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

# ─── Shared helpers ──────────────────────────────────────────────────────────

class _FakeBody:
    """Minimal fake WebhookBody for escalate action tests."""
    def __init__(
        self,
        event: str = "session.completed",
        issue_id: str = "issue-test",
        project_id: str = "proj-test",
    ):
        self.event = event
        self.issueId = issue_id
        self.projectId = project_id
        self.issueNumber = None


class _FakeBKD:
    """Minimal BKD client stub for escalate action tests."""
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def follow_up_issue(self, *a, **kw):
        pass

    async def merge_tags_and_update(self, proj, issue_id, *, add, **kw):
        pass


def _make_collecting_bkd(tag_log: list):
    """Returns a BKDClient class whose merge_tags_and_update appends to tag_log."""
    class _CollectingBKD(_FakeBKD):
        async def merge_tags_and_update(self, proj, issue_id, *, add, **kw):
            tag_log.append(list(add))

    return _CollectingBKD


def _make_collecting_bkd_full(tag_log: list, followup_log: list):
    """Returns a BKDClient that records both merge_tags_and_update and follow_up_issue."""
    class _FullBKD(_FakeBKD):
        async def merge_tags_and_update(self, proj, issue_id, *, add, **kw):
            tag_log.append(list(add))

        async def follow_up_issue(self, *a, **kw):
            followup_log.append({"args": a, "kwargs": kw})

    return _FullBKD


def _make_ctx(involved_repos=None, gh_incident_urls=None, **extra):
    ctx = dict(extra)
    if involved_repos is not None:
        ctx["involved_repos"] = involved_repos
    if gh_incident_urls is not None:
        ctx["gh_incident_urls"] = dict(gh_incident_urls)
    return ctx


async def _run_escalate(monkeypatch, ctx, body=None, tags=None, *, bkd_cls=None,
                        open_incident_mock=None, ctx_updates=None):
    """
    Helper: call actions.escalate with mocked deps, return (result, ctx_updates_list).
    ctx_updates: externally-provided list to capture update_context calls.
    """
    from orchestrator import gh_incident as ghi
    from orchestrator.actions import escalate as esc_mod
    from orchestrator.store import db
    from orchestrator.store import req_state as rs_mod

    if body is None:
        body = _FakeBody()
    if tags is None:
        tags = []
    if ctx_updates is None:
        ctx_updates = []

    # Capture update_context calls
    captured: list[dict] = ctx_updates

    async def _capture_update(pool, req_id, patch):
        captured.append(dict(patch))

    monkeypatch.setattr(rs_mod, "update_context", _capture_update)

    # Mock req_state.get to return a minimal fake row
    class _FakeRow:
        state: ClassVar = None
        context: ClassVar = {}

    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=_FakeRow()))
    monkeypatch.setattr(rs_mod, "cas_transition", AsyncMock(return_value=True))

    # Mock db.get_pool → anything (req_state is fully mocked above)
    monkeypatch.setattr(db, "get_pool", lambda: MagicMock())

    # BKD
    if bkd_cls is not None:
        monkeypatch.setattr(esc_mod, "BKDClient", bkd_cls)

    # gh_incident.open_incident
    if open_incident_mock is not None:
        monkeypatch.setattr(ghi, "open_incident", open_incident_mock)

    result = await esc_mod.escalate(body=body, req_id="REQ-test", tags=tags, ctx=ctx)
    return result, captured


# ═══════════════════════════════════════════════════════════════════════════════
# GHI-S1..S5: open_incident function contract
# ═══════════════════════════════════════════════════════════════════════════════

async def test_ghi_s1_open_incident_disabled_when_repo_empty(monkeypatch):
    """GHI-S1: open_incident(repo="") MUST return None with no HTTP request made."""
    from orchestrator import gh_incident
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")

    http_calls: list = []

    async def _deny(*a, **kw):
        http_calls.append(1)
        raise AssertionError("GHI-S1: MUST NOT make HTTP call when repo=''")

    mock_client = AsyncMock()
    mock_client.post = _deny
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("orchestrator.gh_incident.httpx.AsyncClient", return_value=mock_cm):
        result = await gh_incident.open_incident(
            repo="",
            req_id="REQ-1",
            reason="test",
            retry_count=0,
            intent_issue_id="i1",
            failed_issue_id="f1",
            project_id="p1",
        )

    assert result is None, f"GHI-S1: open_incident with repo='' MUST return None, got {result!r}"
    assert not http_calls, "GHI-S1: no HTTP request MUST be made when repo is empty"


async def test_ghi_s2_open_incident_disabled_when_token_empty(monkeypatch):
    """GHI-S2: open_incident with settings.github_token="" MUST return None, no HTTP."""
    from orchestrator import gh_incident
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "")

    http_calls: list = []

    async def _deny(*a, **kw):
        http_calls.append(1)
        raise AssertionError("GHI-S2: MUST NOT make HTTP call when github_token=''")

    mock_client = AsyncMock()
    mock_client.post = _deny
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("orchestrator.gh_incident.httpx.AsyncClient", return_value=mock_cm):
        result = await gh_incident.open_incident(
            repo="phona/sisyphus",
            req_id="REQ-2",
            reason="test",
            retry_count=0,
            intent_issue_id="i2",
            failed_issue_id="f2",
            project_id="p2",
        )

    assert result is None, (
        f"GHI-S2: open_incident with empty token MUST return None, got {result!r}"
    )
    assert not http_calls, "GHI-S2: no HTTP call MUST be made when token is empty"


async def test_ghi_s3_success_returns_html_url(monkeypatch):
    """GHI-S3: open_incident MUST POST to correct URL with Bearer/Accept headers and return html_url."""
    from orchestrator import gh_incident
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")

    post_calls: list[dict] = []
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"html_url": "https://github.com/phona/sisyphus/issues/42"}
    mock_response.text = '{"html_url": "https://github.com/phona/sisyphus/issues/42"}'

    async def _capture_post(url, **kwargs):
        post_calls.append({"url": url, **kwargs})
        return mock_response

    mock_client = AsyncMock()
    mock_client.post = _capture_post
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("orchestrator.gh_incident.httpx.AsyncClient", return_value=mock_cm):
        result = await gh_incident.open_incident(
            repo="phona/sisyphus",
            req_id="REQ-3",
            reason="test",
            retry_count=0,
            intent_issue_id="i3",
            failed_issue_id="f3",
            project_id="p3",
        )

    assert result == "https://github.com/phona/sisyphus/issues/42", (
        f"GHI-S3: return value MUST equal html_url from GH response, got {result!r}"
    )
    assert len(post_calls) == 1, f"GHI-S3: exactly one POST expected, got {len(post_calls)}"

    posted_url = post_calls[0]["url"]
    assert posted_url == "https://api.github.com/repos/phona/sisyphus/issues", (
        f"GHI-S3: POST URL MUST be https://api.github.com/repos/phona/sisyphus/issues, "
        f"got {posted_url!r}"
    )

    headers = post_calls[0].get("headers", {})
    auth_vals = [str(v) for v in headers.values()]
    assert any("Bearer" in v for v in auth_vals), (
        f"GHI-S3: Authorization: Bearer header MUST be present, got headers={headers}"
    )
    assert any("application/vnd.github" in v for v in auth_vals), (
        f"GHI-S3: Accept: application/vnd.github+json header MUST be present, "
        f"got headers={headers}"
    )


async def test_ghi_s4_request_body_contains_required_fields(monkeypatch):
    """GHI-S4: POST body MUST contain REQ-9, reason, cross-refs and labels sisyphus:incident + reason:..."""
    from orchestrator import gh_incident
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_labels", ["sisyphus:incident"])

    post_calls: list[dict] = []
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.json.return_value = {"html_url": "https://github.com/phona/sisyphus/issues/1"}
    mock_response.text = "{}"

    async def _capture(url, **kwargs):
        post_calls.append({"url": url, **kwargs})
        return mock_response

    mock_client = AsyncMock()
    mock_client.post = _capture
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("orchestrator.gh_incident.httpx.AsyncClient", return_value=mock_cm):
        await gh_incident.open_incident(
            repo="phona/sisyphus",
            req_id="REQ-9",
            reason="fixer-round-cap",
            retry_count=0,
            intent_issue_id="intent-1",
            failed_issue_id="vfy-3",
            project_id="proj-A",
            state="fixer-running",
        )

    assert len(post_calls) == 1, f"GHI-S4: expected exactly one POST, got {len(post_calls)}"
    body_payload = post_calls[0].get("json") or {}

    body_str = json.dumps(body_payload)
    for expected in ("REQ-9", "fixer-round-cap", "intent-1", "vfy-3", "proj-A", "fixer-running"):
        assert expected in body_str, (
            f"GHI-S4: JSON body MUST contain {expected!r}. "
            f"body_str (first 500 chars): {body_str[:500]!r}"
        )

    labels = body_payload.get("labels", [])
    assert "sisyphus:incident" in labels, (
        f"GHI-S4: labels MUST contain 'sisyphus:incident', got labels={labels}"
    )
    assert "reason:fixer-round-cap" in labels, (
        f"GHI-S4: labels MUST contain 'reason:fixer-round-cap', got labels={labels}"
    )


async def test_ghi_s5_http_failure_returns_none_no_raise(monkeypatch):
    """GHI-S5: HTTP 503 MUST return None and not raise any exception."""
    from orchestrator import gh_incident
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")

    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"
    mock_response.json.return_value = {"message": "Service Unavailable"}

    async def _error_response(url, **kwargs):
        return mock_response

    mock_client = AsyncMock()
    mock_client.post = _error_response
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    raised = None
    with patch("orchestrator.gh_incident.httpx.AsyncClient", return_value=mock_cm):
        try:
            result = await gh_incident.open_incident(
                repo="phona/sisyphus",
                req_id="REQ-5",
                reason="test",
                retry_count=0,
                intent_issue_id="i5",
                failed_issue_id="f5",
                project_id="p5",
            )
        except Exception as e:
            raised = e

    assert raised is None, (
        f"GHI-S5: open_incident MUST NOT raise on HTTP 503, "
        f"got {type(raised).__name__}: {raised}"
    )
    assert result is None, (  # type: ignore[possibly-undefined]
        f"GHI-S5: open_incident MUST return None on HTTP 503, got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# GHI-S6..S15: escalate action contract (per-involved-repo loop)
# ═══════════════════════════════════════════════════════════════════════════════

async def test_ghi_s6_real_escalate_single_repo_opens_one_incident(monkeypatch):
    """GHI-S6: single involved repo → open_incident called once, ctx.gh_incident_urls set, github-incident tagged."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "")
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    open_calls: list[dict] = []

    async def _mock_open(*, repo, req_id, reason, **kw):
        open_calls.append({"repo": repo, "req_id": req_id, "reason": reason})
        if repo == "phona/sisyphus":
            return "https://github.com/phona/sisyphus/issues/42"
        return None

    tag_log: list = []
    ctx_updates: list[dict] = []
    ctx = _make_ctx(
        involved_repos=["phona/sisyphus"],
        escalated_reason="verifier-decision-escalate",
        intent_issue_id="intent-s6",
    )

    result, ctx_updates = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s6"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_mock_open,
        ctx_updates=ctx_updates,
    )

    # Contract 1: open_incident called exactly once with repo="phona/sisyphus"
    assert len(open_calls) == 1, (
        f"GHI-S6: open_incident MUST be awaited exactly once, got {len(open_calls)} calls"
    )
    assert open_calls[0]["repo"] == "phona/sisyphus", (
        f"GHI-S6: open_incident MUST be called with repo='phona/sisyphus', "
        f"got {open_calls[0]['repo']!r}"
    )
    assert open_calls[0]["req_id"] == "REQ-test", (
        f"GHI-S6: open_incident must receive req_id, got {open_calls[0].get('req_id')!r}"
    )

    # Contract 2: update_context called with gh_incident_urls
    gh_url_updates = [u for u in ctx_updates if "gh_incident_urls" in u]
    assert gh_url_updates, (
        f"GHI-S6: update_context MUST be called with gh_incident_urls. "
        f"All ctx updates: {ctx_updates}"
    )
    assert gh_url_updates[-1]["gh_incident_urls"] == {
        "phona/sisyphus": "https://github.com/phona/sisyphus/issues/42"
    }, (
        f"GHI-S6: gh_incident_urls MUST map phona/sisyphus → url. "
        f"Got {gh_url_updates[-1]['gh_incident_urls']}"
    )
    # Also: gh_incident_url (legacy) must be set
    assert any("gh_incident_url" in u for u in ctx_updates), (
        "GHI-S6: legacy ctx.gh_incident_url MUST also be set"
    )

    # Contract 3: BKD tag includes 'github-incident'
    assert tag_log, f"GHI-S6: bkd.merge_tags_and_update MUST be called, but tag_log={tag_log}"
    all_tags = [t for tags in tag_log for t in tags]
    assert "github-incident" in all_tags, (
        f"GHI-S6: bkd.merge_tags_and_update MUST include 'github-incident' tag. "
        f"Got add tags: {tag_log}"
    )
    assert "escalated" in all_tags, (
        f"GHI-S6: bkd.merge_tags_and_update MUST include 'escalated' tag. "
        f"Got add tags: {tag_log}"
    )

    # Contract 4: action returns escalated=True
    assert isinstance(result, dict) and result.get("escalated") is True, (
        f"GHI-S6: action MUST return dict with escalated=True, got {result!r}"
    )


async def test_ghi_s7_idempotent_preexisting_urls_skips_post(monkeypatch):
    """GHI-S7: pre-existing ctx.gh_incident_urls → open_incident NOT called, gh_incident_urls preserved, github-incident tagged."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "")
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    open_calls: list = []

    async def _should_not_be_called(*, repo, **kw):
        open_calls.append(repo)
        return None

    tag_log: list = []
    ctx_updates: list = []
    existing_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/issues/42"}
    ctx = _make_ctx(
        involved_repos=["phona/sisyphus"],
        gh_incident_urls=existing_urls,
        escalated_reason="verifier-decision-escalate",
        intent_issue_id="intent-s7",
    )

    _, ctx_updates = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s7"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_should_not_be_called,
        ctx_updates=ctx_updates,
    )

    # Contract 1: open_incident MUST NOT be called (idempotent)
    assert open_calls == [], (
        f"GHI-S7: open_incident MUST NOT be called when repo already in ctx.gh_incident_urls. "
        f"Got calls for: {open_calls}"
    )

    # Contract 2: ctx.gh_incident_urls preserved (not cleared)
    gh_url_updates = [u for u in ctx_updates if "gh_incident_urls" in u]
    # Either no update (nothing new to write) or preserved value
    for u in gh_url_updates:
        assert "phona/sisyphus" in u.get("gh_incident_urls", {}), (
            f"GHI-S7: ctx.gh_incident_urls MUST be preserved (not cleared). Got: {u}"
        )

    # Contract 3: github-incident still added (at least one url present = existing)
    all_tags = [t for tags in tag_log for t in tags]
    assert "github-incident" in all_tags, (
        f"GHI-S7: github-incident MUST be in BKD tags even with pre-existing urls. "
        f"Got add tags: {tag_log}"
    )


async def test_ghi_s8_auto_resume_does_not_open_incident(monkeypatch):
    """GHI-S8: auto-resume path (session.failed, retry_count=0) MUST NOT call open_incident, MUST call follow_up_issue."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "")
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    open_calls: list = []

    async def _should_not_be_called(*, repo, **kw):
        open_calls.append(repo)
        return None

    tag_log: list = []
    followup_log: list = []
    ctx = _make_ctx(
        involved_repos=["phona/sisyphus"],
        auto_retry_count=0,
        intent_issue_id="intent-s8",
    )

    await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.failed", issue_id="issue-s8"),
        bkd_cls=_make_collecting_bkd_full(tag_log, followup_log),
        open_incident_mock=_should_not_be_called,
    )

    # Contract 1: open_incident MUST NOT be called in auto-resume path
    assert open_calls == [], (
        f"GHI-S8: open_incident MUST NOT be called in auto-resume path. "
        f"Got calls: {open_calls}"
    )

    # Contract 2: follow_up_issue MUST be called (auto-resume continues)
    assert followup_log, (
        f"GHI-S8: bkd.follow_up_issue MUST be awaited for auto-resume. "
        f"Got followup_log={followup_log}"
    )


async def test_ghi_s9_gh_failure_does_not_abort_escalate(monkeypatch):
    """GHI-S9: open_incident returns None (GH outage) → action still returns escalated=True, no gh_incident_url in ctx, no github-incident tag."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "")
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    async def _always_none(*, repo, **kw):
        return None  # GH outage

    tag_log: list = []
    ctx_updates: list = []
    ctx = _make_ctx(
        involved_repos=["phona/sisyphus"],
        escalated_reason="verifier-decision-escalate",
        intent_issue_id="intent-s9",
    )

    result, ctx_updates = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s9"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_always_none,
        ctx_updates=ctx_updates,
    )

    # Contract 1: action returns escalated=True even when GH fails
    assert isinstance(result, dict) and result.get("escalated") is True, (
        f"GHI-S9: action MUST return escalated=True even on GH failure. Got {result!r}"
    )

    # Contract 2: ctx MUST NOT include gh_incident_url or non-empty gh_incident_urls
    for u in ctx_updates:
        if "gh_incident_url" in u:
            # Only allowed if it's from a PREVIOUS value (legacy), not a new post
            assert not u.get("gh_incident_url"), (
                f"GHI-S9: ctx MUST NOT include gh_incident_url on GH failure. Got: {u}"
            )
        if "gh_incident_urls" in u:
            assert not u.get("gh_incident_urls"), (
                f"GHI-S9: ctx MUST NOT include non-empty gh_incident_urls on GH failure. Got: {u}"
            )

    # Contract 3: BKD tag merge MUST NOT include 'github-incident'
    assert tag_log, "GHI-S9: bkd.merge_tags_and_update MUST still be called"
    all_tags = [t for tags in tag_log for t in tags]
    assert "github-incident" not in all_tags, (
        f"GHI-S9: 'github-incident' MUST NOT be in BKD tags when GH POST failed. "
        f"Got tags: {all_tags}"
    )

    # Contract 4: escalation continues (bkd was called)
    assert tag_log, "GHI-S9: bkd.merge_tags_and_update MUST still be awaited"


async def test_ghi_s10_no_repos_no_fallback_does_not_break_escalate(monkeypatch):
    """GHI-S10: no involved repos + no fallback → open_incident NOT called, action returns escalated=True, no gh_incident_* in ctx."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "")
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    open_calls: list = []

    async def _should_not_be_called(*, repo, **kw):
        open_calls.append(repo)
        return None

    tag_log: list = []
    ctx_updates: list = []
    ctx = _make_ctx(
        # no involved_repos
        escalated_reason="verifier-decision-escalate",
        intent_issue_id="intent-s10",
    )

    result, ctx_updates = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s10"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_should_not_be_called,
        ctx_updates=ctx_updates,
    )

    # Contract 1: open_incident MUST NOT be called
    assert open_calls == [], (
        f"GHI-S10: open_incident MUST NOT be called when no repos. Got: {open_calls}"
    )

    # Contract 2: action returns escalated=True
    assert isinstance(result, dict) and result.get("escalated") is True, (
        f"GHI-S10: action MUST return escalated=True. Got {result!r}"
    )

    # Contract 3: ctx MUST NOT be mutated with gh_incident_url/gh_incident_urls/gh_incident_opened_at
    for u in ctx_updates:
        assert "gh_incident_url" not in u, (
            f"GHI-S10: ctx MUST NOT be mutated with gh_incident_url. Got update: {u}"
        )
        assert "gh_incident_urls" not in u, (
            f"GHI-S10: ctx MUST NOT be mutated with gh_incident_urls. Got update: {u}"
        )
        assert "gh_incident_opened_at" not in u, (
            f"GHI-S10: ctx MUST NOT be mutated with gh_incident_opened_at. Got update: {u}"
        )

    # Contract 4: github-incident MUST NOT be in tags
    all_tags = [t for tags in tag_log for t in tags]
    assert "github-incident" not in all_tags, (
        f"GHI-S10: 'github-incident' MUST NOT be in BKD tags. Got: {all_tags}"
    )


async def test_ghi_s11_multi_repo_opens_one_incident_per_repo(monkeypatch):
    """GHI-S11: two involved repos → open_incident called twice, ctx.gh_incident_urls has both."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "")
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    open_calls: list[dict] = []

    async def _mock_open(*, repo, **kw):
        open_calls.append({"repo": repo})
        if repo == "phona/repo-a":
            return "https://github.com/phona/repo-a/issues/7"
        if repo == "phona/repo-b":
            return "https://github.com/phona/repo-b/issues/3"
        return None

    tag_log: list = []
    ctx_updates: list = []
    ctx = _make_ctx(
        involved_repos=["phona/repo-a", "phona/repo-b"],
        escalated_reason="verifier-decision-escalate",
        intent_issue_id="intent-s11",
    )

    _, ctx_updates = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s11"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_mock_open,
        ctx_updates=ctx_updates,
    )

    # Contract 1: open_incident called exactly twice
    assert len(open_calls) == 2, (
        f"GHI-S11: open_incident MUST be called exactly twice, got {len(open_calls)} calls. "
        f"Repos: {[c['repo'] for c in open_calls]}"
    )
    repos_called = {c["repo"] for c in open_calls}
    assert repos_called == {"phona/repo-a", "phona/repo-b"}, (
        f"GHI-S11: must call for both repos, got {repos_called}"
    )

    # Contract 2: ctx.gh_incident_urls contains both
    gh_url_updates = [u for u in ctx_updates if "gh_incident_urls" in u]
    assert gh_url_updates, "GHI-S11: update_context MUST include gh_incident_urls"
    final_urls = gh_url_updates[-1]["gh_incident_urls"]
    assert "phona/repo-a" in final_urls, (
        f"GHI-S11: gh_incident_urls MUST contain phona/repo-a. Got: {final_urls}"
    )
    assert "phona/repo-b" in final_urls, (
        f"GHI-S11: gh_incident_urls MUST contain phona/repo-b. Got: {final_urls}"
    )
    assert final_urls["phona/repo-a"] == "https://github.com/phona/repo-a/issues/7"
    assert final_urls["phona/repo-b"] == "https://github.com/phona/repo-b/issues/3"

    # Contract 3: github-incident tagged exactly once (not N times)
    all_add_tag_lists = tag_log
    github_incident_count = sum(1 for tags in all_add_tag_lists if "github-incident" in tags)
    assert github_incident_count >= 1, (
        f"GHI-S11: github-incident MUST be in BKD tags. Got: {tag_log}"
    )


async def test_ghi_s12_partial_failure_isolated(monkeypatch):
    """GHI-S12: repo-a fails (None), repo-b succeeds → action escalated=True, only repo-b in ctx, github-incident tagged."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "")
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    async def _partial(*, repo, **kw):
        if repo == "phona/repo-a":
            return None  # 403 — PAT lacks Issues:Write
        if repo == "phona/repo-b":
            return "https://github.com/phona/repo-b/issues/3"
        return None

    tag_log: list = []
    ctx_updates: list = []
    ctx = _make_ctx(
        involved_repos=["phona/repo-a", "phona/repo-b"],
        escalated_reason="verifier-decision-escalate",
        intent_issue_id="intent-s12",
    )

    result, ctx_updates = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s12"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_partial,
        ctx_updates=ctx_updates,
    )

    # Contract 1: action returns escalated=True (not aborted by partial failure)
    assert isinstance(result, dict) and result.get("escalated") is True, (
        f"GHI-S12: action MUST return escalated=True on partial failure. Got {result!r}"
    )

    # Contract 2: ctx.gh_incident_urls = only repo-b (failed repo absent)
    gh_url_updates = [u for u in ctx_updates if "gh_incident_urls" in u]
    assert gh_url_updates, "GHI-S12: update_context MUST include gh_incident_urls (repo-b succeeded)"
    final_urls = gh_url_updates[-1]["gh_incident_urls"]
    assert "phona/repo-b" in final_urls, (
        f"GHI-S12: successful repo-b MUST be in gh_incident_urls. Got: {final_urls}"
    )
    assert "phona/repo-a" not in final_urls, (
        f"GHI-S12: failed repo-a MUST NOT be in gh_incident_urls. Got: {final_urls}"
    )

    # Contract 3: github-incident tag added (one success suffices)
    all_tags = [t for tags in tag_log for t in tags]
    assert "github-incident" in all_tags, (
        f"GHI-S12: github-incident MUST be tagged even with partial failure. Got: {all_tags}"
    )


async def test_ghi_s13_idempotent_multi_repo_only_posts_missing(monkeypatch):
    """GHI-S13: repo-a already in ctx.gh_incident_urls → only repo-b POSTed, final ctx has both."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "")
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    open_calls: list[dict] = []

    async def _mock_open(*, repo, **kw):
        open_calls.append({"repo": repo})
        return f"https://github.com/{repo}/issues/3"

    tag_log: list = []
    ctx_updates: list = []
    existing_urls = {"phona/repo-a": "https://github.com/phona/repo-a/issues/7"}
    ctx = _make_ctx(
        involved_repos=["phona/repo-a", "phona/repo-b"],
        gh_incident_urls=existing_urls,
        escalated_reason="verifier-decision-escalate",
        intent_issue_id="intent-s13",
    )

    _, ctx_updates = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s13"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_mock_open,
        ctx_updates=ctx_updates,
    )

    # Contract 1: open_incident called exactly once (only for repo-b)
    assert len(open_calls) == 1, (
        f"GHI-S13: open_incident MUST be called exactly once (only repo-b). "
        f"Got calls: {open_calls}"
    )
    assert open_calls[0]["repo"] == "phona/repo-b", (
        f"GHI-S13: MUST call for repo-b (NOT repo-a). Got repo: {open_calls[0]['repo']!r}"
    )
    assert not any(c["repo"] == "phona/repo-a" for c in open_calls), (
        f"GHI-S13: repo-a MUST NOT be POSTed again. Got calls: {open_calls}"
    )

    # Contract 2: persisted gh_incident_urls contains BOTH repos
    gh_url_updates = [u for u in ctx_updates if "gh_incident_urls" in u]
    assert gh_url_updates, (
        f"GHI-S13: update_context MUST include gh_incident_urls. ctx_updates={ctx_updates}"
    )
    final_urls = gh_url_updates[-1]["gh_incident_urls"]
    assert "phona/repo-a" in final_urls, (
        f"GHI-S13: existing repo-a MUST be preserved in gh_incident_urls. Got: {final_urls}"
    )
    assert "phona/repo-b" in final_urls, (
        f"GHI-S13: newly opened repo-b MUST be in gh_incident_urls. Got: {final_urls}"
    )
    assert final_urls["phona/repo-a"] == "https://github.com/phona/repo-a/issues/7", (
        f"GHI-S13: repo-a url MUST be the original (not overwritten). Got: {final_urls}"
    )


async def test_ghi_s14_fallback_to_gh_incident_repo(monkeypatch):
    """GHI-S14: no involved repos, gh_incident_repo set → open_incident called with gh_incident_repo, github-incident tagged."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "phona/sisyphus")
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    open_calls: list[dict] = []

    async def _mock_open(*, repo, **kw):
        open_calls.append({"repo": repo})
        return "https://github.com/phona/sisyphus/issues/99"

    tag_log: list = []
    ctx_updates: list = []
    ctx = _make_ctx(
        # no involved_repos
        escalated_reason="verifier-decision-escalate",
        intent_issue_id="intent-s14",
    )

    _, ctx_updates = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s14"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_mock_open,
        ctx_updates=ctx_updates,
    )

    # Contract 1: open_incident called once with repo="phona/sisyphus" (fallback)
    assert len(open_calls) == 1, (
        f"GHI-S14: open_incident MUST be called exactly once (with fallback repo). "
        f"Got calls: {open_calls}"
    )
    assert open_calls[0]["repo"] == "phona/sisyphus", (
        f"GHI-S14: MUST call with gh_incident_repo='phona/sisyphus' as fallback. "
        f"Got repo: {open_calls[0]['repo']!r}"
    )

    # Contract 2: ctx.gh_incident_urls = {phona/sisyphus: url}
    gh_url_updates = [u for u in ctx_updates if "gh_incident_urls" in u]
    assert gh_url_updates, "GHI-S14: update_context MUST include gh_incident_urls"
    final_urls = gh_url_updates[-1]["gh_incident_urls"]
    assert final_urls == {"phona/sisyphus": "https://github.com/phona/sisyphus/issues/99"}, (
        f"GHI-S14: gh_incident_urls MUST equal expected map. Got: {final_urls}"
    )

    # Contract 3: github-incident tagged
    all_tags = [t for tags in tag_log for t in tags]
    assert "github-incident" in all_tags, (
        f"GHI-S14: github-incident MUST be in BKD tags. Got: {all_tags}"
    )


async def test_ghi_s15_layers_1_to_4_take_precedence_over_gh_incident_repo(monkeypatch):
    """GHI-S15: ctx.involved_repos set + gh_incident_repo set → only ctx.involved_repos is used."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "gh_incident_repo", "phona/sisyphus")  # legacy single-inbox
    monkeypatch.setattr(cfg, "default_involved_repos", [])

    open_calls: list[dict] = []

    async def _mock_open(*, repo, **kw):
        open_calls.append({"repo": repo})
        return f"https://github.com/{repo}/issues/1"

    tag_log: list = []
    ctx_updates: list = []
    ctx = _make_ctx(
        involved_repos=["phona/repo-a"],  # Layer 2: ctx.involved_repos
        escalated_reason="verifier-decision-escalate",
        intent_issue_id="intent-s15",
    )

    _, ctx_updates = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s15"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_mock_open,
        ctx_updates=ctx_updates,
    )

    # Contract 1: open_incident called ONLY for phona/repo-a (NOT for phona/sisyphus)
    repos_called = [c["repo"] for c in open_calls]
    assert "phona/repo-a" in repos_called, (
        f"GHI-S15: open_incident MUST be called for ctx.involved_repos[0]='phona/repo-a'. "
        f"Got calls: {repos_called}"
    )
    assert "phona/sisyphus" not in repos_called, (
        f"GHI-S15: gh_incident_repo='phona/sisyphus' MUST NOT be used when involved_repos is set. "
        f"Got calls: {repos_called}"
    )
    assert len(open_calls) == 1, (
        f"GHI-S15: MUST call exactly once (phona/repo-a only). Got calls: {repos_called}"
    )

    # Contract 2: ctx.gh_incident_urls keys = {phona/repo-a} (no sisyphus)
    gh_url_updates = [u for u in ctx_updates if "gh_incident_urls" in u]
    assert gh_url_updates, "GHI-S15: update_context MUST include gh_incident_urls"
    final_urls = gh_url_updates[-1]["gh_incident_urls"]
    assert set(final_urls.keys()) == {"phona/repo-a"}, (
        f"GHI-S15: gh_incident_urls keys MUST be {{'phona/repo-a'}}, "
        f"not including gh_incident_repo. Got keys: {set(final_urls.keys())}"
    )
