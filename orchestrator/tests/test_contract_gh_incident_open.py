"""Contract tests for the gh-incident-open capability.

Black-box behavioral contracts derived exclusively from:
  openspec/specs/gh-incident-open/spec.md
+ openspec/changes/REQ-one-pr-per-req-1777218057/specs/gh-incident-open/spec.md

History:
  - REQ-gh-incident-per-involved-repo-1777180551: original per-involved-repo
    issue creation behavior (GHI-S1..GHI-S15).
  - REQ-one-pr-per-req-1777218057: escalate now comments on the existing
    feat/{REQ} PR via comment_on_pr; falls back to open_incident only when
    no PR exists for the REQ on this repo. open_incident's own unit-level
    behavior (GHI-S1..GHI-S5) is unchanged.

Scenarios covered:
  GHI-S1   open_incident disabled when repo='' → None, no HTTP
  GHI-S2   open_incident disabled when github_token='' → None, no HTTP
  GHI-S3   open_incident success → POST correct URL + headers, returns html_url
  GHI-S4   open_incident POST body contains required fields and labels array
  GHI-S5   open_incident HTTP failure (503) → None, does not raise
  GHI-S6   escalate single involved-repo: PR found → comment_on_pr (not open_incident);
            ctx.gh_incident_urls + ctx.gh_incident_kinds populated
  GHI-S7   escalate idempotent: ctx.gh_incident_urls covers all repos → no GH calls
  GHI-S8   escalate auto-resume branch → no GH calls
  GHI-S9   escalate: comment_on_pr returns None → no fallback to open_incident
            (PR was found; no point trying issue), no tag, flow continues
  GHI-S10  escalate: no involved_repos and no settings.gh_incident_repo → no calls, no tag
  GHI-S11  escalate multi-involved-repo → one comment per repo
  GHI-S12  escalate partial failure isolated → only successful repo persisted
  GHI-S13  escalate idempotent across multi-repo: only missing repos commented on re-entry
  GHI-S14  escalate falls back to settings.gh_incident_repo when involved_repos empty;
            since the inbox repo has no per-REQ PR, falls through to open_incident
  GHI-S15  ctx.involved_repos beats settings.gh_incident_repo (layers 1-4 win)
  ICP-S1   no PR for feat/{REQ} → falls back to open_incident; gh_incident_kinds = "issue"
  ICP-S2   mixed multi-repo: comment for repo with PR, issue for repo without
  ICP-S3   PR-lookup error (find_pr returns None) treated as "no PR" → falls back to issue

Function signatures verified at test design time:
  find_pr_for_branch(*, repo, branch) -> int | None
  comment_on_pr(*, repo, pr_number, req_id, reason, retry_count, intent_issue_id,
                failed_issue_id, project_id, state=None) -> str | None
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
# Part 1: open_incident unit-level (GHI-S1..GHI-S5) — unchanged by REQ-one-pr-per-req
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenIncidentDisabled:
    """Spec: open_incident returns None when either input is empty."""

    async def test_ghi_s1_disabled_when_repo_arg_empty(self):
        """GHI-S1: open_incident(repo="") → returns None without any HTTP request."""
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
        """GHI-S2: github_token='' → open_incident returns None without any HTTP request."""
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


class TestOpenIncidentSuccess:
    """Spec: on 201, open_incident returns the html_url, uses correct URL and headers."""

    async def test_ghi_s3_success_returns_html_url_with_correct_request(self, httpx_mock):
        """GHI-S3"""
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

        assert result == "https://github.com/phona/sisyphus/issues/42"
        request = httpx_mock.get_request()
        assert request is not None
        assert str(request.url) == "https://api.github.com/repos/phona/sisyphus/issues"
        assert request.headers.get("authorization") == "Bearer ghp_test_token"
        accept_header = request.headers.get("accept", "")
        assert "vnd.github" in accept_header

    async def test_ghi_s4_post_body_contains_required_fields_and_correct_labels(self, httpx_mock):
        """GHI-S4"""
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
        assert request is not None
        body = json.loads(request.content)
        body_str = json.dumps(body)

        for expected_substr in ["REQ-9", "fixer-round-cap", "intent-1", "vfy-3", "proj-A", "fixer-running"]:
            assert expected_substr in body_str, (
                f"POST body MUST contain {expected_substr!r}; body: {body_str!r}"
            )

        labels = body.get("labels", [])
        assert "sisyphus:incident" in labels
        assert "reason:fixer-round-cap" in labels


class TestOpenIncidentHTTPFailure:
    """Spec: non-2xx response → None, never raises."""

    async def test_ghi_s5_http_503_returns_none_and_does_not_raise(self, httpx_mock):
        """GHI-S5"""
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

        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: escalate integration — REQ-one-pr-per-req comment-first behavior
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


def _resolve_table(spec: Any, *, key: Any, kwargs: dict) -> Any:
    """Resolve a return spec from per-helper mock config.

    spec may be: a literal value, a dict keyed on `key`, or a callable(**kwargs).
    """
    if callable(spec):
        return spec(**kwargs)
    if isinstance(spec, dict):
        return spec.get(key)
    return spec


def _make_escalate_mocks(
    *,
    find_pr_calls: list,
    comment_calls: list,
    open_inc_calls: list,
    merge_tags_calls: list,
    update_ctx_calls: list,
    find_pr_return: Any = None,
    comment_return: Any = None,
    open_inc_return: Any = None,
) -> tuple[Any, Any, Any, Any]:
    """Build mock objects for escalate's three gh_incident helpers + BKD/db plumbing.

    Each *_return may be: a literal value, a dict (keyed on repo or (repo, pr_number)),
    or a callable(**kwargs). Calls are recorded into the corresponding *_calls list.
    """
    mock_gh = MagicMock()

    async def _capture_find_pr(**kwargs):
        find_pr_calls.append(dict(kwargs))
        return _resolve_table(find_pr_return, key=kwargs.get("repo"), kwargs=kwargs)

    async def _capture_comment(**kwargs):
        comment_calls.append(dict(kwargs))
        return _resolve_table(
            comment_return,
            key=(kwargs.get("repo"), kwargs.get("pr_number")),
            kwargs=kwargs,
        )

    async def _capture_open(**kwargs):
        open_inc_calls.append(dict(kwargs))
        return _resolve_table(open_inc_return, key=kwargs.get("repo"), kwargs=kwargs)

    mock_gh.find_pr_for_branch = _capture_find_pr
    mock_gh.comment_on_pr = _capture_comment
    mock_gh.open_incident = _capture_open

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
        # PR-merged shortcut sometimes preempts the real-escalate branch when
        # GH stubs return unexpected truthy values; force-disable it so all
        # contract tests below exercise the real-escalate path deterministically.
        patch(
            "orchestrator.actions.escalate._all_prs_merged_for_req",
            AsyncMock(return_value=False),
        ),
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


def _run_with_patches(patches: list, coro_fn):
    """Run `coro_fn()` inside the stack of patches. Returns the coroutine result."""
    # Local helper to avoid contextlib.ExitStack noise across many tests.
    p0, p1, p2, p3, p4, p5, p6 = patches
    with p0, p1, p2, p3, p4, p5, p6:
        return coro_fn()


class TestEscalateRealEscalateGHIS6:
    """GHI-S6: real-escalate single involved repo with PR found → comment_on_pr."""

    async def test_ghi_s6_single_involved_repo_comments_on_pr(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=42,
            comment_return="https://github.com/phona/sisyphus/pull/42#issuecomment-99",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
            "involved_repos": ["phona/sisyphus"],
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(find_pr_calls) == 1
        assert find_pr_calls[0]["repo"] == "phona/sisyphus"
        assert find_pr_calls[0]["branch"] == "feat/REQ-test-ghi"

        assert len(comment_calls) == 1, (
            f"comment_on_pr MUST be awaited exactly once when PR is found; "
            f"got {comment_calls!r}"
        )
        c = comment_calls[0]
        assert c["repo"] == "phona/sisyphus"
        assert c["pr_number"] == 42
        assert c["req_id"] == "REQ-test-ghi"
        assert c["reason"] == "verifier-decision-escalate"

        assert len(open_inc_calls) == 0, (
            f"open_incident MUST NOT be awaited when PR is found; got {open_inc_calls!r}"
        )

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u.get("gh_incident_urls") == {
            "phona/sisyphus": "https://github.com/phona/sisyphus/pull/42#issuecomment-99",
        }
        assert u.get("gh_incident_kinds") == {"phona/sisyphus": "comment"}
        assert u.get("gh_incident_url") == "https://github.com/phona/sisyphus/pull/42#issuecomment-99"
        assert u.get("gh_incident_opened_at")

        assert merge_tags_calls
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list
        assert "escalated" in add_list
        assert "reason:verifier-decision-escalate" in add_list


class TestEscalateIdempotentGHIS7:
    """GHI-S7: ctx.gh_incident_urls already covers the involved repo → no GH calls."""

    async def test_ghi_s7_idempotent_when_urls_dict_covers_all(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=42,
            comment_return="https://example/should/not/be/called",
            open_inc_return="https://example/should/not/be/called",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
            "involved_repos": ["phona/sisyphus"],
            "gh_incident_urls": {
                "phona/sisyphus": "https://github.com/phona/sisyphus/pull/42#issuecomment-99",
            },
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(find_pr_calls) == 0
        assert len(comment_calls) == 0
        assert len(open_inc_calls) == 0
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list


class TestEscalateAutoResumeGHIS8:
    """GHI-S8: auto-resume branch → no GH calls of any kind."""

    async def test_ghi_s8_auto_resume_does_not_call_gh_helpers(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=42,
            comment_return="x",
            open_inc_return="x",
        )

        body = _make_body("session.failed")
        ctx = {
            "escalated_reason": "session-failed",
            "auto_retry_count": 0,
            "involved_repos": ["phona/sisyphus"],
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi"],
                ctx=ctx,
            )

        assert len(find_pr_calls) == 0
        assert len(comment_calls) == 0
        assert len(open_inc_calls) == 0


class TestEscalateGHFailureGHIS9:
    """GHI-S9: PR found but comment_on_pr returns None → no fallback to issue, no tag."""

    async def test_ghi_s9_comment_failure_does_not_abort_escalate(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=42,
            comment_return=None,
            open_inc_return=None,
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
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(comment_calls) == 1
        assert len(open_inc_calls) == 0, (
            "PR was found, so escalate must NOT fall through to open_incident "
            "even when comment_on_pr fails"
        )
        assert merge_tags_calls
        url_updates = [u for u in update_ctx_calls
                       if u.get("gh_incident_url") or u.get("gh_incident_urls")]
        assert not url_updates, f"no URL ctx fields when no GH artifact landed: {url_updates!r}"
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" not in add_list
        if isinstance(result, dict):
            assert result.get("escalated") is True


class TestEscalateDisabledGHIS10:
    """GHI-S10: no involved_repos and no settings.gh_incident_repo → no calls, no tag."""

    async def test_ghi_s10_disabled_default_keeps_old_behavior(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings(gh_incident_repo="")
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return="https://example/should/not/be/called",
            comment_return="https://example/should/not/be/called",
            open_inc_return="https://example/should/not/be/called",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "escalated_source_issue_id": "fail-issue-ghi",
            "auto_retry_count": 5,
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(find_pr_calls) == 0
        assert len(comment_calls) == 0
        assert len(open_inc_calls) == 0
        if merge_tags_calls:
            add_list = _get_add_list(merge_tags_calls[-1])
            assert "github-incident" not in add_list
        url_updates = [u for u in update_ctx_calls
                       if u.get("gh_incident_url") or u.get("gh_incident_urls")]
        assert not url_updates
        if isinstance(result, dict):
            assert result.get("escalated") is True


class TestEscalateMultiRepoGHIS11:
    """GHI-S11: multi-repo REQ → one comment per involved repo."""

    async def test_ghi_s11_multi_repo_one_comment_per_repo(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        pr_table = {"phona/repo-a": 7, "phona/repo-b": 3}
        comment_table = {
            ("phona/repo-a", 7): "https://github.com/phona/repo-a/pull/7#issuecomment-1",
            ("phona/repo-b", 3): "https://github.com/phona/repo-b/pull/3#issuecomment-2",
        }
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=pr_table,
            comment_return=comment_table,
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(comment_calls) == 2
        called_repos = sorted(c["repo"] for c in comment_calls)
        assert called_repos == ["phona/repo-a", "phona/repo-b"]
        assert len(open_inc_calls) == 0

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_urls"] == {
            "phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1",
            "phona/repo-b": "https://github.com/phona/repo-b/pull/3#issuecomment-2",
        }
        assert u["gh_incident_kinds"] == {
            "phona/repo-a": "comment",
            "phona/repo-b": "comment",
        }

        add_list = _get_add_list(merge_tags_calls[-1])
        assert add_list.count("github-incident") == 1


class TestEscalatePartialFailureGHIS12:
    """GHI-S12: partial repo failure isolated; succeeded repo persisted."""

    async def test_ghi_s12_partial_failure_isolated(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        pr_table = {"phona/repo-a": 7, "phona/repo-b": 3}
        comment_table = {
            ("phona/repo-a", 7): None,  # 4xx / outage
            ("phona/repo-b", 3): "https://github.com/phona/repo-b/pull/3#issuecomment-2",
        }
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=pr_table,
            comment_return=comment_table,
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
        }

        result = None
        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
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
            "phona/repo-b": "https://github.com/phona/repo-b/pull/3#issuecomment-2",
        }
        assert u["gh_incident_kinds"] == {"phona/repo-b": "comment"}

        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list


class TestEscalateMultiRepoIdempotentGHIS13:
    """GHI-S13: re-entry only POSTs the missing repos."""

    async def test_ghi_s13_only_missing_repos_posted_on_reentry(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        pr_table = {"phona/repo-b": 3}
        comment_table = {
            ("phona/repo-b", 3): "https://github.com/phona/repo-b/pull/3#issuecomment-2",
        }
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=pr_table,
            comment_return=comment_table,
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
            "gh_incident_urls": {
                "phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1",
            },
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(comment_calls) == 1
        assert comment_calls[0]["repo"] == "phona/repo-b"
        assert comment_calls[0]["pr_number"] == 3
        assert len(open_inc_calls) == 0

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_urls"] == {
            "phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1",
            "phona/repo-b": "https://github.com/phona/repo-b/pull/3#issuecomment-2",
        }


class TestEscalateLegacyFallbackGHIS14:
    """GHI-S14: no involved_repos → fall back to settings.gh_incident_repo.

    The triage-inbox repo has no per-REQ PR, so find_pr_for_branch returns
    None and escalate falls through to open_incident (issue creation).
    """

    async def test_ghi_s14_falls_back_to_settings_gh_incident_repo_via_issue(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings(gh_incident_repo="phona/sisyphus")
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=None,
            comment_return=None,
            open_inc_return="https://github.com/phona/sisyphus/issues/99",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(find_pr_calls) == 1
        assert find_pr_calls[0]["repo"] == "phona/sisyphus"
        assert len(comment_calls) == 0
        assert len(open_inc_calls) == 1
        assert open_inc_calls[0]["repo"] == "phona/sisyphus"

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_urls"] == {
            "phona/sisyphus": "https://github.com/phona/sisyphus/issues/99",
        }
        assert u["gh_incident_kinds"] == {"phona/sisyphus": "issue"}
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list


class TestEscalateLayerPrecedenceGHIS15:
    """GHI-S15: layers 1-4 (involved_repos) win over layer 5 (gh_incident_repo)."""

    async def test_ghi_s15_involved_repos_take_precedence(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings(gh_incident_repo="phona/sisyphus")
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=1,
            comment_return="https://github.com/phona/repo-a/pull/1#issuecomment-1",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/repo-a"],
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(comment_calls) == 1
        assert comment_calls[0]["repo"] == "phona/repo-a"
        assert len(open_inc_calls) == 0

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert set(u["gh_incident_urls"].keys()) == {"phona/repo-a"}


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: REQ-one-pr-per-req fallback paths — ICP-S1..ICP-S3
# ─────────────────────────────────────────────────────────────────────────────


class TestEscalateFallsBackToIssueICPS1:
    """ICP-S1: no PR for feat/{REQ} → falls back to open_incident."""

    async def test_icp_s1_falls_back_to_issue_when_no_pr(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=None,
            comment_return=None,
            open_inc_return="https://github.com/phona/sisyphus/issues/42",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/sisyphus"],
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(find_pr_calls) == 1
        assert find_pr_calls[0]["repo"] == "phona/sisyphus"
        assert find_pr_calls[0]["branch"] == "feat/REQ-test-ghi"
        assert len(comment_calls) == 0
        assert len(open_inc_calls) == 1
        assert open_inc_calls[0]["repo"] == "phona/sisyphus"
        assert open_inc_calls[0]["req_id"] == "REQ-test-ghi"
        assert open_inc_calls[0]["reason"] == "verifier-decision-escalate"

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_urls"] == {
            "phona/sisyphus": "https://github.com/phona/sisyphus/issues/42",
        }
        assert u["gh_incident_kinds"] == {"phona/sisyphus": "issue"}
        add_list = _get_add_list(merge_tags_calls[-1])
        assert "github-incident" in add_list


class TestEscalateMixedMultiRepoICPS2:
    """ICP-S2: comment for repo with PR, issue for repo without."""

    async def test_icp_s2_mixed_multi_repo_comment_and_issue(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        pr_table = {"phona/repo-a": 7}  # repo-b absent → no PR
        comment_table = {
            ("phona/repo-a", 7): "https://github.com/phona/repo-a/pull/7#issuecomment-1",
        }
        open_table = {
            "phona/repo-b": "https://github.com/phona/repo-b/issues/42",
        }
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=pr_table,
            comment_return=comment_table,
            open_inc_return=open_table,
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/repo-a", "phona/repo-b"],
        }

        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(comment_calls) == 1
        assert comment_calls[0]["repo"] == "phona/repo-a"
        assert len(open_inc_calls) == 1
        assert open_inc_calls[0]["repo"] == "phona/repo-b"

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_urls"] == {
            "phona/repo-a": "https://github.com/phona/repo-a/pull/7#issuecomment-1",
            "phona/repo-b": "https://github.com/phona/repo-b/issues/42",
        }
        assert u["gh_incident_kinds"] == {
            "phona/repo-a": "comment",
            "phona/repo-b": "issue",
        }
        add_list = _get_add_list(merge_tags_calls[-1])
        assert add_list.count("github-incident") == 1


class TestEscalateFindPRErrorICPS3:
    """ICP-S3: find_pr_for_branch returns None on HTTP error → falls back to issue."""

    async def test_icp_s3_find_pr_error_treated_as_no_pr(self):
        from orchestrator.actions.escalate import escalate

        find_pr_calls: list = []
        comment_calls: list = []
        open_inc_calls: list = []
        merge_tags_calls: list = []
        update_ctx_calls: list = []

        settings = _make_settings()
        # find_pr_for_branch absorbs HTTP errors internally and yields None;
        # the contract here is "None ⇒ escalate falls through to open_incident".
        mock_gh, mock_BKDClient, mock_rs, mock_k8s = _make_escalate_mocks(
            find_pr_calls=find_pr_calls,
            comment_calls=comment_calls,
            open_inc_calls=open_inc_calls,
            merge_tags_calls=merge_tags_calls,
            update_ctx_calls=update_ctx_calls,
            find_pr_return=None,
            comment_return=None,
            open_inc_return="https://github.com/phona/sisyphus/issues/42",
        )

        body = _make_body("verify.escalate")
        ctx = {
            "escalated_reason": "verifier-decision-escalate",
            "auto_retry_count": 5,
            "involved_repos": ["phona/sisyphus"],
        }

        result = None
        patches = _escalate_patches(settings, mock_gh, mock_BKDClient, mock_rs, mock_k8s)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await escalate(
                body=body,
                req_id="REQ-test-ghi",
                tags=["REQ-test-ghi", "verifier"],
                ctx=ctx,
            )

        assert len(comment_calls) == 0
        assert len(open_inc_calls) == 1

        u = _last_url_update(update_ctx_calls)
        assert u is not None
        assert u["gh_incident_kinds"]["phona/sisyphus"] == "issue"

        if isinstance(result, dict):
            assert result.get("escalated") is True
