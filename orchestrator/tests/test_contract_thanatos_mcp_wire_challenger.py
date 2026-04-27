"""Challenger contract tests for REQ-415 (thanatos M1 wire accept stage to MCP).

Black-box contracts derived exclusively from:
  openspec/changes/REQ-415/specs/thanatos-mcp-wire/spec.md
  openspec/changes/REQ-415/specs/thanatos-mcp-wire/contract.spec.yaml

Dev MUST NOT modify these tests to make them pass — fix the implementation.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not this file.

Scenarios covered:
  TMW-S1  create_accept with thanatos block → render ctx has pod/namespace/skill_repo
  TMW-S2  create_accept without thanatos block → render ctx has thanatos_pod=None/falsy
  TMW-S3  accept.md.j2 with thanatos_pod set → kubectl exec + MCP tools/call + kb_updates + git push
  TMW-S4  accept.md.j2 with thanatos_pod=None → spec.md glob; no 'python -m thanatos.server'
  TMW-S5  both branches include result:pass, result:fail tokens and statusId=review
  TMW-S6  thanatos block without namespace → inherits top-level namespace in render ctx
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.prompts import render

# ── shared test helpers ───────────────────────────────────────────────────────


@dataclass
class _Body:
    issueId: str
    projectId: str = "proj-tmw"


@dataclass
class _FakeIssue:
    id: str
    project_id: str = "proj-tmw"
    issue_number: int = 0
    title: str = ""
    status_id: str = "todo"
    tags: list = field(default_factory=list)
    session_status: str | None = None
    description: str | None = None


def _make_fake_bkd() -> AsyncMock:
    bkd = AsyncMock()
    bkd.create_issue = AsyncMock(return_value=_FakeIssue(id="accept-tmw-1"))
    bkd.update_issue = AsyncMock(return_value=_FakeIssue(id="accept-tmw-1"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    bkd.list_issues = AsyncMock(return_value=[])
    bkd.get_issue = AsyncMock(return_value=_FakeIssue(id="src-1", tags=[]))
    bkd.merge_tags_and_update = AsyncMock(return_value=_FakeIssue(id="accept-tmw-1"))
    return bkd


def _patch_bkd(monkeypatch, fake: AsyncMock) -> None:
    @asynccontextmanager
    async def _ctx(*_a, **_kw):
        yield fake

    monkeypatch.setattr("orchestrator.actions.create_accept.BKDClient", _ctx)


def _patch_db(monkeypatch) -> None:
    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    monkeypatch.setattr("orchestrator.actions.create_accept.db.get_pool", lambda: pool)


def _fake_rc(env_up_last_line: str):
    """k8s_runner controller that returns the given JSON as last stdout line of env-up."""
    from orchestrator.k8s_runner import ExecResult

    class _RC:
        async def exec_in_runner(self, req_id, command, env=None, timeout_sec=600):
            if env and env.get("SISYPHUS_STAGE") == "accept-resolve":
                return ExecResult(
                    exit_code=0,
                    stdout="I:/workspace/integration/lab\n",
                    stderr="",
                    duration_sec=0.1,
                )
            return ExecResult(
                exit_code=0,
                stdout=f"helm output\n{env_up_last_line}\n",
                stderr="",
                duration_sec=5.0,
            )

    return _RC()


_RENDER_TARGET = "orchestrator.actions.create_accept.render"

# Minimal context required by accept.md.j2 (per contract.spec.yaml required fields)
_BASE_CTX: dict = dict(
    req_id="REQ-415",
    endpoint="http://lab.svc:8080",
    namespace="accept-req-415",
    source_issue_id="src-issue-1",
    project_id="proj-tmw",
    project_alias="nnvxh8wj",
    accept_env={},
)


# ── TMW-S1 ───────────────────────────────────────────────────────────────────


async def test_tmw_s1_thanatos_block_parsed_into_render_ctx(monkeypatch) -> None:
    """
    TMW-S1: GIVEN env-up JSON with thanatos block (pod + namespace + skill_repo)
    WHEN create_accept parses it
    THEN render receives thanatos_pod='thanatos-abc', thanatos_namespace='accept-req-415',
         thanatos_skill_repo='ttpos-flutter'
    """
    from orchestrator.actions import create_accept as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)

    env_up_json = json.dumps({
        "endpoint": "http://lab.svc:8080",
        "namespace": "accept-req-415",
        "thanatos": {
            "pod": "thanatos-abc",
            "namespace": "accept-req-415",
            "skill_repo": "ttpos-flutter",
        },
    })
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: _fake_rc(env_up_json),
    )

    captured: dict = {}

    def _capturing_render(template, **kwargs):
        if template == "accept.md.j2":
            captured.update(kwargs)
        return "dummy-prompt"

    monkeypatch.setattr(_RENDER_TARGET, _capturing_render)

    await mod.create_accept(
        body=_Body(issueId="src-issue-1"),
        req_id="REQ-415",
        tags=["accept", "REQ-415"],
        ctx={},
    )

    assert captured.get("thanatos_pod") == "thanatos-abc", (
        f"TMW-S1: thanatos_pod MUST be 'thanatos-abc'; got {captured.get('thanatos_pod')!r}. "
        f"render ctx keys: {list(captured)}"
    )
    assert captured.get("thanatos_namespace") == "accept-req-415", (
        f"TMW-S1: thanatos_namespace MUST be 'accept-req-415'; "
        f"got {captured.get('thanatos_namespace')!r}"
    )
    assert captured.get("thanatos_skill_repo") == "ttpos-flutter", (
        f"TMW-S1: thanatos_skill_repo MUST be 'ttpos-flutter'; "
        f"got {captured.get('thanatos_skill_repo')!r}"
    )


# ── TMW-S2 ───────────────────────────────────────────────────────────────────


async def test_tmw_s2_no_thanatos_block_leaves_pod_falsy(monkeypatch) -> None:
    """
    TMW-S2: GIVEN env-up JSON without thanatos key
    WHEN create_accept parses it
    THEN thanatos_pod in render ctx is None/falsy (template renders legacy branch)
    """
    from orchestrator.actions import create_accept as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)

    env_up_json = json.dumps({
        "endpoint": "http://localhost:18000",
        "namespace": "accept-req-415",
    })
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: _fake_rc(env_up_json),
    )

    captured: dict = {}

    def _capturing_render(template, **kwargs):
        if template == "accept.md.j2":
            captured.update(kwargs)
        return "dummy-prompt"

    monkeypatch.setattr(_RENDER_TARGET, _capturing_render)

    await mod.create_accept(
        body=_Body(issueId="src-issue-2"),
        req_id="REQ-415",
        tags=["accept", "REQ-415"],
        ctx={},
    )

    thanatos_pod = captured.get("thanatos_pod")
    assert not thanatos_pod, (
        f"TMW-S2: thanatos_pod MUST be None/falsy when thanatos block is absent; "
        f"got {thanatos_pod!r}"
    )


# ── TMW-S3 ───────────────────────────────────────────────────────────────────


def test_tmw_s3_thanatos_branch_has_kubectl_exec_mcp_run_all_and_git_push() -> None:
    """
    TMW-S3: GIVEN accept.md.j2 rendered with thanatos_pod='thanatos-abc'
    THEN output contains:
      - 'kubectl -n accept-req-415 exec -i thanatos-abc -- python -m thanatos.server'
      - 'tools/call'
      - 'run_all'
      - 'kb_updates'
      - 'git push origin feat/REQ-415'
    """
    out = render(
        "accept.md.j2",
        **_BASE_CTX,
        thanatos_pod="thanatos-abc",
        thanatos_namespace="accept-req-415",
        thanatos_skill_repo="ttpos-flutter",
    )

    assert "kubectl -n accept-req-415 exec -i thanatos-abc -- python -m thanatos.server" in out, (
        "TMW-S3: output MUST contain "
        "'kubectl -n accept-req-415 exec -i thanatos-abc -- python -m thanatos.server';\n"
        f"first 600 chars: {out[:600]!r}"
    )
    assert "tools/call" in out, (
        f"TMW-S3: output MUST mention 'tools/call' (MCP JSON-RPC); snippet: {out[:400]!r}"
    )
    assert "run_all" in out, (
        f"TMW-S3: output MUST mention 'run_all' tool name; snippet: {out[:400]!r}"
    )
    assert "kb_updates" in out, (
        f"TMW-S3: output MUST reference 'kb_updates'; snippet: {out[:400]!r}"
    )
    assert "git push origin feat/REQ-415" in out, (
        f"TMW-S3: output MUST instruct 'git push origin feat/REQ-415'; snippet: {out[:400]!r}"
    )


# ── TMW-S4 ───────────────────────────────────────────────────────────────────


def test_tmw_s4_fallback_branch_has_spec_glob_no_thanatos_server() -> None:
    """
    TMW-S4: GIVEN accept.md.j2 rendered with thanatos_pod=None
    THEN output contains '/workspace/source/*/openspec/changes/REQ-415/specs/*/spec.md'
    AND does NOT contain 'python -m thanatos.server'
    """
    out = render(
        "accept.md.j2",
        **_BASE_CTX,
        thanatos_pod=None,
        thanatos_namespace=None,
        thanatos_skill_repo=None,
    )

    assert "/workspace/source/*/openspec/changes/REQ-415/specs/*/spec.md" in out, (
        "TMW-S4: legacy branch MUST contain spec.md glob pattern "
        "'/workspace/source/*/openspec/changes/REQ-415/specs/*/spec.md';\n"
        f"first 600 chars: {out[:600]!r}"
    )
    assert "python -m thanatos.server" not in out, (
        "TMW-S4: legacy branch MUST NOT contain 'python -m thanatos.server';\n"
        f"first 600 chars: {out[:600]!r}"
    )


# ── TMW-S5 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "thanatos_pod, label",
    [
        ("thanatos-abc", "thanatos branch"),
        (None, "legacy branch"),
    ],
)
def test_tmw_s5_both_branches_gate_on_result_tags_and_review(
    thanatos_pod: str | None, label: str
) -> None:
    """
    TMW-S5: Both thanatos and legacy branches MUST instruct the agent to PATCH BKD issue
    with result:pass / result:fail tags and statusId=review.
    """
    out = render(
        "accept.md.j2",
        **_BASE_CTX,
        thanatos_pod=thanatos_pod,
        thanatos_namespace="accept-req-415" if thanatos_pod else None,
        thanatos_skill_repo="ttpos-flutter" if thanatos_pod else None,
    )

    assert "result:pass" in out, (
        f"TMW-S5 [{label}]: output MUST contain 'result:pass'; snippet: {out[:400]!r}"
    )
    assert "result:fail" in out, (
        f"TMW-S5 [{label}]: output MUST contain 'result:fail'; snippet: {out[:400]!r}"
    )
    assert "review" in out, (
        f"TMW-S5 [{label}]: output MUST reference statusId=review; snippet: {out[:400]!r}"
    )


# ── TMW-S6 ───────────────────────────────────────────────────────────────────


async def test_tmw_s6_thanatos_namespace_defaults_to_top_level(monkeypatch) -> None:
    """
    TMW-S6: GIVEN thanatos block has pod + skill_repo but NO namespace
    WHEN create_accept parses the env-up JSON
    THEN thanatos_namespace in render ctx equals the top-level namespace ('accept-req-415')
    """
    from orchestrator.actions import create_accept as mod

    fake_bkd = _make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)

    env_up_json = json.dumps({
        "endpoint": "http://lab.svc:8080",
        "namespace": "accept-req-415",
        "thanatos": {
            "pod": "thanatos-abc",
            "skill_repo": "ttpos-flutter",
            # intentionally no "namespace" field
        },
    })
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: _fake_rc(env_up_json),
    )

    captured: dict = {}

    def _capturing_render(template, **kwargs):
        if template == "accept.md.j2":
            captured.update(kwargs)
        return "dummy-prompt"

    monkeypatch.setattr(_RENDER_TARGET, _capturing_render)

    await mod.create_accept(
        body=_Body(issueId="src-issue-6"),
        req_id="REQ-415",
        tags=["accept", "REQ-415"],
        ctx={},
    )

    thanatos_namespace = captured.get("thanatos_namespace")
    assert thanatos_namespace == "accept-req-415", (
        f"TMW-S6: thanatos_namespace MUST default to top-level namespace 'accept-req-415' "
        f"when block omits its own namespace; got {thanatos_namespace!r}"
    )
