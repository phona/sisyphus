"""Challenger contract tests for REQ-dedicated-fixer-prompts-1777420810.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-dedicated-fixer-prompts-1777420810/specs/fixer-prompts/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  DFP-S1  fixer=dev → verifier-fix-dev.md.j2 prompt with "DEV FIXER" + "LOCKED：只改业务代码"
  DFP-S2  fixer=spec → verifier-fix-spec.md.j2 prompt with "SPEC FIXER" + "LOCKED：只改 spec 相关文件"
  DFP-S3  missing fixer → fallback bugfix.md.j2
  DFP-S4  dev prompt contains openspec/test/Makefile prohibition
  DFP-S5  spec prompt contains business-code/test prohibition + spec-drift warning
  DFP-S6  webhook derives target_repo from decision JSON
  DFP-S7  target_repo appears in rendered dev prompt + scopes to one repo
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator import prompts
from orchestrator.router import derive_verifier_event
from orchestrator.state import Event

_REQ_ID = "REQ-dedicated-fixer-prompts-1777420810"
_PROJECT = "nnvxh8wj"


class _FakeBody:
    projectId = _PROJECT


# ─── Shared fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def _patch_start_fixer_deps(monkeypatch):
    """Patch all external I/O in start_fixer; return list that captures render() calls."""
    from orchestrator.actions import _verifier

    # settings
    monkeypatch.setattr(_verifier.settings, "fixer_round_cap", 5)
    monkeypatch.setattr(_verifier.settings, "workdir_root", "/workspace")
    monkeypatch.setattr(_verifier.settings, "bkd_base_url", "http://localhost:3000")
    monkeypatch.setattr(_verifier.settings, "bkd_token", "test")
    monkeypatch.setattr(_verifier.settings, "agent_model", "claude-sonnet-4-6")

    # DB / store
    fake_pool = object()
    monkeypatch.setattr(_verifier.db, "get_pool", lambda: fake_pool)
    monkeypatch.setattr(_verifier.dispatch_slugs, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(_verifier.dispatch_slugs, "put", AsyncMock())
    monkeypatch.setattr(_verifier.req_state, "update_context", AsyncMock())

    # PR links
    monkeypatch.setattr(_verifier.pr_links, "ensure_pr_links_in_ctx", AsyncMock(return_value={}))
    monkeypatch.setattr(_verifier.pr_links, "pr_link_tags", lambda links: [])

    # Capture every call to _verifier.render()
    # NOTE: _verifier imported render at module load time, so we must patch
    # _verifier.render, not prompts.render.
    render_calls = []
    _orig_render = _verifier.render

    def _capture(template_name, **kwargs):
        result = _orig_render(template_name, **kwargs)
        render_calls.append({"template": template_name, "kwargs": dict(kwargs), "result": result})
        return result

    monkeypatch.setattr(_verifier, "render", _capture)

    # BKDClient
    fake_issue = MagicMock()
    fake_issue.id = "fixer-issue-123"

    class _FakeBKD:
        def __init__(self):
            self.create_issue = AsyncMock(return_value=fake_issue)
            self.follow_up_issue = AsyncMock()
            self.update_issue = AsyncMock()

    class _FakeCtxMgr:
        async def __aenter__(self):
            return _FakeBKD()
        async def __aexit__(self, *args, **kwargs):
            pass

    def _mock_bkd_client(*args, **kwargs):
        return _FakeCtxMgr()

    monkeypatch.setattr(_verifier, "BKDClient", _mock_bkd_client)

    return render_calls


# ─── DFP-S1 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_DFP_S1_dev_fixer_uses_dedicated_dev_prompt(_patch_start_fixer_deps, monkeypatch):
    """GIVEN fixer=dev in ctx WHEN start_fixer invoked
    THEN rendered prompt is from verifier-fix-dev.md.j2
    AND contains "DEV FIXER" AND "LOCKED：只改业务代码".
    """
    from orchestrator.actions._verifier import start_fixer

    render_calls = _patch_start_fixer_deps

    await start_fixer(
        body=_FakeBody(),
        req_id=_REQ_ID,
        tags=["verify:dev_cross_check"],
        ctx={
            "verifier_fixer": "dev",
            "verifier_issue_id": "v-issue-456",
        },
    )

    assert len(render_calls) == 1
    call = render_calls[0]
    assert call["template"] == "verifier-fix-dev.md.j2"
    assert "DEV FIXER" in call["result"]
    assert "LOCKED：只改业务代码" in call["result"]


# ─── DFP-S2 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_DFP_S2_spec_fixer_uses_dedicated_spec_prompt(_patch_start_fixer_deps, monkeypatch):
    """GIVEN fixer=spec in ctx WHEN start_fixer invoked
    THEN rendered prompt is from verifier-fix-spec.md.j2
    AND contains "SPEC FIXER" AND "LOCKED：只改 spec 相关文件".
    """
    from orchestrator.actions._verifier import start_fixer

    render_calls = _patch_start_fixer_deps

    await start_fixer(
        body=_FakeBody(),
        req_id=_REQ_ID,
        tags=["verify:spec_lint"],
        ctx={
            "verifier_fixer": "spec",
            "verifier_issue_id": "v-issue-456",
        },
    )

    assert len(render_calls) == 1
    call = render_calls[0]
    assert call["template"] == "verifier-fix-spec.md.j2"
    assert "SPEC FIXER" in call["result"]
    assert "LOCKED：只改 spec 相关文件" in call["result"]


# ─── DFP-S3 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_DFP_S3_missing_fixer_fallback_to_bugfix(_patch_start_fixer_deps, monkeypatch):
    """GIVEN no fixer field in ctx WHEN start_fixer invoked
    THEN rendered prompt falls back to bugfix.md.j2.
    """
    from orchestrator.actions._verifier import start_fixer

    render_calls = _patch_start_fixer_deps

    await start_fixer(
        body=_FakeBody(),
        req_id=_REQ_ID,
        tags=["verify:dev_cross_check"],
        ctx={
            "verifier_issue_id": "v-issue-456",
            # intentionally no verifier_fixer
        },
    )

    assert len(render_calls) == 1
    call = render_calls[0]
    assert call["template"] == "bugfix.md.j2"


# ─── DFP-S4 ─────────────────────────────────────────────────────────────────

def test_DFP_S4_dev_prompt_contains_openspec_test_makefile_prohibition():
    """GIVEN verifier-fix-dev.md.j2 rendered
    THEN prompt contains explicit prohibition against modifying openspec files
    AND test files AND Makefile / CI configuration.
    """
    rendered = prompts.render(
        "verifier-fix-dev.md.j2",
        req_id=_REQ_ID,
        round_n=1,
        kind="verifier-dev",
        source_issue_id="src-123",
        branch=f"feat/{_REQ_ID}",
        workdir=f"/workspace/feat-{_REQ_ID}",
        project_id=_PROJECT,
        project_alias=_PROJECT,
        target_repo="",
    )
    assert "openspec" in rendered
    assert "test" in rendered.lower()
    assert "Makefile" in rendered or "CI 配置" in rendered


# ─── DFP-S5 ─────────────────────────────────────────────────────────────────

def test_DFP_S5_spec_prompt_contains_business_code_test_prohibition_and_spec_drift():
    """GIVEN verifier-fix-spec.md.j2 rendered
    THEN prompt contains explicit prohibition against modifying business source code
    AND test files AND a spec-drift warning.
    """
    rendered = prompts.render(
        "verifier-fix-spec.md.j2",
        req_id=_REQ_ID,
        round_n=1,
        kind="verifier-spec",
        source_issue_id="src-123",
        branch=f"feat/{_REQ_ID}",
        workdir=f"/workspace/feat-{_REQ_ID}",
        project_id=_PROJECT,
        project_alias=_PROJECT,
        target_repo="",
    )
    assert "业务代码" in rendered
    assert "test" in rendered.lower()
    assert "spec-drift" in rendered.lower() or "spec drift" in rendered.lower()


# ─── DFP-S6 ─────────────────────────────────────────────────────────────────

def test_DFP_S6_target_repo_extracted_from_decision_json():
    """GIVEN verifier decision JSON with target_repo WHEN webhook parses decision
    THEN extracted decision dict contains target_repo.
    """
    decision_json = (
        '```json\n'
        '{"action": "fix", "fixer": "dev", "scope": "src/orchestrator", '
        '"reason": "routing bug", "confidence": "high", '
        '"target_repo": "owner/repo-a"}\n'
        '```'
    )
    event, decision, _reason = derive_verifier_event(decision_json, tags=[])
    assert event == Event.VERIFY_FIX_NEEDED
    assert decision is not None
    assert decision.get("target_repo") == "owner/repo-a"


# ─── DFP-S7 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_DFP_S7_target_repo_appears_in_rendered_dev_prompt(_patch_start_fixer_deps, monkeypatch):
    """GIVEN start_fixer invoked with verifier_target_repo=owner/repo-a and fixer=dev
    WHEN prompt is rendered
    THEN prompt contains the target repository identifier
    AND instructs agent to modify only that repository.
    """
    from orchestrator.actions._verifier import start_fixer

    render_calls = _patch_start_fixer_deps

    await start_fixer(
        body=_FakeBody(),
        req_id=_REQ_ID,
        tags=["verify:dev_cross_check"],
        ctx={
            "verifier_fixer": "dev",
            "verifier_issue_id": "v-issue-456",
            "verifier_target_repo": "owner/repo-a",
        },
    )

    assert len(render_calls) == 1
    call = render_calls[0]
    assert call["template"] == "verifier-fix-dev.md.j2"
    rendered = call["result"]
    assert "owner/repo-a" in rendered
    # The prompt should instruct the agent to scope work to that repo
    assert "只改" in rendered and "这一个仓" in rendered
