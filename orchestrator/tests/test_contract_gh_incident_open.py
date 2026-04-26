"""Contract tests for REQ-impl-gh-incident-open-1777173133:
feat(orchestrator): open GitHub issue when REQ enters ESCALATED

Black-box behavioral contracts derived exclusively from:
  openspec/changes/REQ-impl-gh-incident-open-1777173133/specs/gh-incident-open/spec.md

Scenarios covered:
  GHI-S1  open_incident disabled when gh_incident_repo is empty → None, no HTTP
  GHI-S2  open_incident disabled when github_token is empty → None, no HTTP
  GHI-S3  open_incident success → POST correct URL + headers, returns html_url
  GHI-S4  open_incident POST body contains required fields and labels array
  GHI-S5  open_incident HTTP failure (503) → None, does not raise
  GHI-S6  escalate real-escalate: open_incident awaited once, ctx stores URL, github-incident tag
  GHI-S7  escalate idempotent: ctx.gh_incident_url already set → open_incident NOT called
  GHI-S8  escalate auto-resume branch → open_incident NOT called
  GHI-S9  escalate: GH failure (open_incident→None) does not abort the flow
  GHI-S10 escalate: disabled (gh_incident_repo='') → flow proceeds, no github-incident tag

Function signatures verified at test design time (without reading source):
  open_incident(*, req_id, reason, retry_count, intent_issue_id, failed_issue_id,
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
    gh_incident_repo: str = "phona/sisyphus",
    github_token: str = "ghp_test_token",
    gh_incident_labels: list | None = None,
) -> Any:
    s = MagicMock()
    s.gh_incident_repo = gh_incident_repo
    s.github_token = github_token
    s.gh_incident_labels = gh_incident_labels if gh_incident_labels is not None else ["sisyphus:incident"]
    # Fields used by other parts of escalate that are not under test
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
    """Spec: open_incident returns None when either config setting is empty."""

    async def test_ghi_s1_disabled_when_gh_incident_repo_empty(self):
        """
        GHI-S1: gh_incident_repo='' → open_incident returns None without making any HTTP request.
        """
        import orchestrator.gh_incident as ghi

        s = _make_settings(gh_incident_repo="")
        with patch.object(ghi, "settings", s):
            result = await ghi.open_incident(
                req_id="REQ-1",
                reason="x",
                retry_count=0,
                intent_issue_id="i",
                failed_issue_id="f",
                project_id="p",
            )

        assert result is None, (
            f"open_incident MUST return None when gh_incident_repo is empty; got {result!r}"
        )

    async def test_ghi_s2_disabled_when_github_token_empty(self):
        """
        GHI-S2: github_token='' → open_incident returns None without making any HTTP request.
        """
        import orchestrator.gh_incident as ghi

        s = _make_settings(github_token="")
        with patch.object(ghi, "settings", s):
            result = await ghi.open_incident(
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
            "open_incident MUST send an HTTP POST request when both settings are non-empty"
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
# Part 4: escalate action integration — GHI-S6 to GHI-S10
#
# escalate(*, body, req_id, tags, ctx) → dict
# Patches applied to orchestrator.actions.escalate module attributes.
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
    gh_return_url: str | None = "https://github.com/phona/sisyphus/issues/42",
) -> tuple[Any, Any, Any, Any]:
    """Build mock objects for escalate's module-level imports."""

    # Mock gh_incident module
    mock_gh = MagicMock()

    async def _capture_open_incident(**kwargs):
        open_incident_calls.append(dict(kwargs))
        return gh_return_url

    mock_gh.open_incident = _capture_open_incident

    # Mock BKDClient (used as `async with BKDClient(...) as bkd:`)
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

    # Mock req_state module
    mock_rs = MagicMock()

    async def _capture_update_ctx(*args, **kwargs):
        update_ctx_calls.append(_collect_dict_args(*args, **kwargs))

    mock_rs.update_context = _capture_update_ctx
    mock_rs.cas_state = AsyncMock()
    mock_rs.get = AsyncMock()

    # Mock k8s_runner module (cleanup calls — must not fail)
    mock_k8s = MagicMock()
    mock_k8s.cleanup_runner = AsyncMock()
    mock_k8s.delete_runner = AsyncMock()
    mock_k8s.mark_runner_done = AsyncMock()

    return mock_gh, mock_BKDClient, mock_rs, mock_k8s


def _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s):
    """Return list of patch context managers for the escalate module."""
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


