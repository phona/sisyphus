"""Contract tests: sisyphus actively dispatches repository_dispatch before polling.
REQ-426

Black-box challenger. Derived from:
  openspec/changes/REQ-426/specs/pr-ci-active-dispatch/spec.md

Scenarios covered: PRCIAD-S1 through PRCIAD-S4.

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


# ─── Shared helpers ──────────────────────────────────────────────────────────

def _make_response(status_code: int = 204, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or f"HTTP {status_code}"
    return resp


def _make_httpx_mock(per_repo_responses: dict[str, MagicMock] | None = None):
    """Returns (mock_AsyncClient_class, post_calls_list).

    per_repo_responses: {partial_url_fragment: mock_response}
    All unmatched POSTs return HTTP 204 by default.
    """
    post_calls: list[dict] = []
    per_repo_responses = per_repo_responses or {}

    async def _post(url: str, **kwargs):
        call_record = {"url": url, **kwargs}
        post_calls.append(call_record)
        for fragment, resp in per_repo_responses.items():
            if fragment in url:
                return resp
        return _make_response(204)

    mock_client = AsyncMock()
    mock_client.post = _post
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_cls = MagicMock(return_value=mock_cm)
    return mock_cls, post_calls


# ═══════════════════════════════════════════════════════════════════════════════
# PRCIAD-S1: dispatch fires for each repo before polling starts
# ═══════════════════════════════════════════════════════════════════════════════

async def test_prciad_s1_dispatch_fires_for_each_repo_when_enabled(monkeypatch):
    """PRCIAD-S1: pr_ci_dispatch_enabled=True + 2 repos → POST to each repo dispatches endpoint."""
    from orchestrator.actions.create_pr_ci_watch import _dispatch_ci_trigger
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "pr_ci_dispatch_enabled", True)
    monkeypatch.setattr(cfg, "pr_ci_dispatch_event_type", "ci-trigger")
    monkeypatch.setattr(cfg, "github_token", "gh-test-token")

    mock_cls, post_calls = _make_httpx_mock()

    with patch("orchestrator.actions.create_pr_ci_watch.httpx.AsyncClient", mock_cls):
        await _dispatch_ci_trigger(
            repos=["owner/repo-a", "owner/repo-b"],
            branch="feat/REQ-426",
            req_id="REQ-426",
        )

    # Contract 1: exactly 2 POSTs (one per repo)
    assert len(post_calls) == 2, (
        f"PRCIAD-S1: MUST POST exactly twice (once per repo). Got {len(post_calls)}"
    )

    # Contract 2: each POST targets the /dispatches endpoint of the correct repo
    posted_urls = [c["url"] for c in post_calls]
    assert any("owner/repo-a" in u and "/dispatches" in u for u in posted_urls), (
        f"PRCIAD-S1: MUST POST to .../repos/owner/repo-a/dispatches. Got URLs: {posted_urls}"
    )
    assert any("owner/repo-b" in u and "/dispatches" in u for u in posted_urls), (
        f"PRCIAD-S1: MUST POST to .../repos/owner/repo-b/dispatches. Got URLs: {posted_urls}"
    )

    # Contract 3: payload has event_type + client_payload with branch and req_id
    for call_data in post_calls:
        payload = call_data.get("json") or {}
        assert payload.get("event_type") == "ci-trigger", (
            f"PRCIAD-S1: event_type MUST be 'ci-trigger'. Got payload keys={list(payload.keys())}, "
            f"event_type={payload.get('event_type')!r}"
        )
        cp = payload.get("client_payload", {})
        assert "branch" in cp, (
            f"PRCIAD-S1: client_payload MUST contain 'branch'. Got client_payload={cp!r}"
        )
        assert "req_id" in cp, (
            f"PRCIAD-S1: client_payload MUST contain 'req_id'. Got client_payload={cp!r}"
        )
        assert cp["branch"] == "feat/REQ-426", (
            f"PRCIAD-S1: client_payload.branch MUST be 'feat/REQ-426'. Got {cp['branch']!r}"
        )
        assert cp["req_id"] == "REQ-426", (
            f"PRCIAD-S1: client_payload.req_id MUST be 'REQ-426'. Got {cp['req_id']!r}"
        )

    # Contract 4: Authorization: Bearer header present
    for call_data in post_calls:
        headers = call_data.get("headers", {})
        auth_vals = list(headers.values()) if headers else []
        assert any("Bearer" in str(v) for v in auth_vals), (
            f"PRCIAD-S1: Authorization: Bearer header MUST be present. Got headers={headers!r}"
        )
        assert any("gh-test-token" in str(v) for v in auth_vals), (
            f"PRCIAD-S1: Bearer token MUST use settings.github_token. Got headers={headers!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# PRCIAD-S2: dispatch flag disabled — no POST issued
# ═══════════════════════════════════════════════════════════════════════════════

async def test_prciad_s2_dispatch_disabled_no_post_issued(monkeypatch):
    """PRCIAD-S2: pr_ci_dispatch_enabled=False → no POST /dispatches issued at all."""
    from orchestrator.actions.create_pr_ci_watch import _dispatch_ci_trigger
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "pr_ci_dispatch_enabled", False)
    monkeypatch.setattr(cfg, "pr_ci_dispatch_event_type", "ci-trigger")
    monkeypatch.setattr(cfg, "github_token", "gh-test-token")

    mock_cls, post_calls = _make_httpx_mock()

    with patch("orchestrator.actions.create_pr_ci_watch.httpx.AsyncClient", mock_cls):
        await _dispatch_ci_trigger(
            repos=["owner/repo-a", "owner/repo-b"],
            branch="feat/REQ-426",
            req_id="REQ-426",
        )

    # Contract: absolutely no POST calls when flag is False
    dispatch_posts = [c for c in post_calls if "/dispatches" in c.get("url", "")]
    assert not dispatch_posts, (
        f"PRCIAD-S2: NO POST /dispatches MUST be issued when pr_ci_dispatch_enabled=False. "
        f"Got dispatch POSTs: {[c['url'] for c in dispatch_posts]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PRCIAD-S3: one repo dispatch fails — other repos unaffected, polling continues
# ═══════════════════════════════════════════════════════════════════════════════

async def test_prciad_s3_one_repo_fails_others_unaffected(monkeypatch):
    """PRCIAD-S3: repo-a returns 422, repo-b returns 204 → no exception raised, repo-b succeeds."""
    from orchestrator.actions.create_pr_ci_watch import _dispatch_ci_trigger
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "pr_ci_dispatch_enabled", True)
    monkeypatch.setattr(cfg, "pr_ci_dispatch_event_type", "ci-trigger")
    monkeypatch.setattr(cfg, "github_token", "gh-test-token")

    mock_cls, post_calls = _make_httpx_mock({
        "owner/repo-a": _make_response(422, "Unprocessable Entity"),
        "owner/repo-b": _make_response(204),
    })

    raised: Exception | None = None
    with patch("orchestrator.actions.create_pr_ci_watch.httpx.AsyncClient", mock_cls):
        try:
            await _dispatch_ci_trigger(
                repos=["owner/repo-a", "owner/repo-b"],
                branch="feat/REQ-426",
                req_id="REQ-426",
            )
        except Exception as e:
            raised = e

    # Contract 1: no exception raised (best-effort, never propagates)
    assert raised is None, (
        f"PRCIAD-S3: _dispatch_ci_trigger MUST NOT raise when repo-a returns 422. "
        f"Got {type(raised).__name__}: {raised}"
    )

    # Contract 2: both repos were attempted (failure doesn't skip remaining repos)
    posted_urls = [c["url"] for c in post_calls]
    assert any("owner/repo-a" in u for u in posted_urls), (
        f"PRCIAD-S3: repo-a MUST still be attempted. Got URLs: {posted_urls}"
    )
    assert any("owner/repo-b" in u for u in posted_urls), (
        f"PRCIAD-S3: repo-b MUST be attempted even after repo-a fails. Got URLs: {posted_urls}"
    )
    assert len(post_calls) == 2, (
        f"PRCIAD-S3: MUST attempt POST for each repo regardless of failures. "
        f"Got {len(post_calls)} POSTs"
    )


async def test_prciad_s3_all_repos_fail_no_exception(monkeypatch):
    """PRCIAD-S3 extension: ALL repos fail → still no exception raised."""
    from orchestrator.actions.create_pr_ci_watch import _dispatch_ci_trigger
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "pr_ci_dispatch_enabled", True)
    monkeypatch.setattr(cfg, "pr_ci_dispatch_event_type", "ci-trigger")
    monkeypatch.setattr(cfg, "github_token", "gh-test-token")

    mock_cls, post_calls = _make_httpx_mock({
        "owner/repo-a": _make_response(422, "Unprocessable Entity"),
        "owner/repo-b": _make_response(500, "Internal Server Error"),
    })

    raised: Exception | None = None
    with patch("orchestrator.actions.create_pr_ci_watch.httpx.AsyncClient", mock_cls):
        try:
            await _dispatch_ci_trigger(
                repos=["owner/repo-a", "owner/repo-b"],
                branch="feat/REQ-426",
                req_id="REQ-426",
            )
        except Exception as e:
            raised = e

    assert raised is None, (
        f"PRCIAD-S3: _dispatch_ci_trigger MUST NOT raise even when all repos fail. "
        f"Got {type(raised).__name__}: {raised}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PRCIAD-S4: no github_token — dispatch returns 401 warning only
# ═══════════════════════════════════════════════════════════════════════════════

async def test_prciad_s4_empty_token_request_sent_no_exception(monkeypatch):
    """PRCIAD-S4: github_token="" → request sent with empty Bearer; any error logged as warning only."""
    from orchestrator.actions.create_pr_ci_watch import _dispatch_ci_trigger
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "pr_ci_dispatch_enabled", True)
    monkeypatch.setattr(cfg, "pr_ci_dispatch_event_type", "ci-trigger")
    monkeypatch.setattr(cfg, "github_token", "")

    mock_cls, post_calls = _make_httpx_mock({
        "owner/repo-a": _make_response(401, "Unauthorized"),
    })

    raised: Exception | None = None
    with patch("orchestrator.actions.create_pr_ci_watch.httpx.AsyncClient", mock_cls):
        try:
            await _dispatch_ci_trigger(
                repos=["owner/repo-a"],
                branch="feat/REQ-426",
                req_id="REQ-426",
            )
        except Exception as e:
            raised = e

    # Contract 1: no exception propagated
    assert raised is None, (
        f"PRCIAD-S4: _dispatch_ci_trigger MUST NOT raise when token is empty and API returns 401. "
        f"Got {type(raised).__name__}: {raised}"
    )

    # Contract 2: request was still sent (best-effort, does not skip on empty token)
    assert len(post_calls) >= 1, (
        f"PRCIAD-S4: request MUST still be sent even with empty token (spec says it IS sent). "
        f"Got post_calls={post_calls}"
    )

    # Contract 3: Authorization: Bearer header present (with empty value)
    call_data = post_calls[0]
    headers = call_data.get("headers", {})
    has_auth = any("Authorization" in k or "Bearer" in str(v) for k, v in headers.items())
    assert has_auth, (
        f"PRCIAD-S4: Authorization header MUST be present even with empty token. "
        f"Got headers={headers!r}"
    )


async def test_prciad_s4_network_exception_does_not_propagate(monkeypatch):
    """PRCIAD-S4 extension: network exception on dispatch → still no exception propagated."""
    from orchestrator.actions.create_pr_ci_watch import _dispatch_ci_trigger
    from orchestrator.config import settings as cfg
    import httpx

    monkeypatch.setattr(cfg, "pr_ci_dispatch_enabled", True)
    monkeypatch.setattr(cfg, "pr_ci_dispatch_event_type", "ci-trigger")
    monkeypatch.setattr(cfg, "github_token", "gh-test-token")

    async def _raise_network(url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    mock_client = AsyncMock()
    mock_client.post = _raise_network
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_cls = MagicMock(return_value=mock_cm)

    raised: Exception | None = None
    with patch("orchestrator.actions.create_pr_ci_watch.httpx.AsyncClient", mock_cls):
        try:
            await _dispatch_ci_trigger(
                repos=["owner/repo-a"],
                branch="feat/REQ-426",
                req_id="REQ-426",
            )
        except Exception as e:
            raised = e

    assert raised is None, (
        f"PRCIAD-S4: network exception MUST be caught and not propagated. "
        f"Got {type(raised).__name__}: {raised}"
    )
