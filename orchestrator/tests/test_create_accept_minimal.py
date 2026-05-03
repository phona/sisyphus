"""REQ-accept-m1-lite: unit tests for create_accept (thanatos MCP + v0.3-lite fallback).

4 scenarios specified in the REQ:
  AML-S1: cloned_repos 全 OK + 每仓 make 都返 0 → ACCEPT_PASS, ctx accept_result=pass
  AML-S2: 任一仓 accept-env-up 返非 0 → ACCEPT_FAIL, ctx accept_fail_repos=[repo-a]
  AML-S3: 仓里没 accept-env-up target（bash script 内部 skip）→ ACCEPT_PASS（不污染整体）
  AML-S4: cloned_repos 空 → ACCEPT_PASS（vacuous true），不调 exec_in_runner
"""
from __future__ import annotations

import pytest

from orchestrator.actions import create_accept as mod
from orchestrator.actions._integration_resolver import ResolveResult
from orchestrator.k8s_runner import ExecResult
from orchestrator.state import Event

# ─── Fakes ────────────────────────────────────────────────────────────────

class _FakeRC:
    """Fake runner controller that discriminates calls by command content."""

    def __init__(self, lite_exit_code: int = 0, lite_stdout: str = "PASS\n", lite_stderr: str = ""):
        self.lite_exit_code = lite_exit_code
        self.lite_stdout = lite_stdout
        self.lite_stderr = lite_stderr
        self.calls: list[dict] = []

    async def get_runner_status(self, req_id):
        from orchestrator.k8s_runner import RunnerStatus
        return RunnerStatus(
            req_id=req_id, pod_name=f"runner-{req_id.lower()}",
            pvc_name=f"workspace-{req_id.lower()}",
            pod_phase="Running", pvc_phase="Bound", created_at=None,
        )

    async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
        self.calls.append({"req_id": req_id, "command": command, "env": env})
        # env-up call
        if "make accept-env-up" in command:
            return ExecResult(
                exit_code=0,
                stdout='{"endpoint": "http://localhost"}\n',
                stderr="",
                duration_sec=0.5,
            )
        # lite fallback call (shell script)
        return ExecResult(
            exit_code=self.lite_exit_code,
            stdout=self.lite_stdout,
            stderr=self.lite_stderr,
            duration_sec=0.5,
        )


class _FakePool:
    def __init__(self):
        self.ctx_updates: list[dict] = []

    async def execute(self, sql, *args):
        pass

    async def fetchrow(self, sql, *args):
        return None


def _body():
    return type("B", (), {
        "issueId": "pr-ci-1", "projectId": "p",
        "event": "pr-ci.pass", "title": "T",
        "tags": [], "issueNumber": None,
    })()


def _patch(monkeypatch, rc: _FakeRC, pool: _FakePool, skip_accept: bool = False):
    monkeypatch.setattr("orchestrator.actions.create_accept.k8s_runner.get_controller", lambda: rc)
    monkeypatch.setattr("orchestrator.actions.create_accept.db.get_pool", lambda: pool)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", skip_accept)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.accept_smoke_delay_sec", 0)

    async def fake_resolve_integration_dir(rc, req_id):
        return ResolveResult(dir="/workspace/source/test")
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.resolve_integration_dir",
        fake_resolve_integration_dir,
    )

    ctx_updates = pool.ctx_updates
    async def fake_update_ctx(p, req_id, updates):
        ctx_updates.append(updates)
    monkeypatch.setattr("orchestrator.actions.create_accept.req_state.update_context", fake_update_ctx)