class TestEscalateRealEscalateGHIS6:
    """GHI-S6: real-escalate → open_incident called once, ctx URL stored, github-incident tag added."""

    async def test_ghi_s6_real_escalate_opens_incident_and_stores_url(self):
        """
        GHI-S6: body.event='verify.escalate', ctx.escalated_reason='verifier-decision-escalate' →
        - open_incident awaited exactly once with req_id + reason matching input
        - req_state.update_context called with gh_incident_url + gh_incident_opened_at
        - bkd.merge_tags_and_update 'add' contains 'escalated', 'reason:verifier-decision-escalate',
          AND 'github-incident'
        """
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return_url="https://github.com/phona/sisyphus/issues/42",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            try:
                await escalate(
                    body=body,
                    req_id="REQ-test-ghi",
                    tags=["REQ-test-ghi", "verifier"],
                    ctx=ctx,
                )
            except Exception as exc:
                pytest.fail(
                    f"escalate raised unexpectedly in real_escalate path: "
                    f"{type(exc).__name__}: {exc}"
                )

        # Contract 1: open_incident called exactly once
        assert len(open_incident_calls) == 1, (
            f"gh_incident.open_incident MUST be awaited exactly once in real_escalate; "
            f"called {len(open_incident_calls)} time(s). Calls: {open_incident_calls!r}"
        )
        call = open_incident_calls[0]
        assert call.get("req_id") == "REQ-test-ghi", (
            f"open_incident must be called with req_id='REQ-test-ghi'; got call: {call!r}"
        )
        assert call.get("reason") == "verifier-decision-escalate", (
            f"open_incident must be called with reason='verifier-decision-escalate'; "
            f"got call: {call!r}"
        )

        # Contract 2: update_context includes gh_incident_url + gh_incident_opened_at
        url_updates = [u for u in update_ctx_calls if "gh_incident_url" in u]
        assert url_updates, (
            f"req_state.update_context MUST be called with gh_incident_url; "
            f"all captured updates: {update_ctx_calls!r}"
        )
        url_update = url_updates[-1]
        assert url_update["gh_incident_url"] == "https://github.com/phona/sisyphus/issues/42", (
            f"gh_incident_url must equal the URL returned by open_incident; "
            f"got {url_update['gh_incident_url']!r}"
        )
        assert url_update.get("gh_incident_opened_at"), (
            f"update_context MUST include non-empty gh_incident_opened_at; "
            f"got update: {url_update!r}"
        )

        # Contract 3: bkd.merge_tags_and_update add list contains github-incident
        assert merge_tags_calls, (
            "bkd.merge_tags_and_update MUST be called in the real_escalate branch"
        )
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list, (
            f"bkd.merge_tags_and_update 'add' list MUST contain 'github-incident'; "
            f"got add={add_list!r}, full call: {merge_tags_calls[-1]!r}"
        )
        assert "escalated" in add_list, (
            f"bkd.merge_tags_and_update 'add' list MUST still contain 'escalated'; "
            f"got add={add_list!r}"
        )


class TestEscalateIdempotentGHIS7:
    """GHI-S7: ctx.gh_incident_url already set → open_incident NOT called (resume cycle safe)."""

    async def test_ghi_s7_skips_open_incident_when_url_already_in_ctx(self):
        """
        GHI-S7: ctx.gh_incident_url is pre-set →
        open_incident MUST NOT be awaited (idempotent under resume cycles).
        """
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return_url="https://github.com/phona/sisyphus/issues/42",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
            "gh_incident_url": "https://github.com/phona/sisyphus/issues/42",  # pre-set
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            try:
                await escalate(
                    body=body,
                    req_id="REQ-test-ghi",
                    tags=["REQ-test-ghi", "verifier"],
                    ctx=ctx,
                )
            except Exception as exc:
                pytest.fail(
                    f"escalate raised unexpectedly: {type(exc).__name__}: {exc}"
                )

        assert len(open_incident_calls) == 0, (
            f"gh_incident.open_incident MUST NOT be called when ctx.gh_incident_url is already set; "
            f"was called {len(open_incident_calls)} time(s): {open_incident_calls!r}"
        )


