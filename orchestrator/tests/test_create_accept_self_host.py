"""REQ-self-accept-stage-1777121797: self-host integration dir resolution.

Tests for `_integration_resolver.resolve_integration_dir` and how
`teardown_accept_env` consumes it.  The v0.3-lite rewrite of `create_accept`
no longer uses `_integration_resolver` (it iterates /workspace/source/*/ via
shell script), so those integration tests were removed.  Resolver unit tests
and teardown integration tests remain valid.
"""
from __future__ import annotations

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
    """SDA-S4..S7 + SDA-S10 covered here as pure logic."""

    def test_source_priority_when_single_source_present(self):
        # SDA-S4: a single source candidate wins even when integration also has one
        d = _decide(["/workspace/integration/lab"], ["/workspace/source/sisyphus"])
        assert d.dir == "/workspace/source/sisyphus"

    def test_single_source_primary(self):
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
        # SDA-S7: ambiguous source candidates with no explicit integration
        d = _decide([], ["/workspace/source/a", "/workspace/source/b"])
        assert d.dir is None
        assert "multiple source candidates" in d.reason
        assert "/workspace/source/a" in d.reason
        assert "/workspace/source/b" in d.reason

    def test_integration_breaks_tie_for_multiple_sources(self):
        # SDA-S10: explicit integration dir disambiguates multi-source case
        d = _decide(
            ["/workspace/integration/lab"],
            ["/workspace/source/a", "/workspace/source/b"],
        )
        assert d.dir == "/workspace/integration/lab"

    def test_integration_used_when_source_empty(self):
        # legacy / explicit-only path: source has no candidate, integration takes over
        d = _decide(["/workspace/integration/lab"], [])
        assert d.dir == "/workspace/integration/lab"

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
async def test_resolve_returns_source_when_single_source_present():
    # Source-first: single source candidate wins even when integration also has one.
    rc = _FakeRC(stdout="I:/workspace/integration/lab\nS:/workspace/source/sisyphus\n")
    result = await resolve_integration_dir(rc, "REQ-9")
    assert isinstance(result, ResolveResult)
    assert result.dir == "/workspace/source/sisyphus"


@pytest.mark.asyncio
async def test_resolve_uses_single_source_primary():
    rc = _FakeRC(stdout="S:/workspace/source/sisyphus\n")
    result = await resolve_integration_dir(rc, "REQ-9")
    assert result.dir == "/workspace/source/sisyphus"


@pytest.mark.asyncio
async def test_resolve_uses_integration_when_source_ambiguous():
    rc = _FakeRC(
        stdout=(
            "I:/workspace/integration/lab\n"
            "S:/workspace/source/a\nS:/workspace/source/b\n"
        )
    )
    result = await resolve_integration_dir(rc, "REQ-9")
    assert result.dir == "/workspace/integration/lab"


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


# ─── helpers for teardown integration tests ──────────────────────────────

def _make_body(issue_id="pr-ci-1", project_id="p"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": "session.completed", "title": "T",
        "tags": [], "issueNumber": None,
    })()


def _patch_db(monkeypatch, target_module: str):
    class P:
        async def execute(self, sql, *args):
            pass

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(f"orchestrator.actions.{target_module}.db.get_pool", lambda: P())


class _RC:
    """Fake runner: returns scan output on accept-resolve call, env-down output otherwise."""

    def __init__(self, scan_stdout: str, env_up_stdout: str = "", env_up_exit: int = 0):
        self.scan_stdout = scan_stdout
        self.env_up_stdout = env_up_stdout
        self.env_up_exit = env_up_exit
        self.calls: list[dict] = []

    async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
        self.calls.append({"command": command, "env": env})
        if env and env.get("SISYPHUS_STAGE") == "accept-resolve":
            return ExecResult(exit_code=0, stdout=self.scan_stdout, stderr="", duration_sec=0.1)
        return ExecResult(exit_code=self.env_up_exit, stdout=self.env_up_stdout, stderr="", duration_sec=1.0)


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
    assert "cd /workspace/source/sisyphus && make accept-env-down" in down_calls[0]["command"]


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
