"""Challenger contract tests for REQ-pr-issue-traceability-1777218612.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-pr-issue-traceability-1777218612/specs/cross-link/spec.md

Scenarios covered:
  XLINK-S1  bkd_issue_url: base url with /api suffix derives frontend
  XLINK-S2  bkd_issue_url: explicit frontend url override beats base url
  XLINK-S3  bkd_issue_url: missing identifiers return None
  XLINK-S4  bkd_issue_url: unparseable bkd_base_url returns None when override empty
  XLINK-S5  format_pr_links_md: multi-repo dict produces sorted bullet list
  XLINK-S6  format_pr_links_md: empty / None / non-dict input returns empty list
  XLINK-S7  webhook: fresh REQ insert_init includes bkd_intent_url in context
  XLINK-S8  webhook: bkd_issue_url returning None omits bkd_intent_url from context
  XLINK-S9  create_pr_ci_watch: successful discovery persists pr_urls + enters dispatch
  XLINK-S10 create_pr_ci_watch: empty discovery does not call update_context
  XLINK-S11 create_pr_ci_watch: discovery exception does not abort dispatch
  XLINK-S12 gh_incident: body contains markdown link to BKD intent when url provided
  XLINK-S13 gh_incident: body contains PR markdown links when pr_urls provided
  XLINK-S14 gh_incident: absent pr_urls does not add **PRs**: section
  XLINK-S15 escalate: threads bkd_intent_url and pr_urls from ctx to open_incident
  XLINK-S16 analyze prompt: renders cross-link block when bkd_intent_issue_url provided
  XLINK-S17 analyze prompt: omits BKD link line when bkd_intent_issue_url empty
  XLINK-S18 done_archive prompt: renders Known PRs section when pr_urls present
  XLINK-S19 done_archive prompt: omits Known PRs heading when pr_urls absent
  XLINK-S20 SQL query: returns bkd_intent_url and pr_urls_md columns (integration)
  XLINK-S21 SQL query: tolerates missing context fields returning NULL (integration)

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.
"""
from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_settings(
    bkd_base_url: str = "https://bkd.example.test/api",
    bkd_frontend_url: str = "",
    bkd_token: str = "test-token",
    github_token: str = "ghp_test_token",
    gh_incident_repo: str = "phona/sisyphus",
    gh_incident_labels: list | None = None,
    max_auto_retries: int = 2,
    default_involved_repos: list | None = None,
    webhook_token: str = "test-webhook-token",
    checker_pr_ci_watch_enabled: bool = True,
) -> Any:
    s = MagicMock()
    s.bkd_base_url = bkd_base_url
    s.bkd_frontend_url = bkd_frontend_url
    s.bkd_token = bkd_token
    s.github_token = github_token
    s.gh_incident_repo = gh_incident_repo
    s.gh_incident_labels = gh_incident_labels if gh_incident_labels is not None else ["sisyphus:incident"]
    s.max_auto_retries = max_auto_retries
    s.default_involved_repos = list(default_involved_repos or [])
    s.webhook_token = webhook_token
    s.checker_pr_ci_watch_enabled = checker_pr_ci_watch_enabled
    return s


def _setup_bkd_mock(mock_bkd_cls: Any, tags: list[str] | None = None) -> None:
    mock_bkd = AsyncMock()
    mock_bkd.get_issue = AsyncMock(return_value=MagicMock(tags=tags or []))
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_bkd)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_bkd_cls.return_value = mock_ctx