class TestEscalateAutoResumeGHIS8:
    """GHI-S8: auto-resume branch (transient + budget remaining) → open_incident NOT called."""

    async def test_ghi_s8_auto_resume_does_not_call_open_incident(self):
        """
        GHI-S8: body.event='session.failed' (transient), auto_retry_count=0 (budget left) →
        open_incident MUST NOT be called; auto-resume follow-up proceeds normally.
        """
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return_url="https://github.com/phona/sisyphus/issues/99",
        )

        body = _make_body("session.failed")
        ctx = {
            "escalated_reason": "session-failed",  # transient reason
            "auto_retry_count": 0,  # budget remaining (0 < _MAX_AUTO_RETRY=2)
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            try:
                await escalate(
                    body=body,
                    req_id="REQ-test-ghi",
                    tags=["REQ-test-ghi"],
                    ctx=ctx,
                )
            except Exception as exc:
                pytest.fail(
                    f"escalate raised unexpectedly in auto-resume path: "
                    f"{type(exc).__name__}: {exc}"
                )

        assert len(open_incident_calls) == 0, (
            f"gh_incident.open_incident MUST NOT be called in the auto-resume branch "
            f"(transient signal with budget remaining); "
            f"was called {len(open_incident_calls)} time(s): {open_incident_calls!r}"
        )


class TestEscalateGHFailureGHIS9:
    """GHI-S9: GH failure (open_incident→None) does NOT abort the escalate flow."""

    async def test_ghi_s9_gh_failure_does_not_abort_escalate(self):
        """
        GHI-S9: open_incident returns None (GH outage) →
        - bkd.merge_tags_and_update MUST still be called
        - action MUST NOT raise
        - ctx MUST NOT receive gh_incident_url
        """
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return_url=None,  # GH outage → open_incident returns None
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
        }

        result = None
        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            try:
                result = await escalate(
                    body=body,
                    req_id="REQ-test-ghi",
                    tags=["REQ-test-ghi", "verifier"],
                    ctx=ctx,
                )
            except Exception as exc:
                pytest.fail(
                    f"escalate MUST NOT raise when open_incident returns None (GH outage); "
                    f"got {type(exc).__name__}: {exc}"
                )

        # Contract 1: bkd.merge_tags_and_update still called
        assert merge_tags_calls, (
            "bkd.merge_tags_and_update MUST still be called even when open_incident returns None"
        )

        # Contract 2: ctx not updated with gh_incident_url
        url_updates = [u for u in update_ctx_calls if "gh_incident_url" in u]
        assert not url_updates, (
            f"ctx MUST NOT receive gh_incident_url when open_incident returns None; "
            f"found: {url_updates!r}"
        )

        # Contract 3: action returns expected shape (not an error)
        if isinstance(result, dict):
            assert result.get("escalated") is True, (
                f"escalate MUST still return {{escalated: True, ...}} even when GH fails; "
                f"got {result!r}"
            )


class TestEscalateDisabledGHIS10:
    """GHI-S10: gh_incident_repo='' → escalate proceeds normally, no github-incident tag."""

    async def test_ghi_s10_disabled_escalate_proceeds_without_github_incident_tag(self):
        """
        GHI-S10: settings.gh_incident_repo='' →
        - escalate does NOT raise
        - ctx NOT mutated with gh_incident_url
        - bkd.merge_tags_and_update 'add' does NOT contain 'github-incident'
        - return value indicates escalated (not an error)
        """
        from orchestrator.actions.escalate import escalate

        open_incident_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings(gh_incident_repo="")  # DISABLED
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            open_incident_calls, merge_tags_calls, update_ctx_calls,
            gh_return_url="https://github.com/phona/sisyphus/issues/1",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
        }

        result = None
        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            try:
                result = await escalate(
                    body=body,
                    req_id="REQ-test-ghi",
                    tags=["REQ-test-ghi", "verifier"],
                    ctx=ctx,
                )
            except Exception as exc:
                pytest.fail(
                    f"escalate MUST NOT raise when gh_incident_repo is empty; "
                    f"got {type(exc).__name__}: {exc}"
                )

        # Contract 1: no github-incident tag in the add list
        if merge_tags_calls:
            add_list = _get_add_list(merge_tags_calls[-1])
            assert "github-incident" not in add_list, (
                f"When gh_incident_repo is empty, 'github-incident' MUST NOT appear "
                f"in bkd.merge_tags_and_update 'add' list; "
                f"got: {add_list!r}, full call: {merge_tags_calls[-1]!r}"
            )

        # Contract 2: ctx not updated with gh_incident_url
        url_updates = [u for u in update_ctx_calls if "gh_incident_url" in u]
        assert not url_updates, (
            f"ctx MUST NOT receive gh_incident_url when gh_incident_repo is empty; "
            f"found: {url_updates!r}"
        )

        # Contract 3: action proceeds (returns expected shape)
        if isinstance(result, dict):
            assert result.get("escalated") is True, (
                f"escalate MUST return {{escalated: True, ...}} even when disabled; "
                f"got {result!r}"
            )
