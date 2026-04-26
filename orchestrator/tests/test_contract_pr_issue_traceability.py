"""Contract tests for REQ-pr-issue-traceability-1777218612 (cross-link).

Black-box scenarios derived from
``openspec/changes/REQ-pr-issue-traceability-1777218612/specs/cross-link/spec.md``:

  XLINK-S7   webhook insert_init context contains bkd_intent_url
  XLINK-S8   helper returning None omits the field
  XLINK-S9   create_pr_ci_watch persists pr_urls dict
  XLINK-S10  empty discovery does not call update_context with pr_urls
  XLINK-S11  discovery exception does not abort dispatch
  XLINK-S12  gh_incident body contains markdown link to BKD intent
  XLINK-S13  gh_incident body contains PR markdown links
  XLINK-S14  absent pr_urls kwargs do not add PR section
  XLINK-S15  escalate threads ctx fields through to open_incident
  XLINK-S16  analyze prompt renders cross-link block when url provided
  XLINK-S17  analyze prompt omits link line when url empty
  XLINK-S18  done_archive prompt renders Known PRs bullets
  XLINK-S19  done_archive prompt omits heading when pr_urls absent
  XLINK-S20  q05 SQL selects the new bkd_intent_url + pr_urls_md columns
  XLINK-S21  q05 SQL tolerates empty context

Dev MUST NOT change these tests to make them pass — fix the implementation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# XLINK-S7 / S8 — webhook persists bkd_intent_url
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xlink_s7_webhook_insert_init_context_includes_bkd_intent_url(monkeypatch):
    """Drive webhook end-to-end: fresh REQ → insert_init gets bkd_intent_url."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orchestrator import webhook

    monkeypatch.setattr(webhook.links.settings, "bkd_base_url",
                        "https://bkd.example/api", raising=False)
    monkeypatch.setattr(webhook.links.settings, "bkd_frontend_url", "", raising=False)
    monkeypatch.setattr(webhook.settings, "webhook_token", "tok", raising=False)

    insert_calls: list[dict[str, Any]] = []

    async def fake_insert_init(pool, req_id, project_id, *, context, state=None):
        insert_calls.append({"req_id": req_id, "context": dict(context), "state": state})

    monkeypatch.setattr(webhook.db, "get_pool", lambda: MagicMock())
    fresh_row = MagicMock()
    fresh_row.state = MagicMock(value="init")
    fresh_row.context = {}
    monkeypatch.setattr(webhook.req_state, "get",
                        AsyncMock(side_effect=[None, fresh_row]))
    monkeypatch.setattr(webhook.req_state, "insert_init", fake_insert_init)
    monkeypatch.setattr(webhook.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(webhook.dedup, "check_and_record",
                        AsyncMock(return_value="new"))
    monkeypatch.setattr(webhook.dedup, "mark_processed", AsyncMock())
    monkeypatch.setattr(webhook.obs, "record_event", AsyncMock())
    monkeypatch.setattr(webhook.engine, "step",
                        AsyncMock(return_value={"action": "noop"}))

    bkd_inner = MagicMock()
    bkd_inner.get_issue = AsyncMock(return_value=MagicMock(tags=["intent:analyze", "REQ-xy"]))
    bkd_ctx = AsyncMock()
    bkd_ctx.__aenter__ = AsyncMock(return_value=bkd_inner)
    bkd_ctx.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(webhook, "BKDClient", lambda *a, **kw: bkd_ctx)

    app = FastAPI()
    app.include_router(webhook.api)
    client = TestClient(app)

    resp = client.post(
        "/bkd-events",
        json={
            "event": "issue.updated",
            "issueId": "I",
            "projectId": "P",
            "title": "feat: example",
            "tags": ["intent:analyze", "REQ-xy"],
        },
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200, resp.text
    assert insert_calls, f"insert_init must be called for fresh REQ; got: {insert_calls}"
    ctx = insert_calls[0]["context"]
    assert ctx.get("bkd_intent_url") == "https://bkd.example/projects/P/issues/I", ctx
    assert ctx.get("intent_issue_id") == "I"
    assert ctx.get("intent_title") == "feat: example"


@pytest.mark.asyncio
async def test_xlink_s8_unparseable_base_omits_bkd_intent_url(monkeypatch):
    """End-to-end: malformed bkd_base_url → field absent from insert_init context."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from orchestrator import webhook

    monkeypatch.setattr(webhook.links.settings, "bkd_base_url", "not-a-url", raising=False)
    monkeypatch.setattr(webhook.links.settings, "bkd_frontend_url", "", raising=False)
    monkeypatch.setattr(webhook.settings, "webhook_token", "tok", raising=False)

    insert_calls: list[dict[str, Any]] = []

    async def fake_insert_init(pool, req_id, project_id, *, context, state=None):
        insert_calls.append({"req_id": req_id, "context": dict(context), "state": state})

    monkeypatch.setattr(webhook.db, "get_pool", lambda: MagicMock())
    fresh_row = MagicMock()
    fresh_row.state = MagicMock(value="init")
    fresh_row.context = {}
    monkeypatch.setattr(webhook.req_state, "get",
                        AsyncMock(side_effect=[None, fresh_row]))
    monkeypatch.setattr(webhook.req_state, "insert_init", fake_insert_init)
    monkeypatch.setattr(webhook.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(webhook.dedup, "check_and_record",
                        AsyncMock(return_value="new"))
    monkeypatch.setattr(webhook.dedup, "mark_processed", AsyncMock())
    monkeypatch.setattr(webhook.obs, "record_event", AsyncMock())
    monkeypatch.setattr(webhook.engine, "step",
                        AsyncMock(return_value={"action": "noop"}))
    bkd_inner = MagicMock()
    bkd_inner.get_issue = AsyncMock(return_value=MagicMock(tags=["intent:analyze", "REQ-xy"]))
    bkd_ctx = AsyncMock()
    bkd_ctx.__aenter__ = AsyncMock(return_value=bkd_inner)
    bkd_ctx.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(webhook, "BKDClient", lambda *a, **kw: bkd_ctx)

    app = FastAPI()
    app.include_router(webhook.api)
    client = TestClient(app)
    resp = client.post(
        "/bkd-events",
        json={
            "event": "issue.updated",
            "issueId": "I",
            "projectId": "P",
            "title": "feat: example",
            "tags": ["intent:analyze", "REQ-xy"],
        },
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200, resp.text
    assert insert_calls
    assert "bkd_intent_url" not in insert_calls[0]["context"]


# ─────────────────────────────────────────────────────────────────────────────
# XLINK-S9 / S10 / S11 — create_pr_ci_watch persists pr_urls
# ─────────────────────────────────────────────────────────────────────────────


def _patch_create_pr_ci_watch(monkeypatch, *, discover_return=None, discover_exc=None):
    """Patch the side-effects of create_pr_ci_watch and return capture lists."""
    from orchestrator.actions import create_pr_ci_watch as action

    update_ctx_calls: list[dict[str, Any]] = []
    run_checker_calls: list[dict[str, Any]] = []
    dispatch_bkd_calls: list[dict[str, Any]] = []

    async def fake_update_context(pool, req_id, patch):
        update_ctx_calls.append({"req_id": req_id, "patch": dict(patch)})

    async def fake_discover(repos, branch, *, timeout_sec=15.0):
        if discover_exc is not None:
            raise discover_exc
        return discover_return or {}

    async def fake_run_checker(*, req_id, ctx):
        run_checker_calls.append({"req_id": req_id, "ctx": dict(ctx)})
        return {"emit": "noop"}

    async def fake_dispatch(*, body, req_id, ctx):
        dispatch_bkd_calls.append({"req_id": req_id, "ctx": dict(ctx)})
        return {"emit": "noop"}

    async def fake_discover_repos_from_runner(req_id):
        return ["foo/bar"]

    def fake_skip(stage, ev, *, req_id):  # skip_if_enabled is sync
        return None

    monkeypatch.setattr(action.req_state, "update_context", fake_update_context)
    monkeypatch.setattr(action.links, "discover_pr_urls", fake_discover)
    monkeypatch.setattr(action, "_run_checker", fake_run_checker)
    monkeypatch.setattr(action, "_dispatch_bkd_agent", fake_dispatch)
    monkeypatch.setattr(action, "_discover_repos_from_runner", fake_discover_repos_from_runner)
    monkeypatch.setattr(action, "skip_if_enabled", fake_skip)
    monkeypatch.setattr(action.db, "get_pool", lambda: MagicMock())
    return action, update_ctx_calls, run_checker_calls, dispatch_bkd_calls


@pytest.mark.asyncio
async def test_xlink_s9_create_pr_ci_watch_persists_pr_urls_then_runs_checker(monkeypatch):
    """checker_pr_ci_watch_enabled=True → discover_pr_urls result lands in ctx; checker dispatched."""
    pr_urls = {"foo/bar": "https://github.com/foo/bar/pull/9"}
    action, update_calls, run_checker_calls, dispatch_calls = _patch_create_pr_ci_watch(
        monkeypatch, discover_return=pr_urls,
    )
    monkeypatch.setattr(action.settings, "checker_pr_ci_watch_enabled", True, raising=False)

    body = MagicMock(projectId="P", issueId="parent")
    out = await action.create_pr_ci_watch(body=body, req_id="REQ-x", tags=[], ctx={})

    assert any(c["patch"].get("pr_urls") == pr_urls for c in update_calls), (
        f"update_context MUST receive pr_urls patch; got: {update_calls!r}"
    )
    assert len(run_checker_calls) == 1, run_checker_calls
    assert not dispatch_calls
    assert out == {"emit": "noop"}


@pytest.mark.asyncio
async def test_xlink_s9b_dispatch_path_also_persists_pr_urls(monkeypatch):
    """checker flag off → BKD-agent dispatch path also benefits from pr_urls capture."""
    pr_urls = {"foo/bar": "https://github.com/foo/bar/pull/9"}
    action, update_calls, run_checker_calls, dispatch_calls = _patch_create_pr_ci_watch(
        monkeypatch, discover_return=pr_urls,
    )
    monkeypatch.setattr(action.settings, "checker_pr_ci_watch_enabled", False, raising=False)

    body = MagicMock(projectId="P", issueId="parent")
    await action.create_pr_ci_watch(body=body, req_id="REQ-x", tags=[], ctx={})

    assert any(c["patch"].get("pr_urls") == pr_urls for c in update_calls)
    assert not run_checker_calls
    assert len(dispatch_calls) == 1


@pytest.mark.asyncio
async def test_xlink_s10_empty_discovery_does_not_persist_pr_urls(monkeypatch):
    action, update_calls, run_checker_calls, _ = _patch_create_pr_ci_watch(
        monkeypatch, discover_return={},
    )
    monkeypatch.setattr(action.settings, "checker_pr_ci_watch_enabled", True, raising=False)

    body = MagicMock(projectId="P", issueId="parent")
    await action.create_pr_ci_watch(body=body, req_id="REQ-x", tags=[], ctx={})

    pr_url_patches = [c for c in update_calls if "pr_urls" in c["patch"]]
    assert pr_url_patches == [], f"Empty discovery MUST NOT persist pr_urls; got: {pr_url_patches!r}"
    assert len(run_checker_calls) == 1


@pytest.mark.asyncio
async def test_xlink_s11_discovery_exception_does_not_abort_dispatch(monkeypatch):
    import httpx
    action, update_calls, run_checker_calls, _ = _patch_create_pr_ci_watch(
        monkeypatch, discover_exc=httpx.ReadTimeout("slow"),
    )
    monkeypatch.setattr(action.settings, "checker_pr_ci_watch_enabled", True, raising=False)

    body = MagicMock(projectId="P", issueId="parent")
    out = await action.create_pr_ci_watch(body=body, req_id="REQ-x", tags=[], ctx={})

    pr_url_patches = [c for c in update_calls if "pr_urls" in c["patch"]]
    assert pr_url_patches == []
    assert len(run_checker_calls) == 1, "Dispatch MUST still proceed after discovery error"
    assert out == {"emit": "noop"}


# ─────────────────────────────────────────────────────────────────────────────
# XLINK-S12 / S13 / S14 — gh_incident body content
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xlink_s12_body_contains_markdown_link_to_bkd_intent(httpx_mock, monkeypatch):
    from orchestrator import gh_incident as ghi

    s = MagicMock()
    s.github_token = "ghp"
    s.gh_incident_labels = ["sisyphus:incident"]
    s.bkd_base_url = "https://bkd.example/api"
    s.bkd_frontend_url = ""
    monkeypatch.setattr(ghi, "settings", s)
    monkeypatch.setattr(ghi.links, "settings", s, raising=False)

    httpx_mock.add_response(
        method="POST",
        url="https://api.github.com/repos/phona/sisyphus/issues",
        json={"html_url": "https://github.com/phona/sisyphus/issues/1"},
        status_code=201,
    )
    await ghi.open_incident(
        repo="phona/sisyphus",
        req_id="REQ-x",
        reason="r",
        retry_count=0,
        intent_issue_id="i-1",
        failed_issue_id="f-1",
        project_id="p",
        bkd_intent_url="https://bkd.example/projects/p/issues/i-1",
    )

    request = httpx_mock.get_request()
    body = json.loads(request.content)["body"]
    assert "[BKD intent issue](https://bkd.example/projects/p/issues/i-1)" in body
    assert "i-1" in body  # legacy raw id preserved (GHI-S4 contract)


@pytest.mark.asyncio
async def test_xlink_s13_body_contains_pr_markdown_links(httpx_mock, monkeypatch):
    from orchestrator import gh_incident as ghi

    s = MagicMock()
    s.github_token = "ghp"
    s.gh_incident_labels = ["sisyphus:incident"]
    s.bkd_base_url = "https://bkd.example/api"
    s.bkd_frontend_url = ""
    monkeypatch.setattr(ghi, "settings", s)
    monkeypatch.setattr(ghi.links, "settings", s, raising=False)

    httpx_mock.add_response(
        method="POST",
        url="https://api.github.com/repos/phona/sisyphus/issues",
        json={"html_url": "https://github.com/phona/sisyphus/issues/2"},
        status_code=201,
    )
    await ghi.open_incident(
        repo="phona/sisyphus",
        req_id="REQ-x",
        reason="r",
        retry_count=0,
        intent_issue_id="i-1",
        failed_issue_id="f-1",
        project_id="p",
        pr_urls={"foo/bar": "https://github.com/foo/bar/pull/9"},
    )

    request = httpx_mock.get_request()
    body = json.loads(request.content)["body"]
    assert "**PRs**:" in body
    assert "[foo/bar#9](https://github.com/foo/bar/pull/9)" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("pr_urls", [None, {}])
async def test_xlink_s14_absent_pr_urls_omits_pr_section(httpx_mock, monkeypatch, pr_urls):
    from orchestrator import gh_incident as ghi

    s = MagicMock()
    s.github_token = "ghp"
    s.gh_incident_labels = ["sisyphus:incident"]
    s.bkd_base_url = "https://bkd.example/api"
    s.bkd_frontend_url = ""
    monkeypatch.setattr(ghi, "settings", s)
    monkeypatch.setattr(ghi.links, "settings", s, raising=False)

    httpx_mock.add_response(
        method="POST",
        url="https://api.github.com/repos/phona/sisyphus/issues",
        json={"html_url": "https://github.com/phona/sisyphus/issues/3"},
        status_code=201,
    )
    await ghi.open_incident(
        repo="phona/sisyphus",
        req_id="REQ-x",
        reason="r",
        retry_count=0,
        intent_issue_id="i-1",
        failed_issue_id="f-1",
        project_id="p",
        pr_urls=pr_urls,
    )

    request = httpx_mock.get_request()
    body = json.loads(request.content)["body"]
    assert "**PRs**:" not in body, f"empty pr_urls must not add PR section; body: {body!r}"


# ─────────────────────────────────────────────────────────────────────────────
# XLINK-S15 — escalate forwards bkd_intent_url + pr_urls
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xlink_s15_escalate_threads_ctx_fields_to_open_incident(monkeypatch):
    from orchestrator.actions.escalate import escalate

    open_incident_calls: list[dict[str, Any]] = []

    async def fake_open(**kwargs):
        open_incident_calls.append(dict(kwargs))
        return "https://github.com/foo/bar/issues/99"

    mock_gh = MagicMock()
    mock_gh.open_incident = fake_open

    bkd_inner = MagicMock()
    bkd_inner.merge_tags_and_update = AsyncMock()
    bkd_inner.follow_up_issue = AsyncMock()
    bkd_inner.update_issue = AsyncMock()
    bkd_inner.get_issue = AsyncMock(return_value=MagicMock(tags=[]))
    bkd_ctx = AsyncMock()
    bkd_ctx.__aenter__ = AsyncMock(return_value=bkd_inner)
    bkd_ctx.__aexit__ = AsyncMock(return_value=False)
    mock_BKDClient = MagicMock(return_value=bkd_ctx)

    mock_rs = MagicMock()
    mock_rs.update_context = AsyncMock()
    mock_rs.cas_state = AsyncMock()
    mock_rs.get = AsyncMock(return_value=None)

    mock_k8s = MagicMock()
    mock_k8s.cleanup_runner = AsyncMock()
    mock_k8s.delete_runner = AsyncMock()

    settings = MagicMock()
    settings.gh_incident_repo = ""
    settings.github_token = "ghp"
    settings.gh_incident_labels = ["sisyphus:incident"]
    settings.default_involved_repos = []
    settings.bkd_base_url = "https://bkd.example/api"
    settings.bkd_token = "t"
    settings.max_auto_retries = 2

    body = MagicMock()
    body.event = "verify.escalate"
    body.projectId = "p"
    body.issueId = "fail"

    ctx = {
        "escalated_reason": "verifier-decision-escalate",
        "auto_retry_count": 5,
        "involved_repos": ["foo/bar"],
        "bkd_intent_url": "https://bkd.example/projects/p/issues/i-1",
        "pr_urls": {"foo/bar": "https://github.com/foo/bar/pull/9"},
    }

    with patch("orchestrator.actions.escalate.settings", settings), \
         patch("orchestrator.actions.escalate.gh_incident", mock_gh), \
         patch("orchestrator.actions.escalate.BKDClient", mock_BKDClient), \
         patch("orchestrator.actions.escalate.req_state", mock_rs), \
         patch("orchestrator.actions.escalate.k8s_runner", mock_k8s), \
         patch("orchestrator.actions.escalate.db", MagicMock()):
        await escalate(body=body, req_id="REQ-x", tags=["REQ-x", "verifier"], ctx=ctx)

    assert len(open_incident_calls) == 1, open_incident_calls
    call = open_incident_calls[0]
    assert call.get("repo") == "foo/bar"
    assert call.get("bkd_intent_url") == "https://bkd.example/projects/p/issues/i-1"
    assert call.get("pr_urls") == {"foo/bar": "https://github.com/foo/bar/pull/9"}


# ─────────────────────────────────────────────────────────────────────────────
# XLINK-S16 / S17 — analyze prompt PR-body footer block
# ─────────────────────────────────────────────────────────────────────────────


def _render_analyze(**overrides) -> str:
    from orchestrator.prompts import render
    base = {
        "req_id": "REQ-x",
        "aissh_server_id": "SRV",
        "project_id": "P",
        "project_alias": "P",
        "issue_id": "I",
        "cloned_repos": [],
        "bkd_intent_issue_url": "https://bkd.example/projects/P/issues/I",
    }
    base.update(overrides)
    return render("analyze.md.j2", **base)


def test_xlink_s16_analyze_prompt_renders_cross_link_block_with_url():
    out = _render_analyze()
    assert "<!-- sisyphus:cross-link -->" in out
    assert "[BKD intent issue](https://bkd.example/projects/P/issues/I)" in out
    assert "REQ-x" in out


def test_xlink_s17_analyze_prompt_omits_link_line_when_url_empty():
    out = _render_analyze(bkd_intent_issue_url="")
    assert "<!-- sisyphus:cross-link -->" in out
    assert "REQ-x" in out
    assert "[BKD intent issue](" not in out


# ─────────────────────────────────────────────────────────────────────────────
# XLINK-S18 / S19 — done_archive prompt Known PRs section
# ─────────────────────────────────────────────────────────────────────────────


def _render_done_archive(**overrides) -> str:
    from orchestrator.prompts import render
    base = {
        "req_id": "REQ-x",
        "branch": "feat/REQ-x",
        "workdir": "/var/sisyphus-ci/feat-REQ-x",
        "accept_issue_id": "AC",
        "project_id": "P",
        "project_alias": "P",
        "pr_urls": {},
    }
    base.update(overrides)
    return render("done_archive.md.j2", **base)


def test_xlink_s18_done_archive_prompt_renders_known_prs_bullets():
    out = _render_done_archive(
        pr_urls={"foo/bar": "https://github.com/foo/bar/pull/9"},
    )
    assert "## Known PRs" in out
    assert "- [foo/bar#9](https://github.com/foo/bar/pull/9)" in out


def test_xlink_s19_done_archive_prompt_omits_section_when_pr_urls_absent():
    out = _render_done_archive(pr_urls={})
    assert "## Known PRs" not in out


# ─────────────────────────────────────────────────────────────────────────────
# XLINK-S20 / S21 — q05 Metabase SQL exposes new columns
# ─────────────────────────────────────────────────────────────────────────────


_Q05 = (
    Path(__file__).resolve().parents[2]
    / "observability" / "queries" / "sisyphus" / "05-active-req-overview.sql"
)


def test_xlink_s20_q05_selects_bkd_intent_url_and_pr_urls_md():
    sql = _Q05.read_text(encoding="utf-8")
    # bkd_intent_url comes from context jsonb directly
    assert re.search(r"context\s*->>\s*'bkd_intent_url'\s+AS\s+bkd_intent_url", sql), (
        f"q05 must select context->>'bkd_intent_url' as bkd_intent_url; sql:\n{sql}"
    )
    # pr_urls_md is a CTE-derived markdown bullet column
    assert "pr_urls_md" in sql
    assert "/pull/" in sql, "pr_urls_md CTE must extract PR number from /pull/<n>"
    assert "string_agg" in sql
    assert "jsonb_each" in sql, "pr_urls_md must iterate context->'pr_urls' jsonb keys"


def test_xlink_s21_q05_pr_urls_md_cte_is_left_joined_so_missing_pr_urls_yield_null():
    sql = _Q05.read_text(encoding="utf-8")
    # LEFT JOIN ensures rows without pr_urls still appear with NULL
    assert re.search(r"LEFT\s+JOIN\s+pr_urls_md", sql, re.IGNORECASE), sql
    # COALESCE on jsonb empty makes the CTE itself robust against NULL/{}
    assert "COALESCE(r.context->'pr_urls'" in sql
