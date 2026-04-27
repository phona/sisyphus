"""REQ-415 (thanatos M1): create_accept extracts the optional `thanatos`
block from accept-env-up stdout JSON and passes three render-context fields
(`thanatos_pod`, `thanatos_namespace`, `thanatos_skill_repo`) into the
accept.md.j2 prompt. Block absent → all three are None and the template's
fallback (legacy direct-curl) branch renders.

Coverage: TMW-S1 / TMW-S2 / TMW-S6 (specs/thanatos-mcp-wire/spec.md).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from orchestrator.k8s_runner import ExecResult

# ─── fixtures (shared shape with test_create_accept_self_host.py) ─────────


def _make_body(issue_id="pr-ci-1", project_id="p"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": "session.completed", "title": "T",
        "tags": [], "issueNumber": None,
    })()


def _patch_bkd(monkeypatch, fake):
    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake
    monkeypatch.setattr("orchestrator.actions.create_accept.BKDClient", _ctx)


def _patch_db(monkeypatch):
    class P:
        async def execute(self, sql, *args):
            pass

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr("orchestrator.actions.create_accept.db.get_pool", lambda: P())


class _RC:
    """Stand-in runner controller: scan call returns single source dir,
    env-up call returns whatever JSON the test wants on stdout."""

    def __init__(self, env_up_stdout: str, scan_stdout="S:/workspace/source/sisyphus\n"):
        self.scan_stdout = scan_stdout
        self.env_up_stdout = env_up_stdout
        self.calls: list[dict] = []

    async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
        self.calls.append({"command": command, "env": env})
        if env and env.get("SISYPHUS_STAGE") == "accept-resolve":
            return ExecResult(
                exit_code=0, stdout=self.scan_stdout,
                stderr="", duration_sec=0.1,
            )
        return ExecResult(
            exit_code=0, stdout=self.env_up_stdout,
            stderr="", duration_sec=1.0,
        )


def _capture_render(monkeypatch):
    """Capture the kwargs passed to prompts.render so tests can assert on
    the ctx (thanatos_pod / thanatos_namespace / thanatos_skill_repo)."""
    captured: dict = {}

    def fake_render(template_name, **ctx):
        captured["template_name"] = template_name
        captured["ctx"] = ctx
        return "RENDERED"

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.render", fake_render
    )
    return captured


def _fake_bkd():
    from test_actions_smoke import FakeIssue, make_fake_bkd

    bkd = make_fake_bkd()
    bkd.create_issue = AsyncMock(return_value=FakeIssue(id="acc-1"))
    return bkd


# ─── TMW-S1: thanatos block present → fields plumbed into ctx ─────────────


@pytest.mark.asyncio
async def test_thanatos_block_extracted_into_render_ctx(monkeypatch):
    from orchestrator.actions import create_accept as mod

    fake_bkd = _fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch)
    monkeypatch.setattr(mod.settings, "skip_accept", False)

    captured = _capture_render(monkeypatch)

    rc = _RC(env_up_stdout=(
        '{"endpoint":"http://lab.svc:8080","namespace":"accept-req-9",'
        '"thanatos":{"pod":"thanatos-abc",'
        '"namespace":"accept-req-9","skill_repo":"ttpos-flutter"}}\n'
    ))
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: rc,
    )

    out = await mod.create_accept(
        body=_make_body(), req_id="REQ-9", tags=["pr-ci"], ctx={},
    )

    assert out["accept_issue_id"] == "acc-1"
    assert captured["template_name"] == "accept.md.j2"
    ctx = captured["ctx"]
    assert ctx["thanatos_pod"] == "thanatos-abc"
    assert ctx["thanatos_namespace"] == "accept-req-9"
    assert ctx["thanatos_skill_repo"] == "ttpos-flutter"


# ─── TMW-S2: thanatos block missing → ctx fields are None ─────────────────


@pytest.mark.asyncio
async def test_no_thanatos_block_renders_fallback_ctx(monkeypatch):
    from orchestrator.actions import create_accept as mod

    fake_bkd = _fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch)
    monkeypatch.setattr(mod.settings, "skip_accept", False)

    captured = _capture_render(monkeypatch)

    rc = _RC(env_up_stdout=(
        '{"endpoint":"http://localhost:18000","namespace":"accept-req-9"}\n'
    ))
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: rc,
    )

    await mod.create_accept(
        body=_make_body(), req_id="REQ-9", tags=["pr-ci"], ctx={},
    )

    ctx = captured["ctx"]
    # All three thanatos_* must be None / empty so the template falls back.
    assert ctx["thanatos_pod"] is None
    assert ctx["thanatos_skill_repo"] is None
    # thanatos_namespace defaults to top-level namespace even when block absent
    # — it's still passed but is a no-op since thanatos_pod is None.
    assert ctx["thanatos_namespace"] == "accept-req-9"


# ─── TMW-S6: thanatos block without `namespace` inherits top-level ────────


@pytest.mark.asyncio
async def test_thanatos_namespace_defaults_to_top_level(monkeypatch):
    from orchestrator.actions import create_accept as mod

    fake_bkd = _fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch)
    monkeypatch.setattr(mod.settings, "skip_accept", False)

    captured = _capture_render(monkeypatch)

    rc = _RC(env_up_stdout=(
        '{"endpoint":"http://lab.svc:8080","namespace":"accept-req-9",'
        '"thanatos":{"pod":"thanatos-abc","skill_repo":"ttpos-flutter"}}\n'
    ))
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: rc,
    )

    await mod.create_accept(
        body=_make_body(), req_id="REQ-9", tags=["pr-ci"], ctx={},
    )

    ctx = captured["ctx"]
    assert ctx["thanatos_pod"] == "thanatos-abc"
    # block omitted `namespace` → defaulted to top-level "accept-req-9"
    assert ctx["thanatos_namespace"] == "accept-req-9"
    assert ctx["thanatos_skill_repo"] == "ttpos-flutter"
