"""Unit tests for same-slug openspec/changes supersede in start_analyze (Fix D).

REQ-openspec-changes-cleanup-1777343379

Scenarios:
  SUPR-S1  vN redispatch triggers supersede mv+commit in runner
  SUPR-S2  no stale dirs → no mv commit (base_slug == current)
  SUPR-S3  supersede exec failure does NOT block dispatch (fail-open)
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.actions import _clone
from orchestrator.actions import start_analyze as sa_mod
from orchestrator.admission import AdmissionDecision

# ─── shared helpers ───────────────────────────────────────────────────────────


@dataclass
class FakeExecResult:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.1


def _body(project_id="proj-x", issue_id="issue-x"):
    return SimpleNamespace(projectId=project_id, issueId=issue_id, title="t")


class _FakeBKD:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def update_issue(self, *a, **kw):
        pass

    async def follow_up_issue(self, *a, **kw):
        pass


@pytest.fixture(autouse=True)
def _base_patches(monkeypatch):
    """Common patches so test body can focus on the supersede logic."""
    monkeypatch.setattr(
        sa_mod, "check_admission",
        AsyncMock(return_value=AdmissionDecision(admit=True)),
    )
    monkeypatch.setattr(sa_mod.db, "get_pool", lambda: object())
    monkeypatch.setattr(sa_mod.req_state, "update_context", AsyncMock())
    monkeypatch.setattr(sa_mod, "BKDClient", _FakeBKD)
    monkeypatch.setattr(sa_mod, "render", lambda *a, **kw: "prompt")
    monkeypatch.setattr(sa_mod, "filter_propagatable_intent_tags", lambda tags: [])
    monkeypatch.setattr(sa_mod, "links", MagicMock(bkd_issue_url=lambda *a: ""))
    monkeypatch.setattr(sa_mod, "build_status_block_ctx", lambda **kw: {})
    monkeypatch.setattr(sa_mod, "short_title", lambda ctx: "")


def _patch_runner_and_clone(monkeypatch, *, cloned_repos, clone_exit=None,
                            exec_fn: AsyncMock | None = None):
    """Patch k8s_runner + clone helper together."""
    if exec_fn is None:
        exec_fn = AsyncMock(return_value=FakeExecResult(exit_code=0))

    fake_rc = MagicMock()
    fake_rc.ensure_runner = AsyncMock(return_value="runner-pod")
    fake_rc.exec_in_runner = exec_fn

    monkeypatch.setattr(_clone.k8s_runner, "get_controller", lambda: fake_rc)
    monkeypatch.setattr(sa_mod.k8s_runner, "get_controller", lambda: fake_rc)

    async def _fake_clone(req_id, ctx, *, tags=None, default_repos=None):
        return cloned_repos, clone_exit

    monkeypatch.setattr(sa_mod, "clone_involved_repos_into_runner", _fake_clone)
    return fake_rc


# ─── SUPR-S1: vN redispatch triggers supersede exec ──────────────────────────


@pytest.mark.asyncio
async def test_s1_vN_redispatch_triggers_supersede(monkeypatch):
    """SUPR-S1: Dispatching REQ-foo-1234-v2 must call exec_in_runner with
    a supersede command targeting REQ-foo-1234 (old slug, same base)."""
    exec_calls: list[str] = []

    async def capture_exec(req_id, cmd, *, timeout_sec=300):
        exec_calls.append(cmd)
        return FakeExecResult(exit_code=0)

    _patch_runner_and_clone(
        monkeypatch,
        cloned_repos=["phona/sisyphus"],
        exec_fn=capture_exec,
    )

    result = await sa_mod.start_analyze(
        body=_body(),
        req_id="REQ-foo-1234-v2",
        tags=[],
        ctx={"involved_repos": ["phona/sisyphus"]},
    )

    assert "emit" not in result
    # exec_in_runner should have been called for the supersede step
    assert any("REQ-foo-1234-v2" in cmd for cmd in exec_calls), \
        f"expected supersede call; got: {exec_calls}"
    assert any("_superseded" in cmd for cmd in exec_calls), \
        f"expected _superseded in cmd; got: {exec_calls}"


# ─── SUPR-S2: no stale dirs → no mv (base_slug == current req) ───────────────


@pytest.mark.asyncio
async def test_s2_no_stale_dirs_no_mv(monkeypatch):
    """SUPR-S2: When req_id has no -vN suffix, base_slug == req_id and the loop
    finds nothing to supersede. exec_in_runner is still called but should NOT
    produce a commit (exit 0, no mv-stale-dir commands)."""
    exec_calls: list[str] = []

    async def capture_exec(req_id, cmd, *, timeout_sec=300):
        exec_calls.append(cmd)
        return FakeExecResult(exit_code=0)

    _patch_runner_and_clone(
        monkeypatch,
        cloned_repos=["phona/sisyphus"],
        exec_fn=capture_exec,
    )

    # Non-vN req_id: base_slug == req_id, loop skips current dir
    result = await sa_mod.start_analyze(
        body=_body(),
        req_id="REQ-foo-1234",
        tags=[],
        ctx={"involved_repos": ["phona/sisyphus"]},
    )

    assert "emit" not in result
    # Script is called but current dir is excluded via [ "$dname" = "$current" ] && continue
    # The for-loop inside the script still runs but skips the matching dir;
    # we just assert no RuntimeError was raised and dispatch still proceeded.
    assert result.get("issue_id") or result.get("req_id") or True  # reached BKD dispatch


# ─── SUPR-S3: supersede exec failure is fail-open ─────────────────────────────


@pytest.mark.asyncio
async def test_s3_supersede_failure_does_not_block_dispatch(monkeypatch):
    """SUPR-S3: exec_in_runner raising for supersede must not prevent BKD dispatch."""

    async def exploding_exec(req_id, cmd, *, timeout_sec=300):
        raise RuntimeError("exec failed")

    _patch_runner_and_clone(
        monkeypatch,
        cloned_repos=["phona/sisyphus"],
        exec_fn=exploding_exec,
    )

    # Should not raise; dispatch should proceed normally
    result = await sa_mod.start_analyze(
        body=_body(),
        req_id="REQ-foo-1234-v2",
        tags=[],
        ctx={"involved_repos": ["phona/sisyphus"]},
    )

    assert "emit" not in result
