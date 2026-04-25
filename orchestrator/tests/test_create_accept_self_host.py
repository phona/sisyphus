"""REQ-self-accept-stage-1777121797: self-host integration dir resolution.

Tests for `_integration_resolver.resolve_integration_dir` and how
`create_accept` / `teardown_accept_env` consume it. Covers four scenarios
from the spec (SDA-S4 through SDA-S7) plus the create_accept happy + fail
paths integrated with the resolver.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from orchestrator.actions._integration_resolver import (
    ResolveResult,
    _decide,
    _parse_scan,
    resolve_integration_dir,
)
from orchestrator.k8s_runner import ExecResult

# ─── Pure logic tests (no async, no mocks) ────────────────────────────────

class TestParseScan:
    def test_empty(self):
        assert _parse_scan("") == ([], [])

    def test_integration_only(self):
        out = "I:/workspace/integration/lab\n"
        assert _parse_scan(out) == (["/workspace/integration/lab"], [])

    def test_source_only(self):
        out = "S:/workspace/source/sisyphus\n"
        assert _parse_scan(out) == ([], ["/workspace/source/sisyphus"])

    def test_mixed_and_whitespace(self):
        out = "\nI:/workspace/integration/lab\n  \nS:/workspace/source/foo\nS:/workspace/source/bar\n"
        i, s = _parse_scan(out)
        assert i == ["/workspace/integration/lab"]
        assert s == ["/workspace/source/foo", "/workspace/source/bar"]

    def test_ignores_unknown_prefix(self):
        out = "X:/some/junk\nI:/workspace/integration/a\n"
        assert _parse_scan(out) == (["/workspace/integration/a"], [])


class TestDecide:
    """SDA-S4..S7 covered here as pure logic."""

    def test_integration_priority_when_present(self):
        # SDA-S4: integration always wins
        d = _decide(["/workspace/integration/lab"], ["/workspace/source/sisyphus"])
        assert d.dir == "/workspace/integration/lab"

    def test_single_source_fallback(self):
        # SDA-S5: integration empty + exactly one source candidate
        d = _decide([], ["/workspace/source/sisyphus"])
        assert d.dir == "/workspace/source/sisyphus"
        assert d.reason == ""

    def test_no_candidates(self):
        # SDA-S6: nothing at all
        d = _decide([], [])
        assert d.dir is None
        assert "no integration dir resolvable" in d.reason

    def test_multiple_source_refuses_to_pick(self):
        # SDA-S7: ambiguous source candidates
        d = _decide([], ["/workspace/source/a", "/workspace/source/b"])
        assert d.dir is None
        assert "multiple source candidates" in d.reason
        assert "/workspace/source/a" in d.reason
        assert "/workspace/source/b" in d.reason

    def test_first_integration_when_multiple(self):
        # multiple integration candidates → take first (matches old shell glob)
        d = _decide(["/workspace/integration/a", "/workspace/integration/b"], [])
        assert d.dir == "/workspace/integration/a"


# ─── resolve_integration_dir end-to-end (against fake controller) ─────────

class _FakeRC:
    """Stand-in for k8s_runner.RunnerController; records exec calls."""

    def __init__(self, stdout: str = "", exit_code: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.exit_code = exit_code
        self.stderr = stderr
        self.calls: list[dict] = []

    async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
        self.calls.append({
            "req_id": req_id, "command": command, "env": env,
            "timeout_sec": timeout_sec,
        })
        return ExecResult(
            exit_code=self.exit_code, stdout=self.stdout,
            stderr=self.stderr, duration_sec=0.1,
        )


@pytest.mark.asyncio
async def test_resolve_returns_integration_when_present():
    rc = _FakeRC(stdout="I:/workspace/integration/lab\nS:/workspace/source/sisyphus\n")
    result = await resolve_integration_dir(rc, "REQ-9")
    assert isinstance(result, ResolveResult)
    assert result.dir == "/workspace/integration/lab"


@pytest.mark.asyncio
async def test_resolve_falls_back_to_single_source():
    rc = _FakeRC(stdout="S:/workspace/source/sisyphus\n")
    result = await resolve_integration_dir(rc, "REQ-9")
    assert result.dir == "/workspace/source/sisyphus"


@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_candidates():
    rc = _FakeRC(stdout="")
    result = await resolve_integration_dir(rc, "REQ-9")
    assert result.dir is None
    assert "no integration dir resolvable" in result.reason


@pytest.mark.asyncio
async def test_resolve_returns_none_when_ambiguous_sources():
    rc = _FakeRC(stdout="S:/workspace/source/a\nS:/workspace/source/b\n")
    result = await resolve_integration_dir(rc, "REQ-9")
    assert result.dir is None
    assert "multiple source candidates" in result.reason


@pytest.mark.asyncio
async def test_resolve_handles_scan_nonzero_exit():
    rc = _FakeRC(stdout="", exit_code=2, stderr="oops")
    result = await resolve_integration_dir(rc, "REQ-9")
    assert result.dir is None
    assert "exit_code=2" in result.reason


@pytest.mark.asyncio
async def test_resolve_only_one_exec_call():
    """Resolver must do its work in a single kubectl exec round-trip."""
    rc = _FakeRC(stdout="I:/workspace/integration/lab\n")
    await resolve_integration_dir(rc, "REQ-9")
    assert len(rc.calls) == 1


# ─── create_accept integrated with resolver ──────────────────────────────

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


def _patch_db(monkeypatch, target_module: str):
    class P:
        async def execute(self, sql, *args):
            pass

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(f"orchestrator.actions.{target_module}.db.get_pool", lambda: P())


class _RC:
    """Returns scan output then env-up output across calls."""

    def __init__(self, scan_stdout: str, env_up_stdout: str = "",
                 env_up_exit: int = 0):
        self.scan_stdout = scan_stdout
        self.env_up_stdout = env_up_stdout
        self.env_up_exit = env_up_exit
        self.calls: list[dict] = []

    async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
        self.calls.append({"command": command, "env": env})
        # Scan call has env={"SISYPHUS_STAGE": "accept-resolve"}
        if env and env.get("SISYPHUS_STAGE") == "accept-resolve":
            return ExecResult(
                exit_code=0, stdout=self.scan_stdout,
                stderr="", duration_sec=0.1,
            )
        return ExecResult(
            exit_code=self.env_up_exit, stdout=self.env_up_stdout,
            stderr="", duration_sec=1.0,
        )


@pytest.mark.asyncio
async def test_create_accept_self_host_fallback(monkeypatch):
    """integration empty + single source repo with target → fallback to source dir."""
    from test_actions_smoke import FakeIssue, make_fake_bkd

    from orchestrator.actions import create_accept as mod

    fake_bkd = make_fake_bkd()
    fake_bkd.create_issue.return_value = FakeIssue(id="acc-1")
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch, "create_accept")
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)

    rc = _RC(
        scan_stdout="S:/workspace/source/sisyphus\n",
        env_up_stdout='{"endpoint":"http://localhost:18000","namespace":"accept-req-9"}\n',
    )
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: rc,
    )

    out = await mod.create_accept(
        body=_make_body(), req_id="REQ-9", tags=["pr-ci"], ctx={},
    )

    assert out["accept_issue_id"] == "acc-1"
    assert out["endpoint"] == "http://localhost:18000"
    # Verify the env-up call used the fallback source dir
    env_up_calls = [c for c in rc.calls if c["env"] and c["env"].get("SISYPHUS_STAGE") == "accept-env-up"]
    assert len(env_up_calls) == 1
    assert "cd /workspace/source/sisyphus && make ci-accept-env-up" in env_up_calls[0]["command"]


@pytest.mark.asyncio
async def test_create_accept_no_resolvable_dir_emits_envup_fail(monkeypatch):
    """integration empty + no source repo with target → emit accept-env-up.fail with reason."""
    from test_actions_smoke import make_fake_bkd

    from orchestrator.actions import create_accept as mod

    fake_bkd = make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch, "create_accept")
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)

    rc = _RC(scan_stdout="")
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: rc,
    )

    out = await mod.create_accept(
        body=_make_body(), req_id="REQ-9", tags=["pr-ci"], ctx={},
    )

    assert out["emit"] == "accept-env-up.fail"
    assert "no integration dir resolvable" in out["reason"]
    # No env-up call should have been issued (the scan call exists, but no env-up)
    env_up_calls = [c for c in rc.calls if c["env"] and c["env"].get("SISYPHUS_STAGE") == "accept-env-up"]
    assert len(env_up_calls) == 0
    # No BKD agent dispatched
    fake_bkd.create_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_accept_ambiguous_source_emits_envup_fail(monkeypatch):
    """integration empty + multiple source repos with target → fail (refuse to pick)."""
    from test_actions_smoke import make_fake_bkd

    from orchestrator.actions import create_accept as mod

    fake_bkd = make_fake_bkd()
    _patch_bkd(monkeypatch, fake_bkd)
    _patch_db(monkeypatch, "create_accept")
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)

    rc = _RC(scan_stdout="S:/workspace/source/a\nS:/workspace/source/b\n")
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: rc,
    )

    out = await mod.create_accept(
        body=_make_body(), req_id="REQ-9", tags=["pr-ci"], ctx={},
    )

    assert out["emit"] == "accept-env-up.fail"
    assert "multiple source candidates" in out["reason"]
    fake_bkd.create_issue.assert_not_awaited()


# ─── teardown_accept_env integrated with resolver ─────────────────────────

@pytest.mark.asyncio
async def test_teardown_uses_resolved_dir(monkeypatch):
    """teardown re-resolves and uses the same dir as create_accept would have."""
    from orchestrator.actions import teardown_accept_env as mod
    from orchestrator.config import settings

    monkeypatch.setattr(settings, "skip_accept", False)
    monkeypatch.setattr(settings, "test_mode", False)
    _patch_db(monkeypatch, "teardown_accept_env")

    rc = _RC(
        scan_stdout="S:/workspace/source/sisyphus\n",
        env_up_stdout="",  # env-down call output unused
    )
    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.k8s_runner.get_controller",
        lambda: rc,
    )

    out = await mod.teardown_accept_env(
        body=_make_body(), req_id="REQ-9",
        tags=["accept", "REQ-9", "result:pass"], ctx={},
    )

    assert out["emit"] == "teardown-done.pass"
    assert out["accept_result"] == "pass"
    assert out["env_down_ok"] is True
    # Verify teardown ran in the resolved source dir
    down_calls = [c for c in rc.calls if c["env"] and c["env"].get("SISYPHUS_STAGE") == "accept-teardown"]
    assert len(down_calls) == 1
    assert "cd /workspace/source/sisyphus && make ci-accept-env-down" in down_calls[0]["command"]


@pytest.mark.asyncio
async def test_teardown_skips_env_down_when_no_dir(monkeypatch):
    """No resolvable dir → teardown skips env-down (best-effort) but still emits next event."""
    from orchestrator.actions import teardown_accept_env as mod
    from orchestrator.config import settings

    monkeypatch.setattr(settings, "skip_accept", False)
    monkeypatch.setattr(settings, "test_mode", False)
    _patch_db(monkeypatch, "teardown_accept_env")

    rc = _RC(scan_stdout="")
    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.k8s_runner.get_controller",
        lambda: rc,
    )

    out = await mod.teardown_accept_env(
        body=_make_body(), req_id="REQ-9",
        tags=["accept", "REQ-9", "result:fail"], ctx={},
    )

    assert out["emit"] == "teardown-done.fail"
    assert out["accept_result"] == "fail"
    assert out["env_down_ok"] is False
    # Only the scan call ran; no teardown command
    down_calls = [c for c in rc.calls if c["env"] and c["env"].get("SISYPHUS_STAGE") == "accept-teardown"]
    assert len(down_calls) == 0