def _render_jinja2(template_name: str, **kwargs: Any) -> str:
    import jinja2
    prompts_dir = os.path.join(
        os.path.dirname(__file__), "..", "src", "orchestrator", "prompts"
    )
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(prompts_dir),
        undefined=jinja2.Undefined,
    )
    return env.get_template(template_name).render(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: links.bkd_issue_url — XLINK-S1..S4
# ─────────────────────────────────────────────────────────────────────────────


class TestBkdIssueUrl:
    """Spec: bkd_issue_url resolves a clickable BKD frontend URL from settings."""

    def test_xlink_s1_api_suffix_derives_frontend(self) -> None:
        """
        XLINK-S1: bkd_base_url='https://bkd.example/api', bkd_frontend_url='' →
        result == 'https://bkd.example/projects/p/issues/i'
        (the '/api' suffix is stripped to derive the frontend base).
        """
        import orchestrator.links as lm

        s = _make_settings(bkd_base_url="https://bkd.example/api", bkd_frontend_url="")
        with patch.object(lm, "settings", s):
            result = lm.bkd_issue_url("p", "i")

        assert result == "https://bkd.example/projects/p/issues/i", (
            f"XLINK-S1: expected 'https://bkd.example/projects/p/issues/i', got {result!r}"
        )

    def test_xlink_s2_explicit_frontend_override_beats_base_url(self) -> None:
        """
        XLINK-S2: bkd_frontend_url='https://bkd.example/' (trailing slash) →
        result strips the slash and uses bkd_frontend_url, ignoring bkd_base_url.
        """
        import orchestrator.links as lm

        s = _make_settings(
            bkd_base_url="https://api.bkd.example/api",
            bkd_frontend_url="https://bkd.example/",
        )
        with patch.object(lm, "settings", s):
            result = lm.bkd_issue_url("p", "i")

        assert result == "https://bkd.example/projects/p/issues/i", (
            f"XLINK-S2: expected 'https://bkd.example/projects/p/issues/i', got {result!r}"
        )

    def test_xlink_s3_empty_project_id_returns_none(self) -> None:
        """XLINK-S3: empty project_id → None (no partial URL)."""
        import orchestrator.links as lm

        s = _make_settings(bkd_base_url="https://bkd.example/api", bkd_frontend_url="")
        with patch.object(lm, "settings", s):
            result = lm.bkd_issue_url("", "i")

        assert result is None, (
            f"XLINK-S3: bkd_issue_url('', 'i') MUST be None; got {result!r}"
        )

    def test_xlink_s3_empty_issue_id_returns_none(self) -> None:
        """XLINK-S3: empty issue_id → None."""
        import orchestrator.links as lm

        s = _make_settings(bkd_base_url="https://bkd.example/api", bkd_frontend_url="")
        with patch.object(lm, "settings", s):
            result = lm.bkd_issue_url("p", "")

        assert result is None, (
            f"XLINK-S3: bkd_issue_url('p', '') MUST be None; got {result!r}"
        )

    def test_xlink_s4_unparseable_base_url_returns_none(self) -> None:
        """
        XLINK-S4: bkd_base_url='not-a-url', bkd_frontend_url='' →
        resolved frontend has no scheme → MUST return None.
        """
        import orchestrator.links as lm

        s = _make_settings(bkd_base_url="not-a-url", bkd_frontend_url="")
        with patch.object(lm, "settings", s):
            result = lm.bkd_issue_url("p", "i")

        assert result is None, (
            f"XLINK-S4: unparseable base URL MUST return None; got {result!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: links.format_pr_links_md — XLINK-S5..S6
# ─────────────────────────────────────────────────────────────────────────────


class TestFormatPrLinksMd:
    """Spec: format_pr_links_md returns sorted markdown bullet list or []."""

    def test_xlink_s5_multi_repo_produces_sorted_bullet_list(self) -> None:
        """
        XLINK-S5: two repos → sorted by repo key, PR# parsed from /pull/<n> path segment.
        """
        from orchestrator.links import format_pr_links_md

        pr_urls = {
            "foo/bar": "https://github.com/foo/bar/pull/9",
            "baz/qux": "https://github.com/baz/qux/pull/3",
        }
        result = format_pr_links_md(pr_urls)

        assert result == [
            "- [baz/qux#3](https://github.com/baz/qux/pull/3)",
            "- [foo/bar#9](https://github.com/foo/bar/pull/9)",
        ], f"XLINK-S5: got {result!r}"

    def test_xlink_s6_none_input_returns_empty_list(self) -> None:
        """XLINK-S6: None → []."""
        from orchestrator.links import format_pr_links_md

        assert format_pr_links_md(None) == [], "XLINK-S6: None must yield []"

    def test_xlink_s6_empty_dict_returns_empty_list(self) -> None:
        """XLINK-S6: {} → []."""
        from orchestrator.links import format_pr_links_md

        assert format_pr_links_md({}) == [], "XLINK-S6: empty dict must yield []"

    def test_xlink_s6_non_dict_string_returns_empty_list(self) -> None:
        """XLINK-S6: non-dict (string) → []."""
        from orchestrator.links import format_pr_links_md

        assert format_pr_links_md("not-a-dict") == [], (  # type: ignore[arg-type]
            "XLINK-S6: string input must yield []"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: webhook insert_init context — XLINK-S7..S8
# ─────────────────────────────────────────────────────────────────────────────


class TestWebhookInsertInitContext:
    """Spec: webhook includes/omits bkd_intent_url in insert_init context."""

    async def test_xlink_s7_fresh_req_context_includes_bkd_intent_url(self) -> None:
        """
        XLINK-S7: valid bkd_base_url → insert_init context['bkd_intent_url'] is
        set to '<frontend>/projects/<projectId>/issues/<issueId>'.
        Both 'intent_issue_id' and 'intent_title' must also still be present.
        """
        import orchestrator.router as router_mod
        import orchestrator.webhook as wh
        from orchestrator.state import Event

        captured: dict = {}

        async def _fake_insert_init(pool: Any, req_id: str, project_id: str,
                                     *, context: dict, state: Any = None) -> None:
            captured["context"] = dict(context)

        s = _make_settings(bkd_base_url="https://bkd.example/api", bkd_frontend_url="")

        with (
            patch.object(wh, "settings", s),
            patch("orchestrator.links.settings", s),
            patch.object(wh.db, "get_pool", return_value=MagicMock()),
            patch.object(wh.req_state, "get", new=AsyncMock(return_value=None)),
            patch.object(wh.req_state, "insert_init",
                         new=AsyncMock(side_effect=_fake_insert_init)),
            patch.object(wh.dedup, "check_and_record", new=AsyncMock(return_value="ok")),
            patch.object(wh.dedup, "mark_processed", new=AsyncMock()),
            patch.object(wh.obs, "record_event", new=AsyncMock()),
            patch.object(wh.engine, "step", new=AsyncMock()),
            patch.object(wh, "_push_upstream_status", new=AsyncMock()),
            patch.object(router_mod, "derive_event", return_value=Event.INTENT_ANALYZE),
            patch.object(router_mod, "extract_req_id", return_value="REQ-xlink-s7-test"),
            patch.object(wh, "BKDClient") as mock_bkd_cls,
        ):
            _setup_bkd_mock(mock_bkd_cls, tags=["intent:analyze"])

            from fastapi import FastAPI
            from starlette.testclient import TestClient

            app = FastAPI()
            app.include_router(wh.api)

            with TestClient(app, raise_server_exceptions=False) as client:
                client.post(
                    "/bkd-events",
                    headers={"Authorization": "Bearer test-webhook-token"},
                    json={
                        "event": "issue.updated",
                        "projectId": "P",
                        "issueId": "I",
                        "tags": ["intent:analyze"],
                        "title": "Test REQ for XLINK-S7",
                        "timestamp": "2026-01-01T00:00:00Z",
                    },
                )

        assert "context" in captured, (
            "XLINK-S7: insert_init was never called — fresh REQ branch not reached. "
            "Check that the webhook handler calls insert_init for new REQs."
        )
        ctx = captured["context"]
        assert "bkd_intent_url" in ctx, (
            f"XLINK-S7: insert_init context MUST contain 'bkd_intent_url'; "
            f"got keys: {sorted(ctx.keys())}"
        )
        assert ctx["bkd_intent_url"] == "https://bkd.example/projects/P/issues/I", (
            f"XLINK-S7: bkd_intent_url MUST equal "
            f"'https://bkd.example/projects/P/issues/I'; got {ctx['bkd_intent_url']!r}"
        )
        assert ctx.get("intent_issue_id") == "I", (
            f"XLINK-S7: intent_issue_id must still be present; "
            f"got {ctx.get('intent_issue_id')!r}"
        )

    async def test_xlink_s8_malformed_base_url_omits_bkd_intent_url(self) -> None:
        """
        XLINK-S8: bkd_base_url='not-a-url', bkd_frontend_url='' →
        bkd_issue_url returns None → insert_init context MUST NOT have 'bkd_intent_url'.
        """
        import orchestrator.router as router_mod
        import orchestrator.webhook as wh
        from orchestrator.state import Event

        captured: dict = {}

        async def _fake_insert_init(pool: Any, req_id: str, project_id: str,
                                     *, context: dict, state: Any = None) -> None:
            captured["context"] = dict(context)

        s = _make_settings(bkd_base_url="not-a-url", bkd_frontend_url="")

        with (
            patch.object(wh, "settings", s),
            patch("orchestrator.links.settings", s),
            patch.object(wh.db, "get_pool", return_value=MagicMock()),
            patch.object(wh.req_state, "get", new=AsyncMock(return_value=None)),
            patch.object(wh.req_state, "insert_init",
                         new=AsyncMock(side_effect=_fake_insert_init)),
            patch.object(wh.dedup, "check_and_record", new=AsyncMock(return_value="ok")),
            patch.object(wh.dedup, "mark_processed", new=AsyncMock()),
            patch.object(wh.obs, "record_event", new=AsyncMock()),
            patch.object(wh.engine, "step", new=AsyncMock()),
            patch.object(wh, "_push_upstream_status", new=AsyncMock()),
            patch.object(router_mod, "derive_event", return_value=Event.INTENT_ANALYZE),
            patch.object(router_mod, "extract_req_id", return_value="REQ-xlink-s8-test"),
            patch.object(wh, "BKDClient") as mock_bkd_cls,
        ):
            _setup_bkd_mock(mock_bkd_cls, tags=["intent:analyze"])

            from fastapi import FastAPI
            from starlette.testclient import TestClient

            app = FastAPI()
            app.include_router(wh.api)

            with TestClient(app, raise_server_exceptions=False) as client:
                client.post(
                    "/bkd-events",
                    headers={"Authorization": "Bearer test-webhook-token"},
                    json={
                        "event": "issue.updated",
                        "projectId": "P",
                        "issueId": "I",
                        "tags": ["intent:analyze"],
                        "title": "Test REQ for XLINK-S8",
                        "timestamp": "2026-01-01T00:00:01Z",
                    },
                )

        assert "context" in captured, (
            "XLINK-S8: insert_init was never called; cannot verify context absence."
        )
        ctx = captured["context"]
        assert "bkd_intent_url" not in ctx, (
            f"XLINK-S8: insert_init context MUST NOT contain 'bkd_intent_url' when "
            f"bkd_base_url is unparseable; got keys: {sorted(ctx.keys())}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: create_pr_ci_watch pr_urls persistence — XLINK-S9..S11
# ─────────────────────────────────────────────────────────────────────────────


class TestCreatePrCiWatchPrUrls:
    """Spec: create_pr_ci_watch persists pr_urls before dispatch; tolerates failures."""

    async def test_xlink_s9_successful_discovery_persists_and_enters_checker(
        self,
    ) -> None:
        """
        XLINK-S9: discover_pr_urls returns non-empty dict →
        update_context called with {"pr_urls": <dict>} AND checker dispatch entered once.
        """
        import orchestrator.actions.create_pr_ci_watch as mod

        mock_update_ctx = AsyncMock()
        mock_checker = AsyncMock(return_value={})

        s = _make_settings(checker_pr_ci_watch_enabled=True)

        with (
            patch.object(mod, "settings", s),
            patch.object(mod.db, "get_pool", return_value=MagicMock()),
            patch.object(mod.links, "discover_pr_urls",
                         new=AsyncMock(return_value={"foo/bar": "https://github.com/foo/bar/pull/9"})),
            patch.object(mod.req_state, "update_context", new=mock_update_ctx),
            patch.object(mod, "_run_checker", new=mock_checker),
            patch.object(mod, "_discover_repos_from_runner",
                         new=AsyncMock(return_value=["foo/bar"])),
        ):
            body = MagicMock()
            body.projectId = "P"
            await mod.create_pr_ci_watch(
                body=body,
                req_id="REQ-x",
                tags=[],
                ctx={"involved_repos": ["foo/bar"]},
            )

        # update_context MUST have been called with pr_urls
        assert mock_update_ctx.called, (
            "XLINK-S9: req_state.update_context MUST be called when discovery returns non-empty dict"
        )
        call_args = mock_update_ctx.call_args
        patch_arg = call_args[0][2] if call_args[0] else call_args[1].get("patch", {})
        assert "pr_urls" in patch_arg, (
            f"XLINK-S9: update_context patch MUST contain 'pr_urls'; got {patch_arg!r}"
        )
        assert patch_arg["pr_urls"] == {"foo/bar": "https://github.com/foo/bar/pull/9"}, (
            f"XLINK-S9: pr_urls value mismatch; got {patch_arg['pr_urls']!r}"
        )
        # checker dispatch MUST be entered
        assert mock_checker.called, (
            "XLINK-S9: _run_checker (checker dispatch) MUST be entered exactly once"
        )

    async def test_xlink_s10_empty_discovery_does_not_call_update_context(
        self,
    ) -> None:
        """
        XLINK-S10: discover_pr_urls returns {} →
        req_state.update_context MUST NOT be called with a pr_urls patch.
        """
        import orchestrator.actions.create_pr_ci_watch as mod

        mock_update_ctx = AsyncMock()
        s = _make_settings(checker_pr_ci_watch_enabled=True)

        with (
            patch.object(mod, "settings", s),
            patch.object(mod.links, "discover_pr_urls",
                         new=AsyncMock(return_value={})),
            patch.object(mod.req_state, "update_context", new=mock_update_ctx),
            patch.object(mod, "_run_checker", new=AsyncMock(return_value={})),
            patch.object(mod, "_discover_repos_from_runner",
                         new=AsyncMock(return_value=["foo/bar"])),
        ):
            body = MagicMock()
            body.projectId = "P"
            await mod.create_pr_ci_watch(
                body=body,
                req_id="REQ-x",
                tags=[],
                ctx={"involved_repos": ["foo/bar"]},
            )

        # Check update_context was not called with pr_urls
        for c in mock_update_ctx.call_args_list:
            patch_arg = c[0][2] if c[0] else c[1].get("patch", {})
            assert "pr_urls" not in patch_arg, (
                f"XLINK-S10: update_context MUST NOT be called with pr_urls when "
                f"discovery returns empty; got call with {patch_arg!r}"
            )

    async def test_xlink_s11_discovery_exception_does_not_abort_dispatch(
        self,
    ) -> None:
        """
        XLINK-S11: discover_pr_urls raises httpx.HTTPError →
        exception MUST NOT propagate AND checker dispatch MUST still be entered.
        """
        import httpx
        import orchestrator.actions.create_pr_ci_watch as mod

        mock_checker = AsyncMock(return_value={})
        s = _make_settings(checker_pr_ci_watch_enabled=True)

        with (
            patch.object(mod, "settings", s),
            patch.object(mod.links, "discover_pr_urls",
                         new=AsyncMock(side_effect=httpx.HTTPError("network error"))),
            patch.object(mod.req_state, "update_context", new=AsyncMock()),
            patch.object(mod, "_run_checker", new=mock_checker),
            patch.object(mod, "_discover_repos_from_runner",
                         new=AsyncMock(return_value=["foo/bar"])),
        ):
            body = MagicMock()
            body.projectId = "P"
            try:
                await mod.create_pr_ci_watch(
                    body=body,
                    req_id="REQ-x",
                    tags=[],
                    ctx={"involved_repos": ["foo/bar"]},
                )
            except Exception as exc:
                pytest.fail(
                    f"XLINK-S11: create_pr_ci_watch MUST NOT propagate "
                    f"discover_pr_urls exception; got {type(exc).__name__}: {exc}"
                )

        assert mock_checker.called, (
            "XLINK-S11: _run_checker MUST still be entered when discover_pr_urls raises"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 5: gh_incident body cross-links — XLINK-S12..S14
# ─────────────────────────────────────────────────────────────────────────────


class TestGhIncidentBody:
    """Spec: open_incident body embeds BKD intent link and PR links when provided."""

    async def test_xlink_s12_body_contains_bkd_intent_markdown_link(
        self, httpx_mock: Any
    ) -> None:
        """
        XLINK-S12: bkd_intent_url provided →
        POST body contains '[BKD intent issue](<bkd_intent_url>)' AND raw intent_issue_id.
        """
        import orchestrator.gh_incident as ghi

        httpx_mock.add_response(
            method="POST",
            url="https://api.github.com/repos/phona/sisyphus/issues",
            json={"html_url": "https://github.com/phona/sisyphus/issues/99"},
            status_code=201,
        )
        s = _make_settings()
        with patch.object(ghi, "settings", s):
            await ghi.open_incident(
                repo="phona/sisyphus",
                req_id="REQ-xlink-s12",
                reason="test-reason",
                retry_count=0,
                intent_issue_id="i-1",
                failed_issue_id="f-1",
                project_id="P",
                bkd_intent_url="https://bkd.example/projects/p/issues/i-1",
            )

        request = httpx_mock.get_request()
        assert request is not None, "XLINK-S12: POST must have been sent"
        body_str = json.dumps(json.loads(request.content))

        assert "[BKD intent issue](https://bkd.example/projects/p/issues/i-1)" in body_str, (
            f"XLINK-S12: body MUST contain markdown link to bkd_intent_url; "
            f"body: {body_str!r}"
        )
        assert "i-1" in body_str, (
            f"XLINK-S12: raw intent_issue_id 'i-1' MUST still be present "
            f"(GHI-S4 contract preserved); body: {body_str!r}"
        )

    async def test_xlink_s13_body_contains_pr_markdown_links(
        self, httpx_mock: Any
    ) -> None:
        """
        XLINK-S13: pr_urls provided →
        POST body contains '**PRs**:' followed by markdown link for each repo.
        """
        import orchestrator.gh_incident as ghi

        httpx_mock.add_response(
            method="POST",
            url="https://api.github.com/repos/phona/sisyphus/issues",
            json={"html_url": "https://github.com/phona/sisyphus/issues/100"},
            status_code=201,
        )
        s = _make_settings()
        with patch.object(ghi, "settings", s):
            await ghi.open_incident(
                repo="phona/sisyphus",
                req_id="REQ-xlink-s13",
                reason="test-reason",
                retry_count=0,
                intent_issue_id="i-2",
                failed_issue_id="f-2",
                project_id="P",
                pr_urls={"foo/bar": "https://github.com/foo/bar/pull/9"},
            )

        request = httpx_mock.get_request()
        assert request is not None, "XLINK-S13: POST must have been sent"
        body_str = json.dumps(json.loads(request.content))

        assert "**PRs**:" in body_str, (
            f"XLINK-S13: body MUST contain '**PRs**:' when pr_urls provided; "
            f"body: {body_str!r}"
        )
        assert "[foo/bar#9](https://github.com/foo/bar/pull/9)" in body_str, (
            f"XLINK-S13: body MUST contain markdown link for foo/bar PR #9; "
            f"body: {body_str!r}"
        )

    async def test_xlink_s14_none_pr_urls_omits_prs_section(
        self, httpx_mock: Any
    ) -> None:
        """
        XLINK-S14 (pr_urls=None): POST body MUST NOT contain '**PRs**:'.
        """
        import orchestrator.gh_incident as ghi

        httpx_mock.add_response(
            method="POST",
            url="https://api.github.com/repos/phona/sisyphus/issues",
            json={"html_url": "https://github.com/phona/sisyphus/issues/101"},
            status_code=201,
        )
        s = _make_settings()
        with patch.object(ghi, "settings", s):
            await ghi.open_incident(
                repo="phona/sisyphus",
                req_id="REQ-xlink-s14a",
                reason="no-prs",
                retry_count=0,
                intent_issue_id="i-3",
                failed_issue_id="f-3",
                project_id="P",
                pr_urls=None,
            )

        body_str = json.dumps(json.loads(httpx_mock.get_request().content))
        assert "**PRs**:" not in body_str, (
            f"XLINK-S14: body MUST NOT contain '**PRs**:' when pr_urls=None; body: {body_str!r}"
        )

    async def test_xlink_s14_empty_pr_urls_omits_prs_section(
        self, httpx_mock: Any
    ) -> None:
        """
        XLINK-S14 (pr_urls={}): POST body MUST NOT contain '**PRs**:'.
        """
        import orchestrator.gh_incident as ghi

        httpx_mock.add_response(
            method="POST",
            url="https://api.github.com/repos/phona/sisyphus/issues",
            json={"html_url": "https://github.com/phona/sisyphus/issues/102"},
            status_code=201,
        )
        s = _make_settings()
        with patch.object(ghi, "settings", s):
            await ghi.open_incident(
                repo="phona/sisyphus",
                req_id="REQ-xlink-s14b",
                reason="no-prs",
                retry_count=0,
                intent_issue_id="i-4",
                failed_issue_id="f-4",
                project_id="P",
                pr_urls={},
            )

        body_str = json.dumps(json.loads(httpx_mock.get_request().content))
        assert "**PRs**:" not in body_str, (
            f"XLINK-S14: body MUST NOT contain '**PRs**:' when pr_urls={{}}; body: {body_str!r}"
        )

    async def test_xlink_s14_omitted_pr_urls_kwarg_omits_prs_section(
        self, httpx_mock: Any
    ) -> None:
        """
        XLINK-S14 (pr_urls kwarg omitted): POST body MUST NOT contain '**PRs**:'.
        """
        import orchestrator.gh_incident as ghi

        httpx_mock.add_response(
            method="POST",
            url="https://api.github.com/repos/phona/sisyphus/issues",
            json={"html_url": "https://github.com/phona/sisyphus/issues/103"},
            status_code=201,
        )
        s = _make_settings()
        with patch.object(ghi, "settings", s):
            await ghi.open_incident(
                repo="phona/sisyphus",
                req_id="REQ-xlink-s14c",
                reason="no-prs",
                retry_count=0,
                intent_issue_id="i-5",
                failed_issue_id="f-5",
                project_id="P",
                # pr_urls intentionally omitted
            )

        body_str = json.dumps(json.loads(httpx_mock.get_request().content))
        assert "**PRs**:" not in body_str, (
            f"XLINK-S14: body MUST NOT contain '**PRs**:' when pr_urls kwarg omitted; "
            f"body: {body_str!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 6: escalate ctx forwarding — XLINK-S15
# ─────────────────────────────────────────────────────────────────────────────


class TestEscalateCtxForwarding:
    """Spec: escalate threads bkd_intent_url and pr_urls from ctx to open_incident."""

    async def test_xlink_s15_escalate_forwards_ctx_fields_to_open_incident(
        self,
    ) -> None:
        """
        XLINK-S15: ctx has 'bkd_intent_url' and 'pr_urls' →
        open_incident MUST be called with those exact kwargs for involved repo.
        """
        import orchestrator.actions.escalate as esc_mod
        import orchestrator.gh_incident as ghi

        captured_calls: list[dict] = []

        async def _fake_open_incident(**kwargs: Any) -> str:
            captured_calls.append(kwargs)
            return "https://github.com/foo/bar/issues/1"

        s = _make_settings(
            gh_incident_repo="foo/bar",
            github_token="ghp_test",
        )

        mock_row = MagicMock()
        mock_row.state = "dev-running"

        with (
            patch.object(esc_mod, "settings", s),
            patch.object(ghi, "settings", s),
            patch.object(ghi, "open_incident", new=_fake_open_incident),
            patch.object(esc_mod.db, "get_pool", return_value=MagicMock()),
            patch.object(esc_mod.req_state, "get", new=AsyncMock(return_value=mock_row)),
            patch.object(esc_mod.req_state, "update_context", new=AsyncMock()),
            patch.object(esc_mod.req_state, "cas_transition", new=AsyncMock(return_value=mock_row)),
            patch.object(esc_mod, "_all_prs_merged_for_req",
                         new=AsyncMock(return_value=False)),
        ):
            body = MagicMock()
            body.event = "session.completed"
            body.projectId = "P"
            body.issueId = "fail-issue"

            await esc_mod.escalate(
                body=body,
                req_id="REQ-xlink-s15",
                tags=[],
                ctx={
                    "involved_repos": ["foo/bar"],
                    "bkd_intent_url": "https://bkd.example/projects/p/issues/i",
                    "pr_urls": {"foo/bar": "https://github.com/foo/bar/pull/9"},
                },
            )

        assert len(captured_calls) >= 1, (
            "XLINK-S15: open_incident MUST be called at least once for involved repo"
        )
        # Find call for repo=foo/bar
        foo_bar_calls = [c for c in captured_calls if c.get("repo") == "foo/bar"]
        assert foo_bar_calls, (
            f"XLINK-S15: no open_incident call for repo='foo/bar'; "
            f"calls: {captured_calls!r}"
        )
        call_kw = foo_bar_calls[0]
        assert call_kw.get("bkd_intent_url") == "https://bkd.example/projects/p/issues/i", (
            f"XLINK-S15: bkd_intent_url not forwarded correctly; got {call_kw.get('bkd_intent_url')!r}"
        )
        assert call_kw.get("pr_urls") == {"foo/bar": "https://github.com/foo/bar/pull/9"}, (
            f"XLINK-S15: pr_urls not forwarded correctly; got {call_kw.get('pr_urls')!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 7: analyze prompt cross-link block — XLINK-S16..S17
# ─────────────────────────────────────────────────────────────────────────────


class TestAnalyzePromptCrossLink:
    """Spec: analyze.md.j2 renders sisyphus:cross-link block conditionally."""

    _BASE_VARS = dict(
        req_id="REQ-x",
        project_id="P",
        project_alias="P",
        issue_id="I",
        aissh_server_id="X",
        cloned_repos=[],
    )

    def test_xlink_s16_renders_cross_link_block_when_url_provided(self) -> None:
        """
        XLINK-S16: bkd_intent_issue_url='https://...' →
        output contains '<!-- sisyphus:cross-link -->' AND
        '[BKD intent issue](https://bkd.example/projects/P/issues/I)'.
        """
        result = _render_jinja2(
            "analyze.md.j2",
            **self._BASE_VARS,
            bkd_intent_issue_url="https://bkd.example/projects/P/issues/I",
        )

        assert "<!-- sisyphus:cross-link -->" in result, (
            "XLINK-S16: output MUST contain '<!-- sisyphus:cross-link -->'"
        )
        assert "[BKD intent issue](https://bkd.example/projects/P/issues/I)" in result, (
            "XLINK-S16: output MUST contain markdown link to BKD intent issue URL"
        )

    def test_xlink_s17_omits_bkd_link_when_url_empty(self) -> None:
        """
        XLINK-S17: bkd_intent_issue_url='' →
        output MUST still contain '<!-- sisyphus:cross-link -->' and REQ id,
        but MUST NOT contain '[BKD intent issue]('.
        """
        result = _render_jinja2(
            "analyze.md.j2",
            **self._BASE_VARS,
            bkd_intent_issue_url="",
        )

        assert "<!-- sisyphus:cross-link -->" in result, (
            "XLINK-S17: '<!-- sisyphus:cross-link -->' MUST still be present"
        )
        assert "REQ-x" in result, (
            "XLINK-S17: REQ id 'REQ-x' MUST still be present"
        )
        assert "[BKD intent issue](" not in result, (
            "XLINK-S17: '[BKD intent issue](' MUST NOT appear when bkd_intent_issue_url is empty"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 8: done_archive prompt pr_urls rendering — XLINK-S18..S19
# ─────────────────────────────────────────────────────────────────────────────


class TestDoneArchivePromptPrUrls:
    """Spec: done_archive.md.j2 renders Known PRs section conditionally."""

    _BASE_VARS = dict(
        req_id="REQ-x",
        accept_issue_id="accept-1",
        project_id="P",
        project_alias="P",
        issue_id="I",
        intent_issue_id="intent-1",
    )

    def test_xlink_s18_renders_known_prs_when_pr_urls_present(self) -> None:
        """
        XLINK-S18: pr_urls={'foo/bar': 'https://github.com/foo/bar/pull/9'} →
        output contains '## Known PRs' AND
        '- [foo/bar#9](https://github.com/foo/bar/pull/9)'.
        """
        result = _render_jinja2(
            "done_archive.md.j2",
            **self._BASE_VARS,
            pr_urls={"foo/bar": "https://github.com/foo/bar/pull/9"},
        )

        assert "## Known PRs" in result, (
            "XLINK-S18: output MUST contain '## Known PRs' when pr_urls is non-empty"
        )
        assert "- [foo/bar#9](https://github.com/foo/bar/pull/9)" in result, (
            "XLINK-S18: output MUST contain markdown bullet for foo/bar#9"
        )

    def test_xlink_s19_omits_known_prs_when_pr_urls_absent(self) -> None:
        """
        XLINK-S19: pr_urls absent / empty →
        output MUST NOT contain '## Known PRs' (no orphan heading).
        """
        for pr_urls_val in ({}, None, "absent"):
            kwargs = dict(**self._BASE_VARS)
            if pr_urls_val != "absent":
                kwargs["pr_urls"] = pr_urls_val  # type: ignore[assignment]
            result = _render_jinja2("done_archive.md.j2", **kwargs)

            assert "## Known PRs" not in result, (
                f"XLINK-S19: '## Known PRs' MUST NOT appear when pr_urls={pr_urls_val!r}; "
                f"found in output"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Part 9: Metabase SQL columns — XLINK-S20..S21 (integration)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestActiveReqOverviewSqlColumns:
    """
    Spec: 05-active-req-overview.sql selects bkd_intent_url and pr_urls_md.

    Requires live PostgreSQL via SISYPHUS_PG_DSN (run with make ci-integration-test).
    """

    _SQL_PATH = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "observability",
        "queries",
        "sisyphus",
        "05-active-req-overview.sql",
    )

    async def _get_pool(self) -> Any:
        import asyncpg
        dsn = os.environ.get("SISYPHUS_PG_DSN", "postgresql://test:test@localhost/test")
        return await asyncpg.create_pool(dsn)

    async def test_xlink_s20_query_returns_new_columns(self) -> None:
        """
        XLINK-S20: row with context containing bkd_intent_url and pr_urls jsonb →
        query result has bkd_intent_url and pr_urls_md with expected values.
        """
        import asyncpg

        sql = open(self._SQL_PATH).read()
        test_req_id = "REQ-xlink-s20-sql-test"
        test_context = json.dumps({
            "intent_issue_id": "I",
            "bkd_intent_url": "https://bkd.example/projects/P/issues/I",
            "pr_urls": {"foo/bar": "https://github.com/foo/bar/pull/9"},
        })

        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO req_state (req_id, project_id, state, context, updated_at)
                    VALUES ($1, 'P', 'analyzing', $2::jsonb, now())
                    ON CONFLICT (req_id) DO UPDATE
                      SET state='analyzing', context=EXCLUDED.context, updated_at=now()
                    """,
                    test_req_id, test_context,
                )

                rows = await conn.fetch(sql)
                row = next((r for r in rows if r["req_id"] == test_req_id), None)

                assert row is not None, (
                    f"XLINK-S20: query must return the test row for req_id={test_req_id!r}"
                )
                assert row["bkd_intent_url"] == "https://bkd.example/projects/P/issues/I", (
                    f"XLINK-S20: bkd_intent_url must equal the stored URL; "
                    f"got {row['bkd_intent_url']!r}"
                )
                assert row["pr_urls_md"] is not None, (
                    "XLINK-S20: pr_urls_md MUST NOT be NULL when pr_urls contains entries"
                )
                assert "[foo/bar#9](https://github.com/foo/bar/pull/9)" in row["pr_urls_md"], (
                    f"XLINK-S20: pr_urls_md MUST contain markdown link for foo/bar#9; "
                    f"got {row['pr_urls_md']!r}"
                )
        finally:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM req_state WHERE req_id = $1", test_req_id
                )
            await pool.close()

    async def test_xlink_s21_query_tolerates_missing_context_fields(self) -> None:
        """
        XLINK-S21: row with empty context {} →
        bkd_intent_url is NULL AND pr_urls_md is NULL (or empty string).
        """
        sql = open(self._SQL_PATH).read()
        test_req_id = "REQ-xlink-s21-sql-test"

        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO req_state (req_id, project_id, state, context, updated_at)
                    VALUES ($1, 'P', 'analyzing', '{}'::jsonb, now())
                    ON CONFLICT (req_id) DO UPDATE
                      SET state='analyzing', context=EXCLUDED.context, updated_at=now()
                    """,
                    test_req_id,
                )

                rows = await conn.fetch(sql)
                row = next((r for r in rows if r["req_id"] == test_req_id), None)

                assert row is not None, (
                    f"XLINK-S21: query must return the test row for req_id={test_req_id!r}"
                )
                assert row["bkd_intent_url"] is None, (
                    f"XLINK-S21: bkd_intent_url MUST be NULL when context is empty; "
                    f"got {row['bkd_intent_url']!r}"
                )
                # pr_urls_md NULL or empty string
                assert not row["pr_urls_md"], (
                    f"XLINK-S21: pr_urls_md MUST be NULL or empty when pr_urls absent; "
                    f"got {row['pr_urls_md']!r}"
                )
        finally:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM req_state WHERE req_id = $1", test_req_id
                )
            await pool.close()
