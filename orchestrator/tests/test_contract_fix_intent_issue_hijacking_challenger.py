"""Challenger contract tests for REQ-fix-intent-issue-hijacking-1777427339.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-fix-intent-issue-hijacking-1777427339/specs/fix-intent-issue-hijacking/spec.md

Scenarios covered:
  FIX-S1  intent issue keeps original title and status; new analyze sub-issue created
  FIX-S2  analyze sub-issue gets analyze tag and working status
  FIX-S3  intent issue gets req_id tag; user hint tags forwarded to both issues
  FIX-S4  idempotency via dispatch_slugs: redispatch returns cached issue id
  FIX-S5  backward compatibility: existing analyze tag on intent issue preserved
  FIX-S6  analyze.md.j2 renders without UndefinedError when intake_summary absent

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _make_body(
    *, project_id: str = "nnvxh8wj", issue_id: str = "intent-issue-abc",
    title: str = "Add login endpoint",
) -> SimpleNamespace:
    return SimpleNamespace(projectId=project_id, issueId=issue_id, title=title)


def _patch_admission(monkeypatch, target_module: Any) -> None:
    """Patch admission gate to always admit."""
    from orchestrator.admission import AdmissionDecision

    monkeypatch.setattr(
        target_module, "check_admission",
        AsyncMock(return_value=AdmissionDecision(admit=True)),
    )


def _patch_k8s_runner(monkeypatch, target_module: Any) -> AsyncMock:
    """Patch k8s_runner.get_controller to return a fake with ensure_runner + exec_in_runner."""
    exec_fn = AsyncMock(
        return_value=SimpleNamespace(stdout="", stderr="", exit_code=0, duration_sec=0.1),
    )
    ensure_fn = AsyncMock(return_value="runner-pod-x")

    class FakeRC:
        def __init__(self):
            self.exec_in_runner = exec_fn
            self.ensure_runner = ensure_fn

    monkeypatch.setattr(target_module.k8s_runner, "get_controller", lambda: FakeRC())
    return exec_fn


def _patch_db_and_dispatch(monkeypatch, target_module: Any, *, slug_hit: str | None = None) -> tuple:
    """Patch db.get_pool, req_state.update_context, dispatch_slugs.get/put."""
    pool_mock = object()
    monkeypatch.setattr(target_module.db, "get_pool", lambda: pool_mock)

    update_ctx = AsyncMock()
    monkeypatch.setattr(target_module.req_state, "update_context", update_ctx)

    get_slug = AsyncMock(return_value=slug_hit)
    put_slug = AsyncMock()
    monkeypatch.setattr(target_module.dispatch_slugs, "get", get_slug)
    monkeypatch.setattr(target_module.dispatch_slugs, "put", put_slug)

    return pool_mock, update_ctx, get_slug, put_slug


def _patch_bkd_client(monkeypatch, target_module: Any, *,
                      intent_issue_tags: list[str] | None = None) -> tuple:
    """Patch BKDClient to capture all BKD calls. Returns captured mocks.

    merge_tags_and_update is implemented with real get-issue → merge → update logic
    so that tests can verify tag preservation behavior.
    """
    create_issue = AsyncMock(
        return_value=SimpleNamespace(id="analyze-sub-issue-xyz"),
    )
    follow_up = AsyncMock(return_value=None)
    update_issue = AsyncMock(return_value=None)

    # Default intent issue tags if not provided
    _intent_tags = list(intent_issue_tags or [])

    async def _get_issue(project_id: str, issue_id: str):
        return SimpleNamespace(id=issue_id, tags=list(_intent_tags))

    async def _merge_tags_and_update(
        project_id: str, issue_id: str, *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        status_id: str | None = None,
    ):
        cur = await _get_issue(project_id, issue_id)
        new_tags = list(cur.tags)
        for t in remove or []:
            while t in new_tags:
                new_tags.remove(t)
        for t in add or []:
            if t not in new_tags:
                new_tags.append(t)
        update_kwargs = {
            "project_id": project_id,
            "issue_id": issue_id,
            "tags": new_tags,
        }
        if status_id is not None:
            update_kwargs["status_id"] = status_id
        await update_issue(**update_kwargs)
        return SimpleNamespace(id=issue_id, tags=new_tags)

    bkd_instance = MagicMock()
    bkd_instance.merge_tags_and_update = _merge_tags_and_update
    bkd_instance.create_issue = create_issue
    bkd_instance.follow_up_issue = follow_up
    bkd_instance.update_issue = update_issue
    bkd_instance.__aenter__ = AsyncMock(return_value=bkd_instance)
    bkd_instance.__aexit__ = AsyncMock(return_value=None)

    monkeypatch.setattr(target_module, "BKDClient", lambda *a, **kw: bkd_instance)
    return bkd_instance.merge_tags_and_update, create_issue, follow_up, update_issue


# ─────────────────────────────────────────────────────────────────────────────
# FIX-S1: intent issue keeps original title and status
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_FIX_S1_intent_issue_keeps_title_and_status(monkeypatch):
    """
    GIVEN a BKD intent issue exists with title "Add login endpoint" and status "todo"
    WHEN start_analyze is dispatched for REQ-X
    THEN the intent issue title remains unchanged and status remains unchanged
    AND a new analyze sub-issue is created with title containing [REQ-X] [ANALYZE]
    """
    import orchestrator.actions.start_analyze as sa

    _patch_admission(monkeypatch, sa)
    _patch_k8s_runner(monkeypatch, sa)
    _patch_db_and_dispatch(monkeypatch, sa)
    _merge_tags, create_issue, _follow_up, update_issue = _patch_bkd_client(monkeypatch, sa)

    body = _make_body(title="Add login endpoint")
    result = await sa.start_analyze(
        body=body, req_id="REQ-X", tags=["intent:analyze"], ctx={},
    )

    # Intent issue must NOT be renamed or have status changed
    for call in update_issue.call_args_list:
        _, kwargs = call
        if kwargs.get("issue_id") == body.issueId:
            assert "title" not in kwargs, (
                "FIX-S1: intent issue title must not be modified"
            )
            assert "status_id" not in kwargs, (
                "FIX-S1: intent issue status must not be modified"
            )

    # A new analyze sub-issue must be created
    create_issue.assert_awaited_once()
    _, c_kwargs = create_issue.call_args
    assert "[REQ-X] [ANALYZE]" in c_kwargs["title"], (
        f"FIX-S1: analyze sub-issue title must contain '[REQ-X] [ANALYZE]', got {c_kwargs['title']!r}"
    )
    assert result["issue_id"] == "analyze-sub-issue-xyz", (
        "FIX-S1: result must contain the new analyze sub-issue id"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-S2: analyze sub-issue gets analyze tag and working status
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_FIX_S2_analyze_sub_issue_tags_and_status(monkeypatch):
    """
    GIVEN start_analyze is dispatched for REQ-X
    WHEN the BKD sub-issue is created
    THEN the sub-issue tags include "analyze" and "REQ-X"
    AND the sub-issue status is set to "working" to trigger the agent
    """
    import orchestrator.actions.start_analyze as sa

    _patch_admission(monkeypatch, sa)
    _patch_k8s_runner(monkeypatch, sa)
    _patch_db_and_dispatch(monkeypatch, sa)
    _merge_tags, create_issue, _follow_up, update_issue = _patch_bkd_client(monkeypatch, sa)

    body = _make_body()
    await sa.start_analyze(
        body=body, req_id="REQ-X", tags=["intent:analyze"], ctx={},
    )

    # Sub-issue creation tags must include analyze and REQ-X
    create_issue.assert_awaited_once()
    _, c_kwargs = create_issue.call_args
    tags = c_kwargs.get("tags", [])
    assert "analyze" in tags, (
        f"FIX-S2: analyze sub-issue tags must include 'analyze', got {tags!r}"
    )
    assert "REQ-X" in tags, (
        f"FIX-S2: analyze sub-issue tags must include 'REQ-X', got {tags!r}"
    )

    # Sub-issue status must be set to working
    working_calls = [
        call for call in update_issue.call_args_list
        if call.kwargs.get("status_id") == "working"
    ]
    assert len(working_calls) >= 1, (
        "FIX-S2: analyze sub-issue must have status_id set to 'working'"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-S3: intent issue gets req_id tag; user hint tags forwarded
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_FIX_S3_intent_issue_gets_req_id_and_hint_tags_forwarded(monkeypatch):
    """
    GIVEN a BKD intent issue exists without REQ-X tag
    WHEN start_analyze is dispatched for REQ-X
    THEN the intent issue gets "REQ-X" tag added
    AND user hint tags (e.g., "repo:phona/foo", "ux:fast-track") are forwarded
       to both intent issue and analyze sub-issue
    """
    import orchestrator.actions.start_analyze as sa

    _patch_admission(monkeypatch, sa)
    _patch_k8s_runner(monkeypatch, sa)
    _patch_db_and_dispatch(monkeypatch, sa)
    _merge_tags, create_issue, _follow_up, update_issue = _patch_bkd_client(monkeypatch, sa)

    body = _make_body()
    await sa.start_analyze(
        body=body, req_id="REQ-X",
        tags=["intent:analyze", "repo:phona/foo", "ux:fast-track"],
        ctx={},
    )

    # Intent issue update must include REQ-X + hint tags (via merge_tags_and_update → update_issue)
    intent_update_calls = [
        call for call in update_issue.call_args_list
        if call.kwargs.get("issue_id") == body.issueId
    ]
    assert len(intent_update_calls) >= 1, (
        "FIX-S3: intent issue must be updated with merged tags"
    )
    intent_tags = intent_update_calls[0].kwargs.get("tags", [])
    assert "REQ-X" in intent_tags, (
        f"FIX-S3: intent issue must get REQ-X tag, got {intent_tags!r}"
    )
    assert "repo:phona/foo" in intent_tags, (
        f"FIX-S3: intent issue must get repo hint tag, got {intent_tags!r}"
    )
    assert "ux:fast-track" in intent_tags, (
        f"FIX-S3: intent issue must get ux hint tag, got {intent_tags!r}"
    )

    # Analyze sub-issue must also get hint tags
    _, c_kwargs = create_issue.call_args
    sub_tags = c_kwargs.get("tags", [])
    assert "repo:phona/foo" in sub_tags, (
        f"FIX-S3: analyze sub-issue must get repo hint tag, got {sub_tags!r}"
    )
    assert "ux:fast-track" in sub_tags, (
        f"FIX-S3: analyze sub-issue must get ux hint tag, got {sub_tags!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-S4: idempotency via dispatch_slugs
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_FIX_S4_redispatch_is_idempotent(monkeypatch):
    """
    GIVEN start_analyze has already created an analyze sub-issue for REQ-X
    WHEN start_analyze is dispatched again for the same REQ-X
    THEN no new analyze sub-issue is created
    AND the existing analyze issue ID is returned from the slug cache
    """
    import orchestrator.actions.start_analyze as sa

    _patch_admission(monkeypatch, sa)
    _patch_k8s_runner(monkeypatch, sa)
    _patch_db_and_dispatch(
        monkeypatch, sa, slug_hit="existing-analyze-issue-123",
    )
    _merge_tags, create_issue, follow_up, update_issue = _patch_bkd_client(monkeypatch, sa)

    body = _make_body()
    result = await sa.start_analyze(
        body=body, req_id="REQ-X", tags=["intent:analyze"], ctx={},
    )

    # No new issue should be created
    create_issue.assert_not_awaited()

    # Must return the cached issue id
    assert result.get("issue_id") == "existing-analyze-issue-123", (
        f"FIX-S4: redispatch must return cached issue id, got {result!r}"
    )

    # Must not send follow-up or update status on cached hit
    follow_up.assert_not_awaited()
    update_issue.assert_not_awaited()


# ─────────────────────────────────────────────────────────────────────────────
# FIX-S5: backward compatibility with existing intent issue tags
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_FIX_S5_backward_compat_preserves_existing_analyze_tag(monkeypatch):
    """
    GIVEN an intent issue already has "analyze" tag from a prior run
    WHEN start_analyze is dispatched
    THEN the existing "analyze" tag on the intent issue is preserved
    AND the new analyze sub-issue still gets its own "analyze" tag
    """
    import orchestrator.actions.start_analyze as sa

    _patch_admission(monkeypatch, sa)
    _patch_k8s_runner(monkeypatch, sa)
    _patch_db_and_dispatch(monkeypatch, sa)
    # Intent issue already has "analyze" from a prior (old-version) run
    _merge_tags, create_issue, _follow_up, update_issue = _patch_bkd_client(
        monkeypatch, sa, intent_issue_tags=["analyze"],
    )

    body = _make_body()
    await sa.start_analyze(
        body=body, req_id="REQ-X",
        tags=["analyze", "repo:phona/foo"],
        ctx={},
    )

    # Intent issue update must still contain the existing "analyze" tag
    intent_update_calls = [
        call for call in update_issue.call_args_list
        if call.kwargs.get("issue_id") == body.issueId
    ]
    assert len(intent_update_calls) >= 1, (
        "FIX-S5: intent issue must be updated"
    )
    intent_tags = intent_update_calls[0].kwargs.get("tags", [])
    assert "analyze" in intent_tags, (
        f"FIX-S5: existing analyze tag on intent issue must be preserved, got {intent_tags!r}"
    )

    # New analyze sub-issue must still get analyze tag
    _, c_kwargs = create_issue.call_args
    sub_tags = c_kwargs.get("tags", [])
    assert "analyze" in sub_tags, (
        f"FIX-S5: new analyze sub-issue must get analyze tag, got {sub_tags!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# FIX-S6: analyze.md.j2 handles direct analyze path without intake summary
# ─────────────────────────────────────────────────────────────────────────────


def test_FIX_S6_analyze_prompt_renders_without_undefined_error():
    """
    GIVEN the direct analyze path (no intake) is triggered
    WHEN the analyze prompt is rendered without intake_summary
    THEN the prompt renders successfully without UndefinedError on intake_summary
    AND the prompt includes guidance for the agent to self-analyze the intent issue
    """
    import jinja2

    prompts_dir = os.path.join(
        os.path.dirname(__file__), "..", "src", "orchestrator", "prompts",
    )
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(prompts_dir),
        undefined=jinja2.Undefined,
    )
    template = env.get_template("analyze.md.j2")

    # Render without intake_summary — must not raise
    try:
        result = template.render(
            req_id="REQ-X",
            aissh_server_id="test-server-id",
            project_id="nnvxh8wj",
            project_alias="nnvxh8wj",
            issue_id="analyze-issue-xyz",
            cloned_repos=None,
            bkd_intent_issue_url="https://bkd.example.test/projects/nnvxh8wj/issues/intent-issue-abc",
            status_block={"stage": "analyze", "req_id": "REQ-X"},
            mcp_capability_providers={"ssh_exec": "aissh-tao"},
        )
    except jinja2.UndefinedError as e:
        pytest.fail(f"FIX-S6: analyze.md.j2 raised UndefinedError without intake_summary: {e}")

    # Must contain guidance for direct analyze path
    assert "直接 analyze 入口" in result or "direct analyze" in result.lower(), (
        "FIX-S6: prompt must include guidance for direct analyze path (no intake summary)"
    )

    # Must not contain raw template variables
    assert "{{ intake_summary" not in result, (
        "FIX-S6: rendered prompt must not contain unexpanded template variables"
    )
