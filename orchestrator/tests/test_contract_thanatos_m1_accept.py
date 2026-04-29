"""Contract tests for REQ-thanatos-m1-impl-1777389456 — THAN-M1-S7 / THAN-M1-S8.

Black-box contracts for create_accept thanatos MCP dispatch vs v0.3-lite fallback.
Derived from:
  openspec/changes/REQ-thanatos-m1-impl-1777389456/specs/thanatos/spec.md
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.state import Event

# ─── Shared helpers ──────────────────────────────────────────────────────────


def _body():
    return type("B", (), {
        "issueId": "source-issue-1",
        "projectId": "test-project",
    })()


class _FakeExecResult:
    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_sec = 0.5


class _FakeRC:
    def __init__(self, results: list[_FakeExecResult] | None = None):
        self.results = list(results or [])
        self.calls: list[dict] = []
        self._idx = 0

    async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
        self.calls.append({"req_id": req_id, "command": command, "env": env})
        result = self.results[self._idx] if self._idx < len(self.results) else _FakeExecResult()
        self._idx += 1
        return result


class _FakePool:
    async def execute(self, sql, *args):
        pass

    async def fetchrow(self, sql, *args):
        return None


class _MockBKDClient:
    def __init__(self):
        self.create_issue_calls: list[dict] = []
        self.follow_up_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self._issue = MagicMock(id="mock-accept-issue-id")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def create_issue(self, **kwargs):
        self.create_issue_calls.append(kwargs)
        return self._issue

    async def follow_up_issue(self, **kwargs):
        self.follow_up_calls.append(kwargs)

    async def update_issue(self, **kwargs):
        self.update_calls.append(kwargs)


def _patch_common(monkeypatch, rc: _FakeRC, lite_fallback_return: dict | None = None):
    """Patch all external I/O for create_accept."""
    from orchestrator.actions import create_accept as mod

    monkeypatch.setattr(mod.k8s_runner, "get_controller", lambda: rc)
    monkeypatch.setattr(mod.db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(mod.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(mod.pr_links, "ensure_pr_links_in_ctx", AsyncMock(return_value={}))
    monkeypatch.setattr(mod, "render", MagicMock(return_value="mock accept prompt"))
    monkeypatch.setattr(mod, "short_title", lambda ctx: "")
    monkeypatch.setattr(mod.settings, "accept_smoke_delay_sec", 0)
    monkeypatch.setattr(mod.settings, "agent_model", "claude-sonnet-4-6")
    monkeypatch.setattr(mod.settings, "bkd_base_url", "http://localhost:3000")
    monkeypatch.setattr(mod.settings, "bkd_token", "test-token")
    monkeypatch.setattr(mod, "skip_if_enabled", lambda *a, **k: None)

    async def fake_resolve_integration_dir(rc_obj, req_id):
        return MagicMock(dir="/workspace/integration/test", reason=None)

    monkeypatch.setattr(mod, "resolve_integration_dir", fake_resolve_integration_dir)

    if lite_fallback_return is not None:
        async def fake_lite(*, req_id, ctx):
            mod._lite_fallback_called = True
            return lite_fallback_return
        monkeypatch.setattr(mod, "_run_lite_fallback", fake_lite)
    else:
        mod._lite_fallback_called = False
        async def fake_lite(*, req_id, ctx):
            mod._lite_fallback_called = True
            return {"emit": Event.ACCEPT_PASS.value}
        monkeypatch.setattr(mod, "_run_lite_fallback", fake_lite)


# ─── THAN-M1-S7: create_accept with thanatos block dispatches agent ──────────


@pytest.mark.asyncio
async def test_than_m1_s7_thanatos_block_dispatches_agent(monkeypatch):
    """THAN-M1-S7: endpoint JSON with thanatos block → BKD accept-agent issue created."""
    from orchestrator.actions import create_accept as mod

    endpoint_json = json.dumps({
        "endpoint": "http://lab.local:8080",
        "thanatos": {
            "pod": "thanatos-lab",
            "namespace": "accept-test",
            "skill_repo": "phona/sisyphus",
        },
    })
    rc = _FakeRC(results=[
        _FakeExecResult(exit_code=0, stdout=f"=== env-up ===\n{endpoint_json}\n"),
    ])

    mock_bkd = _MockBKDClient()
    monkeypatch.setattr(mod, "BKDClient", lambda *a, **k: mock_bkd)

    _patch_common(monkeypatch, rc, lite_fallback_return=None)

    out = await mod.create_accept(
        body=_body(), req_id="REQ-thanatos-m1-test", tags=[], ctx={},
    )

    assert not mod._lite_fallback_called, (
        "v0.3-lite fallback must NOT be called when thanatos block is present"
    )
    assert len(mock_bkd.create_issue_calls) == 1, (
        "BKD create_issue must be called exactly once"
    )
    call = mock_bkd.create_issue_calls[0]
    assert call.get("project_id") == "test-project"
    assert "accept" in call.get("tags", [])
    assert "parent-id:source-issue-1" in call.get("tags", [])
    assert "mock-accept-issue-id" in out.get("accept_issue_id", ""), (
        "result must contain accept_issue_id"
    )
    assert out.get("endpoint") == "http://lab.local:8080"
    assert out.get("namespace") == "accept-req-thanatos-m1-test"


# ─── THAN-M1-S8: create_accept without thanatos block falls back to lite ─────


@pytest.mark.asyncio
async def test_than_m1_s8_no_thanatos_block_falls_back_to_lite(monkeypatch):
    """THAN-M1-S8: endpoint JSON without thanatos block → v0.3-lite shell script path executed."""
    from orchestrator.actions import create_accept as mod

    endpoint_json = json.dumps({
        "endpoint": "http://lab.local:8080",
    })
    rc = _FakeRC(results=[
        _FakeExecResult(exit_code=0, stdout=f"=== env-up ===\n{endpoint_json}\n"),
    ])

    mock_bkd = _MockBKDClient()
    monkeypatch.setattr(mod, "BKDClient", lambda *a, **k: mock_bkd)

    _patch_common(
        monkeypatch,
        rc,
        lite_fallback_return={"emit": Event.ACCEPT_PASS.value, "note": "lite fallback"},
    )

    out = await mod.create_accept(
        body=_body(), req_id="REQ-thanatos-m1-test", tags=[], ctx={},
    )

    assert mod._lite_fallback_called, (
        "v0.3-lite fallback MUST be called when thanatos block is absent"
    )
    assert len(mock_bkd.create_issue_calls) == 0, (
        "BKD create_issue must NOT be called in lite fallback path"
    )
    assert out.get("emit") == Event.ACCEPT_PASS.value
