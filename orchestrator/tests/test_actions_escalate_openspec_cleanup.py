"""Unit tests for openspec/changes cleanup in escalate path (Fix A).

REQ-openspec-changes-cleanup-1777343379

Scenarios:
  OSC-S1  real escalate path calls exec_in_runner with rm + commit command
  OSC-S2  cleanup exception does NOT block escalate (fail-open)
  OSC-S3  no repos → exec_in_runner is never called
  OSC-S4  runner controller unavailable → escalate still completes (fail-open)
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.actions import escalate as esc_mod
from orchestrator.state import ReqState

# ─── shared helpers ───────────────────────────────────────────────────────────


@dataclass
class FakeExecResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.1


def _body(event="verifier-decision-escalate", issue_id="issue-x", project_id="proj-x"):
    return SimpleNamespace(event=event, issueId=issue_id, projectId=project_id)


def _make_fake_pool(state=ReqState.REVIEW_RUNNING):
    """Minimal fake pool for req_state.get / cas_transition / update_context."""

    class _Row:
        pass

    row = _Row()
    row.state = state

    class _FakePool:
        async def fetchrow(self, *a, **kw):
            return {"state": state.value, "context": {}, "history": [],
                    "req_id": "REQ-x", "project_id": "proj-x",
                    "created_at": None, "updated_at": None}

        async def execute(self, *a, **kw):
            pass

    return _FakePool()


class _FakeBKD:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def merge_tags_and_update(self, *a, **kw):
        pass

    async def update_issue(self, *a, **kw):
        pass


# ─── OSC-S1: real escalate calls exec_in_runner with cleanup command ─────────


@pytest.mark.asyncio
async def test_s1_real_escalate_calls_openspec_cleanup(monkeypatch):
    """OSC-S1: verifier-decision-escalate triggers cleanup exec_in_runner for each repo."""
    exec_calls: list[str] = []

    async def fake_exec(req_id, cmd, *, timeout_sec=300):
        exec_calls.append(cmd)
        return FakeExecResult(exit_code=0)

    fake_rc = MagicMock()
    fake_rc.exec_in_runner = fake_exec

    monkeypatch.setattr(esc_mod.k8s_runner, "get_controller", lambda: fake_rc)
    monkeypatch.setattr(esc_mod, "BKDClient", _FakeBKD)
    monkeypatch.setattr(esc_mod.db, "get_pool", lambda: _make_fake_pool())
    monkeypatch.setattr(esc_mod.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(esc_mod.req_state, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(esc_mod, "gh_incident", MagicMock(open_incident=AsyncMock(return_value=None)))
    monkeypatch.setattr(esc_mod.settings, "github_token", "")
    monkeypatch.setattr(esc_mod.settings, "default_involved_repos", [])

    ctx = {
        "involved_repos": ["phona/sisyphus", "phona/ttpos"],
        "intent_issue_id": "intent-1",
    }
    result = await esc_mod.escalate(
        body=_body(event="verifier-decision-escalate"),
        req_id="REQ-test-cleanup-1234",
        tags=["REQ-test-cleanup-1234"],
        ctx=ctx,
    )

    assert result.get("escalated") is True
    # Should have called exec_in_runner twice (one per repo)
    assert len(exec_calls) == 2
    # Each call should contain the rm + commit pattern
    for cmd in exec_calls:
        assert "openspec/changes/REQ-test-cleanup-1234" in cmd
        assert "rm -rf" in cmd
        assert "git commit" in cmd


# ─── OSC-S2: cleanup exception does NOT block escalate ────────────────────────


@pytest.mark.asyncio
async def test_s2_cleanup_failure_is_fail_open(monkeypatch):
    """OSC-S2: exec_in_runner raising RuntimeError must NOT prevent escalate from returning."""

    async def exploding_exec(req_id, cmd, *, timeout_sec=300):
        raise RuntimeError("pod not found")

    fake_rc = MagicMock()
    fake_rc.exec_in_runner = exploding_exec

    monkeypatch.setattr(esc_mod.k8s_runner, "get_controller", lambda: fake_rc)
    monkeypatch.setattr(esc_mod, "BKDClient", _FakeBKD)
    monkeypatch.setattr(esc_mod.db, "get_pool", lambda: _make_fake_pool())
    monkeypatch.setattr(esc_mod.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(esc_mod.req_state, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(esc_mod, "gh_incident", MagicMock(open_incident=AsyncMock(return_value=None)))
    monkeypatch.setattr(esc_mod.settings, "github_token", "")
    monkeypatch.setattr(esc_mod.settings, "default_involved_repos", [])

    ctx = {
        "involved_repos": ["phona/sisyphus"],
        "intent_issue_id": "intent-1",
    }
    # Must not raise
    result = await esc_mod.escalate(
        body=_body(event="verifier-decision-escalate"),
        req_id="REQ-test-fail-open-1234",
        tags=[],
        ctx=ctx,
    )
    assert result.get("escalated") is True


# ─── OSC-S3: no repos → exec_in_runner never called ─────────────────────────


@pytest.mark.asyncio
async def test_s3_no_repos_skips_cleanup(monkeypatch):
    """OSC-S3: When no involved_repos are resolved, exec_in_runner is never called."""
    exec_calls: list[str] = []

    async def fake_exec(req_id, cmd, *, timeout_sec=300):
        exec_calls.append(cmd)
        return FakeExecResult()

    fake_rc = MagicMock()
    fake_rc.exec_in_runner = fake_exec

    monkeypatch.setattr(esc_mod.k8s_runner, "get_controller", lambda: fake_rc)
    monkeypatch.setattr(esc_mod, "BKDClient", _FakeBKD)
    monkeypatch.setattr(esc_mod.db, "get_pool", lambda: _make_fake_pool())
    monkeypatch.setattr(esc_mod.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(esc_mod.req_state, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(esc_mod, "gh_incident", MagicMock(open_incident=AsyncMock(return_value=None)))
    monkeypatch.setattr(esc_mod.settings, "github_token", "")
    monkeypatch.setattr(esc_mod.settings, "default_involved_repos", [])

    # No involved_repos in ctx, no tags, no default
    result = await esc_mod.escalate(
        body=_body(event="verifier-decision-escalate"),
        req_id="REQ-no-repos-1234",
        tags=[],
        ctx={},
    )
    assert result.get("escalated") is True
    assert exec_calls == []


# ─── OSC-S4: runner controller unavailable → escalate completes ──────────────


@pytest.mark.asyncio
async def test_s4_no_runner_controller_fail_open(monkeypatch):
    """OSC-S4: get_controller() raising RuntimeError must not block escalate."""
    monkeypatch.setattr(
        esc_mod.k8s_runner, "get_controller",
        lambda: (_ for _ in ()).throw(RuntimeError("no controller")),
    )
    monkeypatch.setattr(esc_mod, "BKDClient", _FakeBKD)
    monkeypatch.setattr(esc_mod.db, "get_pool", lambda: _make_fake_pool())
    monkeypatch.setattr(esc_mod.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(esc_mod.req_state, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(esc_mod, "gh_incident", MagicMock(open_incident=AsyncMock(return_value=None)))
    monkeypatch.setattr(esc_mod.settings, "github_token", "")
    monkeypatch.setattr(esc_mod.settings, "default_involved_repos", [])

    ctx = {"involved_repos": ["phona/sisyphus"], "intent_issue_id": "intent-1"}
    result = await esc_mod.escalate(
        body=_body(event="verifier-decision-escalate"),
        req_id="REQ-no-ctrl-1234",
        tags=[],
        ctx=ctx,
    )
    assert result.get("escalated") is True
