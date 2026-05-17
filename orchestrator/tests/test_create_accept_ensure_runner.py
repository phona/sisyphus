"""Tests for create_accept runner-pod ensure logic (#308).

Two scenarios:
  - ERS-S1: runner pod NotFound → ensure_runner_with_clone called → proceeds
  - ERS-S2: runner pod already Running → ensure skipped → proceeds normally
"""
from __future__ import annotations

import pytest

from orchestrator.actions import create_accept as mod
from orchestrator.actions._integration_resolver import ResolveResult
from orchestrator.k8s_runner import ExecResult, RunnerStatus
from orchestrator.state import Event

# ─── Fakes ────────────────────────────────────────────────────────────────


class _FakeRC:
    def __init__(self, pod_phase: str = "Running"):
        self._pod_phase = pod_phase
        self.ensure_runner_calls: list[str] = []
        self.exec_calls: list[dict] = []

    async def get_runner_status(self, req_id: str) -> RunnerStatus | None:
        if self._pod_phase == "NotFound":
            return None
        return RunnerStatus(
            req_id=req_id,
            pod_name=f"runner-{req_id.lower()}",
            pvc_name=f"workspace-{req_id.lower()}",
            pod_phase=self._pod_phase,
            pvc_phase="Bound",
            created_at=None,
        )

    async def ensure_runner(self, req_id: str, *, wait_ready: bool = True,
                            timeout_sec=None, attempts=None) -> str:
        self.ensure_runner_calls.append(req_id)
        return f"runner-{req_id.lower()}"

    async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
        self.exec_calls.append({"req_id": req_id, "command": command})
        if "make accept-env-up" in command:
            return ExecResult(
                exit_code=0,
                stdout='{"endpoint": "http://localhost"}\n',
                stderr="",
                duration_sec=0.5,
            )
        # lite fallback
        return ExecResult(exit_code=0, stdout="PASS\n", stderr="", duration_sec=0.5)


class _FakePool:
    def __init__(self):
        self.ctx_updates: list[dict] = []

    async def execute(self, sql, *args):
        pass

    async def fetchrow(self, sql, *args):
        return None


def _body():
    return type("B", (), {
        "issueId": "src-1", "projectId": "proj",
        "event": "pr-ci.pass", "title": "T",
        "tags": [], "issueNumber": None,
    })()


def _patch(monkeypatch, rc: _FakeRC, pool: _FakePool,
           clone_exit: int | None = None,
           cloned_repos: list[str] | None = None):
    monkeypatch.setattr("orchestrator.actions.create_accept.k8s_runner.get_controller", lambda: rc)
    monkeypatch.setattr("orchestrator.actions.create_accept.db.get_pool", lambda: pool)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.accept_smoke_delay_sec", 0)
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.settings.default_involved_repos", []
    )

    async def fake_ensure_runner_with_clone(req_id, ctx, *, tags, default_repos, branch):
        return (cloned_repos or [], clone_exit)

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.ensure_runner_with_clone",
        fake_ensure_runner_with_clone,
    )

    async def fake_resolve_integration_dir(rc, req_id):
        return ResolveResult(dir="/workspace/source/test")

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.resolve_integration_dir",
        fake_resolve_integration_dir,
    )

    async def fake_update_ctx(p, req_id, updates):
        pool.ctx_updates.append(updates)

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.req_state.update_context",
        fake_update_ctx,
    )


# ─── ERS-S1: pod NotFound → ensure called → proceeds ─────────────────────


@pytest.mark.skip(reason="v0.3-lite fallback removed (PR #547); rewrite for child-agent dispatch")
@pytest.mark.asyncio
async def test_ers_s1_pod_missing_ensures_and_proceeds(monkeypatch):
    """Pod NotFound → ensure_runner_with_clone → continues to env-up → ACCEPT_PASS."""
    rc = _FakeRC(pod_phase="NotFound")
    pool = _FakePool()
    _patch(monkeypatch, rc, pool, clone_exit=None, cloned_repos=["org/repo"])

    out = await mod.create_accept(
        body=_body(),
        req_id="REQ-test-123",
        tags=[],
        ctx={"branch": "feat/REQ-test-123", "cloned_repos": ["org/repo"]},
    )

    assert out.get("emit") == Event.ACCEPT_PASS.value, f"expected ACCEPT_PASS, got {out}"
    # exec_in_runner called for env-up + lite fallback
    assert len(rc.exec_calls) >= 1


@pytest.mark.asyncio
async def test_ers_s1_clone_fail_emits_env_up_fail(monkeypatch):
    """Pod NotFound + clone fails → ACCEPT_ENV_UP_FAIL, ctx records error."""
    rc = _FakeRC(pod_phase="NotFound")
    pool = _FakePool()
    _patch(monkeypatch, rc, pool, clone_exit=1, cloned_repos=["org/repo"])

    out = await mod.create_accept(
        body=_body(),
        req_id="REQ-test-456",
        tags=[],
        ctx={"branch": "feat/REQ-test-456"},
    )

    assert out.get("emit") == Event.ACCEPT_ENV_UP_FAIL.value
    assert "clone failed" in out.get("reason", "")
    fail_ctx = next((u for u in pool.ctx_updates if u.get("accept_result") == "fail"), None)
    assert fail_ctx is not None, "accept_result=fail must be stored in ctx"


# ─── ERS-S2: pod already Running → ensure skipped → proceeds ─────────────


@pytest.mark.skip(reason="v0.3-lite fallback removed (PR #547); rewrite for child-agent dispatch")
@pytest.mark.asyncio
async def test_ers_s2_pod_running_skips_ensure(monkeypatch):
    """Pod already Running → ensure_runner_with_clone NOT called → proceeds normally."""
    rc = _FakeRC(pod_phase="Running")
    pool = _FakePool()

    ensure_called = []

    async def fake_ensure_runner_with_clone(req_id, ctx, *, tags, default_repos, branch):
        ensure_called.append(req_id)
        return ([], None)

    monkeypatch.setattr("orchestrator.actions.create_accept.k8s_runner.get_controller", lambda: rc)
    monkeypatch.setattr("orchestrator.actions.create_accept.db.get_pool", lambda: pool)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.accept_smoke_delay_sec", 0)
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.settings.default_involved_repos", []
    )
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.ensure_runner_with_clone",
        fake_ensure_runner_with_clone,
    )

    async def fake_resolve_integration_dir(rc, req_id):
        return ResolveResult(dir="/workspace/source/test")

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.resolve_integration_dir",
        fake_resolve_integration_dir,
    )

    async def fake_update_ctx(p, req_id, updates):
        pool.ctx_updates.append(updates)

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.req_state.update_context",
        fake_update_ctx,
    )

    out = await mod.create_accept(
        body=_body(),
        req_id="REQ-test-789",
        tags=[],
        ctx={"branch": "feat/REQ-test-789", "cloned_repos": ["org/repo"]},
    )

    assert len(ensure_called) == 0, "ensure_runner_with_clone must NOT be called when pod exists"
    assert out.get("emit") == Event.ACCEPT_PASS.value
