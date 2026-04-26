"""Contract tests for REQ-gh-incident-per-involved-repo-1777180551:
feat(orchestrator): open one GitHub incident per involved source repo.

Black-box behavioral contracts derived exclusively from:
  openspec/specs/gh-incident-open/spec.md (post REQ-gh-incident-per-involved-repo)

Scenarios covered:
  GHI-S1   open_incident disabled when repo='' → None, no HTTP
  GHI-S2   open_incident disabled when github_token='' → None, no HTTP
  GHI-S3   open_incident success → POST correct URL + headers, returns html_url
  GHI-S4   open_incident POST body contains required fields and labels array
  GHI-S5   open_incident HTTP failure (503) → None, does not raise
  GHI-S6   escalate single involved-repo: one POST, ctx.gh_incident_urls has the entry
  GHI-S7   escalate idempotent: ctx.gh_incident_urls already covers all involved repos → no POST
  GHI-S8   escalate auto-resume branch → no POST
  GHI-S9   escalate: GH failure (open_incident→None) does not abort the flow
  GHI-S10  escalate: no involved_repos and no settings.gh_incident_repo → no POST, no tag
  GHI-S11  escalate multi-involved-repo → one POST per repo, urls dict has both keys
  GHI-S12  escalate partial failure isolated → only successful repo persisted
  GHI-S13  escalate idempotent across multi-repo: only missing repos POSTed on re-entry
  GHI-S14  escalate falls back to settings.gh_incident_repo when involved_repos empty
  GHI-S15  ctx.involved_repos beats settings.gh_incident_repo (layers 1-4 win)

Function signatures verified at test design time:
  open_incident(*, repo, req_id, reason, retry_count, intent_issue_id, failed_issue_id,
                project_id, state=None) -> str | None
  escalate(*, body, req_id, tags, ctx) -> dict

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_settings(
    gh_incident_repo: str = "",
    github_token: str = "ghp_test_token",
    gh_incident_labels: list | None = None,
    default_involved_repos: list | None = None,
) -> Any:
    s = MagicMock()
    s.gh_incident_repo = gh_incident_repo
    s.github_token = github_token
    s.gh_incident_labels = gh_incident_labels if gh_incident_labels is not None else ["sisyphus:incident"]
    s.default_involved_repos = list(default_involved_repos or [])
    s.bkd_base_url = "https://bkd.example.test/api"
    s.bkd_token = "test-token"
    s.max_auto_retries = 2
    return s


def _make_body(event: str = "verify.escalate", project_id: str = "proj-test") -> Any:
    b = MagicMock()
    b.event = event
    b.projectId = project_id
    b.issueId = "fail-issue-ghi"
    return b


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: open_incident — GHI-S1 and GHI-S2 (no HTTP)
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenIncidentDisabled:
    """Spec: open_incident returns None when either input is empty."""

    async def test_ghi_s1_disabled_when_repo_arg_empty(self):
        """
        GHI-S1: open_incident(repo="") → returns None without any HTTP request.
        """
        import orchestrator.gh_incident as ghi

        s = _make_settings()
        with patch.object(ghi, "settings", s):
            result = await ghi.open_incident(
                repo="",
                req_id="REQ-1",
                reason="x",
                retry_count=0,
                intent_issue_id="i",
                failed_issue_id="f",
                project_id="p",
            )

        assert result is None, (
            f"open_incident MUST return None when repo arg is empty; got {result!r}"
        )

    async def test_ghi_s2_disabled_when_github_token_empty(self):
        """
        GHI-S2: github_token='' → open_incident returns None without any HTTP request.
        """
        import orchestrator.gh_incident as ghi

        s = _make_settings(github_token="")
        with patch.object(ghi, "settings", s):
            result = await ghi.open_incident(
                repo="phona/sisyphus",
                req_id="REQ-1",
                reason="x",
                retry_count=0,
                intent_issue_id="i",
                failed_issue_id="f",
                project_id="p",
            )

        assert result is None, (
            f"open_incident MUST return None when github_token is empty; got {result!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: open_incident success — GHI-S3 and GHI-S4
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenIncidentSuccess:
    """Spec: on 201, open_incident returns the html_url, uses correct URL and headers."""

    async def test_ghi_s3_success_returns_html_url_with_correct_request(self, httpx_mock):
        """
        GHI-S3: GH API returns 201 + html_url →
        POST hits https://api.github.com/repos/phona/sisyphus/issues with
        Authorization: Bearer <token> and Accept: application/vnd.github+json.
        Return value equals the html_url string.
        """
        import orchestrator.gh_incident as ghi

        httpx_mock.add_response(
            method="POST",
            url="https://api.github.com/repos/phona/sisyphus/issues",
            json={"html_url": "https://github.com/phona/sisyphus/issues/42"},
            status_code=201,
        )
        s = _make_settings()
        with patch.object(ghi, "settings", s):
            result = await ghi.open_incident(
                repo="phona/sisyphus",
                req_id="REQ-1",
                reason="test-reason",
                retry_count=0,
                intent_issue_id="intent-abc",
                failed_issue_id="fail-xyz",
                project_id="proj-1",
            )

        assert result == "https://github.com/phona/sisyphus/issues/42", (
            f"open_incident MUST return the html_url from GH 201 response; got {result!r}"
        )

        request = httpx_mock.get_request()
        assert request is not None, (
            "open_incident MUST send an HTTP POST request when both inputs are non-empty"
        )
        assert str(request.url) == "https://api.github.com/repos/phona/sisyphus/issues", (
            f"POST URL must be 'https://api.github.com/repos/phona/sisyphus/issues'; "
            f"got {request.url!r}"
        )
        assert request.headers.get("authorization") == "Bearer ghp_test_token", (
            f"Authorization header must be 'Bearer ghp_test_token'; "
            f"got {request.headers.get('authorization')!r}"
        )
        accept_header = request.headers.get("accept", "")
        assert "vnd.github" in accept_header, (
            f"Accept header must contain 'vnd.github'; got {accept_header!r}"
        )

    async def test_ghi_s4_post_body_contains_required_fields_and_correct_labels(self, httpx_mock):
        """
        GHI-S4: POST body must contain all cross-reference substrings:
        REQ-9, fixer-round-cap, intent-1, vfy-3, proj-A, fixer-running.
        labels array must contain 'sisyphus:incident' and 'reason:fixer-round-cap'.
        """
        import orchestrator.gh_incident as ghi

        httpx_mock.add_response(
            method="POST",
            url="https://api.github.com/repos/phona/sisyphus/issues",
            json={"html_url": "https://github.com/phona/sisyphus/issues/7"},
            status_code=201,
        )
        s = _make_settings()
        with patch.object(ghi, "settings", s):
            await ghi.open_incident(
                repo="phona/sisyphus",
                req_id="REQ-9",
                reason="fixer-round-cap",
                retry_count=0,
                intent_issue_id="intent-1",
                failed_issue_id="vfy-3",
                project_id="proj-A",
                state="fixer-running",
            )

        request = httpx_mock.get_request()
        assert request is not None, "Must have sent a POST request"
        body = json.loads(request.content)
        body_str = json.dumps(body)

        for expected_substr in ["REQ-9", "fixer-round-cap", "intent-1", "vfy-3", "proj-A", "fixer-running"]:
            assert expected_substr in body_str, (
                f"POST body MUST contain {expected_substr!r}; body: {body_str!r}"
            )

        labels = body.get("labels", [])
        assert "sisyphus:incident" in labels, (
            f"labels MUST contain 'sisyphus:incident'; got: {labels!r}"
        )
        assert "reason:fixer-round-cap" in labels, (
            f"labels MUST contain 'reason:fixer-round-cap'; got: {labels!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: open_incident HTTP failure — GHI-S5
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenIncidentHTTPFailure:
    """Spec: non-2xx response → None, never raises."""

    async def test_ghi_s5_http_503_returns_none_and_does_not_raise(self, httpx_mock):
        """
        GHI-S5: GH API returns 503 → open_incident MUST return None and MUST NOT raise.
        """
        import orchestrator.gh_incident as ghi

        httpx_mock.add_response(
            method="POST",
            url="https://api.github.com/repos/phona/sisyphus/issues",
            status_code=503,
            text="Service Unavailable",
        )
        s = _make_settings()
        result = None
        with patch.object(ghi, "settings", s):
            try:
                result = await ghi.open_incident(
                    repo="phona/sisyphus",
                    req_id="REQ-1",
                    reason="x",
                    retry_count=0,
                    intent_issue_id="i",
                    failed_issue_id="f",
                    project_id="p",
                )
            except Exception as exc:
                pytest.fail(
                    f"open_incident MUST NOT raise on HTTP 503; "
                    f"got {type(exc).__name__}: {exc}"
                )

        assert result is None, (
            f"open_incident MUST return None on HTTP 503 error; got {result!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: escalate action integration — GHI-S6 .. GHI-S15
# ─────────────────────────────────────────────────────────────────────────────


def _collect_dict_args(*args, **kwargs) -> dict:
    """Collect all dict-like arguments (positional or keyword) into one merged dict."""
    collected: dict = {}
    for a in args:
        if isinstance(a, dict):
            collected.update(a)
    for v in kwargs.values():
        if isinstance(v, dict):
            collected.update(v)
    return collected


def _make_escalate_mocks(
    open_incident_calls: list,
    merge_tags_calls: list,
    update_ctx_calls: list,
    gh_return: Any = "https://github.com/phona/sisyphus/issues/42",
) -> tuple[Any, Any, Any, Any]:
    """Build mock objects for escalate's module-level imports.

    `gh_return` may be:
      - a string  → all open_incident calls return that string
      - None      → all calls return None (GH outage)
      - a dict    → keyed by repo arg; missing keys → None
      - a callable → called with kwargs, must return str | None
    """

    mock_gh = MagicMock()

    async def _capture_open_incident(**kwargs):
        open_incident_calls.append(dict(kwargs))
        if callable(gh_return):
            return gh_return(**kwargs)
        if isinstance(gh_return, dict):
            return gh_return.get(kwargs.get("repo"))
        return gh_return

    mock_gh.open_incident = _capture_open_incident

    mock_bkd_inner = MagicMock()

    async def _capture_merge(*args, **kwargs):
        merge_tags_calls.append({"args": args, "kwargs": kwargs})

    mock_bkd_inner.merge_tags_and_update = _capture_merge
    mock_bkd_inner.follow_up_issue = AsyncMock()
    mock_bkd_inner.update_issue = AsyncMock()
    mock_bkd_inner.get_issue = AsyncMock(return_value=MagicMock(tags=[]))

    mock_bkd_inst = AsyncMock()
    mock_bkd_inst.__aenter__ = AsyncMock(return_value=mock_bkd_inner)
    mock_bkd_inst.__aexit__ = AsyncMock(return_value=False)
    mock_BKDClient = MagicMock(return_value=mock_bkd_inst)

    mock_rs = MagicMock()

    async def _capture_update_ctx(*args, **kwargs):
        update_ctx_calls.append(_collect_dict_args(*args, **kwargs))

    mock_rs.update_context = _capture_update_ctx
    mock_rs.cas_state = AsyncMock()
    mock_rs.get = AsyncMock()

    mock_k8s = MagicMock()
    mock_k8s.cleanup_runner = AsyncMock()
    mock_k8s.delete_runner = AsyncMock()
    mock_k8s.mark_runner_done = AsyncMock()

    return mock_gh, mock_BKDClient, mock_rs, mock_k8s


def _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s):
    return [
        patch("orchestrator.actions.escalate.settings", settings),
        patch("orchestrator.actions.escalate.gh_incident", mock_gh),
        patch("orchestrator.actions.escalate.BKDClient", mock_BKDClient),
        patch("orchestrator.actions.escalate.req_state", mock_rs),
        patch("orchestrator.actions.escalate.k8s_runner", mock_k8s),
        patch("orchestrator.actions.escalate.db", MagicMock()),
    ]


def _get_add_list(merge_call: dict) -> list:
    """Extract the 'add' list from a merge_tags_and_update call record."""
    add_list = merge_call["kwargs"].get("add", [])
    if not add_list:
        for a in merge_call["args"]:
            if isinstance(a, (list, tuple)) and len(a) > 0:
                if any(s.startswith("reason:") or s == "escalated" for s in a if isinstance(s, str)):
                    add_list = list(a)
                    break
    return add_list


def _last_url_update(update_ctx_calls: list) -> dict | None:
    matches = [u for u in update_ctx_calls
               if "gh_incident_urls" in u or "gh_incident_url" in u]
    return matches[-1] if matches else None


class TestEscalateRealEscalateGHIS6:
    """GHI-S6: real-escalate single involved repo → one POST, ctx.gh_incident_urls populated."""

    async def test_ghi_s6_single_involved_repo_opens_one_incident(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return="https://github.com/phona/sisyphus/issues/42",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
            "involved_repos": ["phona/sisyphus"],
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(open_incident_calls) == 1, (
            f"open_incident MUST be awaited exactly once for a single involved repo; "
            f"got {len(open_incident_calls)} calls: {open_incident_calls!r}"
        )
        call = open_incident_calls[0]
        assert call.get("repo") == "phona/sisyphus", (
            f"open_incident must be called with repo='phona/sisyphus'; got {call!r}"
        )
        assert call.get("req_id") == "REQ-test-ghi"
        assert call.get("reason") == "verifier-decision-escalate"

        u = _last_url_update(update_ctx_calls)
        assert u is not None, (
            f"update_context MUST be called with gh_incident_urls; "
            f"all updates: {update_ctx_calls!r}"
        )
        assert u.get("gh_incident_urls") == {
            "phona/sisyphus": "https://github.com/phona/sisyphus/issues/42",
        }, f"gh_incident_urls dict mismatch: {u!r}"
        assert u.get("gh_incident_url") == "https://github.com/phona/sisyphus/issues/42", (
            f"legacy gh_incident_url must equal the first URL: {u!r}"
        )
        assert u.get("gh_incident_opened_at"), (
            f"gh_incident_opened_at MUST be non-empty: {u!r}"
        )

        assert merge_tags_calls
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list
        assert "escalated" in add_list
        assert "reason:verifier-decision-escalate" in add_list


class TestEscalateIdempotentGHIS7:
    """GHI-S7: ctx.gh_incident_urls already covers the involved repo → no POST."""

    async def test_ghi_s7_idempotent_when_urls_dict_covers_all(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return="https://example/should/not/be/called",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
            "involved_repos": ["phona/sisyphus"],
            "gh_incident_urls": {
                "phona/sisyphus": "https://github.com/phona/sisyphus/issues/42",
            },
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(open_incident_calls) == 0, (
            f"open_incident MUST NOT be called when ctx.gh_incident_urls covers "
            f"every involved repo; got {open_incident_calls!r}"
        )
        # github-incident tag still emitted (existing URLs justify it)
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list


class TestEscalateAutoResumeGHIS8:
    """GHI-S8: auto-resume branch → no POST."""

    async def test_ghi_s8_auto_resume_does_not_call_open_incident(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return="https://example/should/not/be/called",
        )

        body = _make_body("session.failed")
        ctx = {
            "escalated_reason": "session-failed",
            "auto_retry_count": 0,  # budget remaining
            "involved_repos": ["phona/sisyphus"],
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi"],
                ctx=ctx,
            )

        assert len(open_incident_calls) == 0, (
            f"open_incident MUST NOT be called in auto-resume; got {open_incident_calls!r}"
        )


class TestEscalateGHFailureGHIS9:
    """GHI-S9: GH outage → open_incident returns None; flow continues, no tag, no URL ctx fields."""

    async def test_ghi_s9_gh_failure_does_not_abort_escalate(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return=None,  # outage
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
            "involved_repos": ["phona/sisyphus"],
        }

        result = None
        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert merge_tags_calls, (
            "bkd.merge_tags_and_update MUST still be called even when open_incident returns None"
        )
        url_updates = [u for u in update_ctx_calls
                       if u.get("gh_incident_url") or u.get("gh_incident_urls")]
        assert not url_updates, (
            f"ctx MUST NOT receive gh_incident_url(s) when GH POSTs all returned None; "
            f"found: {url_updates!r}"
        )
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" not in add_list, (
            f"'github-incident' tag MUST NOT be added when no URL was opened; "
            f"got: {add_list!r}"
        )
        if isinstance(result, dict):
            assert result.get("escalated") is True


class TestEscalateDisabledGHIS10:
    """GHI-S10: no involved_repos and no settings.gh_incident_repo → no POST, no tag."""

    async def test_ghi_s10_disabled_default_keeps_old_behavior(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings(gh_incident_repo="")
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return="https://example/should/not/be/called",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
            # NO involved_repos
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(open_incident_calls) == 0, (
            f"open_incident MUST NOT be called when all 5 layers are empty; "
            f"got: {open_incident_calls!r}"
        )
        if merge_tags_calls:
            add_list = _get_add_list(merge_tags_calls[-1])
            assert "github-incident" not in add_list, (
                f"'github-incident' tag MUST NOT appear; got: {add_list!r}"
            )
        url_updates = [u for u in update_ctx_calls
                       if u.get("gh_incident_url") or u.get("gh_incident_urls")]
        assert not url_updates, (
            f"ctx MUST NOT receive gh_incident_url(s); found: {url_updates!r}"
        )
        if isinstance(result, dict):
            assert result.get("escalated") is True


class TestEscalateMultiRepoGHIS11:
    """GHI-S11: multi-repo REQ → one POST per involved repo."""

    async def test_ghi_s11_multi_repo_one_incident_per_repo(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        gh_table = {
            "phona/repo-a": "https://github.com/phona/repo-a/issues/7",
            "phona/repo-b": "https://github.com/phona/repo-b/issues/3",
        }
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return=gh_table,
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(open_incident_calls) == 2, (
            f"open_incident MUST be called once per involved repo (2 expected); "
            f"got: {open_incident_calls!r}"
        )
        called_repos = sorted(c["repo"] for c in open_incident_calls)
        assert called_repos == ["phona/repo-a", "phona/repo-b"]

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_urls"] == gh_table

        add_list = _get_add_list(merge_tags_calls[-1])
        assert add_list.count("github-incident") == 1


class TestEscalatePartialFailureGHIS12:
    """GHI-S12: partial repo failure isolated; succeeded repo persisted."""

    async def test_ghi_s12_partial_failure_isolated(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        gh_table = {
            "phona/repo-a": None,  # 403 / outage
            "phona/repo-b": "https://github.com/phona/repo-b/issues/3",
        }
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return=gh_table,
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
        }

        result = None
        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        if isinstance(result, dict):
            assert result.get("escalated") is True

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_urls"] == {
            "phona/repo-b": "https://github.com/phona/repo-b/issues/3",
        }, f"Only the successful repo MUST be persisted; got {u!r}"

        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list


class TestEscalateMultiRepoIdempotentGHIS13:
    """GHI-S13: re-entry only POSTs the missing repos."""

    async def test_ghi_s13_only_missing_repos_posted_on_reentry(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return="https://github.com/phona/repo-b/issues/3",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
            "gh_incident_urls": {
                "phona/repo-a": "https://github.com/phona/repo-a/issues/7",
            },
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(open_incident_calls) == 1, (
            f"Only the missing repo MUST be re-POSTed; got: {open_incident_calls!r}"
        )
        assert open_incident_calls[0]["repo"] == "phona/repo-b"

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_urls"] == {
            "phona/repo-a": "https://github.com/phona/repo-a/issues/7",
            "phona/repo-b": "https://github.com/phona/repo-b/issues/3",
        }, f"merged urls dict mismatch: {u!r}"


class TestEscalateLegacyFallbackGHIS14:
    """GHI-S14: no involved_repos → fall back to settings.gh_incident_repo."""

    async def test_ghi_s14_falls_back_to_settings_gh_incident_repo(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings(gh_incident_repo="phona/sisyphus")
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return="https://github.com/phona/sisyphus/issues/99",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            # No involved_repos / no repo: tags / no default_involved_repos
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(open_incident_calls) == 1, (
            f"open_incident MUST be called once with the legacy fallback repo; "
            f"got: {open_incident_calls!r}"
        )
        assert open_incident_calls[0]["repo"] == "phona/sisyphus"

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_urls"] == {
            "phona/sisyphus": "https://github.com/phona/sisyphus/issues/99",
        }
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list


class TestEscalateLayerPrecedenceGHIS15:
    """GHI-S15: layers 1-4 (involved_repos) win over layer 5 (gh_incident_repo)."""

    async def test_ghi_s15_involved_repos_take_precedence(self):
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings(gh_incident_repo="phona/sisyphus")  # layer 5 also set
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return="https://github.com/phona/repo-a/issues/1",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/repo-a"],  # layer 2
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(open_incident_calls) == 1, (
            f"Only the layer-2 repo MUST be POSTed; got: {open_incident_calls!r}"
        )
        assert open_incident_calls[0]["repo"] == "phona/repo-a", (
            f"layer 2 MUST beat layer 5; got repo={open_incident_calls[0]['repo']!r}"
        )

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert set(u["gh_incident_urls"].keys()) == {"phona/repo-a"}, (
            f"Only the layer-2 repo MUST appear in gh_incident_urls; got: {u!r}"
        )