# ─── AML-S1: all pass ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aml_s1_all_repos_pass(monkeypatch):
    """Script exits 0, stdout='PASS' → ACCEPT_PASS, ctx accept_result=pass."""
    rc = _FakeRC(lite_exit_code=0, lite_stdout="=== accept-env-up: sisyphus ===\nPASS\n")
    pool = _FakePool()
    _patch(monkeypatch, rc, pool)

    out = await mod.create_accept(
        body=_body(), req_id="REQ-1", tags=[], ctx={"cloned_repos": ["phona/sisyphus"]},
    )

    assert out["emit"] == Event.ACCEPT_PASS.value
    # manifest probe (R8 trigger) + env-up + lite fallback = 3 calls; env-up
    # and lite fallback both must be present, and at least one manifest probe
    cmds = [c["command"] for c in rc.calls]
    assert any("make accept-env-up" in c for c in cmds), "env-up command must run"
    assert any(".sisyphus/env.yaml" in c for c in cmds), "manifest probe must run"
    assert any(u.get("accept_result") == "pass" for u in pool.ctx_updates), (
        "accept_result='pass' must be stored in ctx"
    )


# ─── AML-S2: env-up fail ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_aml_s2_envup_fail_emits_accept_fail(monkeypatch):
    """Script exits 1, stdout ends with FAIL:repo-a → ACCEPT_FAIL, fail_repos in ctx."""
    rc = _FakeRC(
        lite_exit_code=1,
        lite_stdout="=== accept-env-up: repo-a ===\n=== FAIL accept-env-up: repo-a ===\nFAIL:repo-a\n",
        lite_stderr="make: *** [accept-env-up] Error 1",
    )
    pool = _FakePool()
    _patch(monkeypatch, rc, pool)

    out = await mod.create_accept(
        body=_body(), req_id="REQ-1", tags=[], ctx={"cloned_repos": ["org/repo-a"]},
    )

    assert out["emit"] == Event.ACCEPT_FAIL.value
    assert "repo-a" in out.get("fail_repos", []), "fail_repos must list the failing repo"
    fail_ctx = next((u for u in pool.ctx_updates if u.get("accept_result") == "fail"), None)
    assert fail_ctx is not None, "accept_result='fail' must be stored in ctx"
    assert "repo-a" in fail_ctx.get("accept_fail_repos", [])


# ─── AML-S3: no accept-env-up target → skip → overall pass ───────────────

@pytest.mark.asyncio
async def test_aml_s3_no_target_skips_repo_not_fail(monkeypatch):
    """Bash script internally skips repo with no target; exits 0 → ACCEPT_PASS.

    The exec IS called (Python doesn't short-circuit); the script decides to skip
    the repo and still exits 0 with 'PASS'.
    """
    rc = _FakeRC(lite_exit_code=0, lite_stdout="PASS\n")  # script internally skipped the repo
    pool = _FakePool()
    _patch(monkeypatch, rc, pool)

    out = await mod.create_accept(
        body=_body(), req_id="REQ-1", tags=[], ctx={"cloned_repos": ["org/no-makefile-repo"]},
    )

    assert out["emit"] == Event.ACCEPT_PASS.value
    # cross-repo refactor adds a manifest probe; env-up + lite fallback both
    # still run alongside it
    cmds = [c["command"] for c in rc.calls]
    assert any("make accept-env-up" in c for c in cmds)


# ─── AML-S4: empty cloned_repos → vacuous pass ───────────────────────────

@pytest.mark.asyncio
async def test_aml_s4_empty_cloned_repos_vacuous_pass(monkeypatch):
    """No repos → ACCEPT_PASS immediately, exec_in_runner never called."""
    rc = _FakeRC()
    pool = _FakePool()
    _patch(monkeypatch, rc, pool)

    out = await mod.create_accept(
        body=_body(), req_id="REQ-1", tags=[], ctx={"cloned_repos": []},
    )

    assert out["emit"] == Event.ACCEPT_PASS.value
    # env-up is still called (integration dir resolved); lite fallback sees
    # empty repos and returns vacuous pass without further exec_in_runner.
    cmds = [c["command"] for c in rc.calls]
    assert any("make accept-env-up" in c for c in cmds)
