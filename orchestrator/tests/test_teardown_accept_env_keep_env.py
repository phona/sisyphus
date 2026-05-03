"""REQ-feat-accept-env-substep-timing: KEEP_ENV plumbing in teardown_accept_env.

Covers spec scenarios KEEP-S1..S3 from
openspec/changes/REQ-feat-accept-env-substep-timing-1777812776/specs/
accept-env-observability/spec.md.
"""
from __future__ import annotations

import pytest

from orchestrator.actions import teardown_accept_env as mod
from orchestrator.actions._integration_resolver import ResolveResult
from orchestrator.k8s_runner import ExecResult
from orchestrator.state import Event


class _FakeRC:
    def __init__(self):
        self.calls: list[dict] = []

    async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
        self.calls.append({"req_id": req_id, "command": command, "env": env})
        return ExecResult(exit_code=0, stdout="", stderr="", duration_sec=0.1)


def _body():
    return type("B", (), {
        "issueId": "i-1", "projectId": "p", "event": "accept.pass",
        "title": "T", "tags": [], "issueNumber": None,
    })()


def _patch(monkeypatch, rc, *, accept_keep_env: bool, accept_result: str = "pass"):
    monkeypatch.setattr("orchestrator.actions.teardown_accept_env.k8s_runner.get_controller", lambda: rc)
    monkeypatch.setattr("orchestrator.actions.teardown_accept_env.settings.accept_keep_env", accept_keep_env)
    monkeypatch.setattr("orchestrator.actions.teardown_accept_env.settings.skip_accept", False)
    monkeypatch.setattr("orchestrator.actions.teardown_accept_env.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions.teardown_accept_env.db.get_pool", lambda: object())

    async def fake_resolve(_rc, _req_id):
        return ResolveResult(dir="/workspace/source/test")
    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.resolve_integration_dir",
        fake_resolve,
    )

    async def fake_update_ctx(_pool, _req_id, _updates):
        pass
    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.req_state.update_context",
        fake_update_ctx,
    )


# ─── KEEP-S1: flag on → KEEP_ENV=1 in env ──────────────────────────────────

@pytest.mark.asyncio
async def test_keep_s1_flag_on_injects_keep_env_1(monkeypatch):
    rc = _FakeRC()
    _patch(monkeypatch, rc, accept_keep_env=True)

    await mod.teardown_accept_env(
        body=_body(), req_id="REQ-1", tags=["accept", "result:pass"],
        ctx={"accept_result": "pass"},
    )
    assert len(rc.calls) == 1
    env = rc.calls[0]["env"]
    assert env.get("KEEP_ENV") == "1"
    # base env still present
    assert env.get("SISYPHUS_REQ_ID") == "REQ-1"
    assert env.get("SISYPHUS_STAGE") == "accept-teardown"


# ─── KEEP-S2: flag off (default) → KEEP_ENV not in env ─────────────────────

@pytest.mark.asyncio
async def test_keep_s2_flag_off_omits_keep_env(monkeypatch):
    rc = _FakeRC()
    _patch(monkeypatch, rc, accept_keep_env=False)

    await mod.teardown_accept_env(
        body=_body(), req_id="REQ-1", tags=["accept", "result:pass"],
        ctx={"accept_result": "pass"},
    )
    assert len(rc.calls) == 1
    env = rc.calls[0]["env"]
    assert "KEEP_ENV" not in env
    assert env.get("SISYPHUS_REQ_ID") == "REQ-1"


# ─── KEEP-S3: emit routing unaffected by KEEP_ENV ──────────────────────────

@pytest.mark.asyncio
async def test_keep_s3_pass_routing_with_keep_env(monkeypatch):
    rc = _FakeRC()
    _patch(monkeypatch, rc, accept_keep_env=True)

    out = await mod.teardown_accept_env(
        body=_body(), req_id="REQ-1", tags=["accept", "result:pass"],
        ctx={"accept_result": "pass"},
    )
    assert out["emit"] == Event.TEARDOWN_DONE_PASS.value


@pytest.mark.asyncio
async def test_keep_s3_fail_routing_with_keep_env(monkeypatch):
    rc = _FakeRC()
    _patch(monkeypatch, rc, accept_keep_env=True)

    out = await mod.teardown_accept_env(
        body=_body(), req_id="REQ-1", tags=["accept", "result:fail"],
        ctx={"accept_result": "fail"},
    )
    assert out["emit"] == Event.TEARDOWN_DONE_FAIL.value
