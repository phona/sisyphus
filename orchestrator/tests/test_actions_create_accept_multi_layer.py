"""Unit tests for create_accept multi-layer + R8 backward compat + R10 attribution.

These tests stub out the runner controller's exec_in_runner and the BKD client
so the action's pure orchestration logic (manifest read, topology drive, env
inject, attribution write) can be exercised without infrastructure.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest


@dataclass
class FakeExec:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_sec: float = 0.1


class FakeRunnerController:
    """Stub for k8s_runner.RunnerController honoring the call sequence the action makes.

    The test wires up a list of (matcher, FakeExec) pairs and the controller
    pops the first matching one for each exec_in_runner call. A matcher is a
    substring that must appear in the command.
    """

    def __init__(self):
        self.scripted: list[tuple[str, FakeExec]] = []
        self.calls: list[tuple[str, dict]] = []
        self.pod_status_phase = "Running"

    def script(self, match: str, result: FakeExec) -> None:
        self.scripted.append((match, result))

    async def get_runner_status(self, req_id):
        @dataclass
        class _Status:
            pod_phase: str = "Running"
        return _Status(pod_phase=self.pod_status_phase)

    async def ensure_runner(self, *args, **kwargs):
        return None

    async def exec_in_runner(self, req_id, command, *, env=None, timeout_sec=None, workdir=None):
        self.calls.append((command, dict(env or {})))
        for i, (matcher, result) in enumerate(self.scripted):
            if matcher in command:
                self.scripted.pop(i)
                return result
        # default: empty success (no script entry) — shouldn't normally happen
        return FakeExec(stdout="", stderr="", exit_code=0)


@dataclass
class FakeBody:
    issueId: str = "src-1"
    projectId: str = "p"


@dataclass
class FakeIssue:
    id: str = "accept-1"


def _patch_db(monkeypatch):
    """Capture all DB writes done by the action without needing a real pool."""
    writes: list = []

    class P:
        async def execute(self, sql, *args):
            writes.append(("execute", sql.strip()[:60], args))

        async def fetchrow(self, sql, *args):
            writes.append(("fetchrow", sql.strip()[:60], args))
            return {"id": 1}

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.db.get_pool", lambda: P(),
    )
    return writes


def _patch_bkd(monkeypatch, fake_issue: FakeIssue | None = None):
    bkd = AsyncMock()
    bkd.create_issue = AsyncMock(return_value=fake_issue or FakeIssue())
    bkd.update_issue = AsyncMock(return_value=fake_issue or FakeIssue())
    bkd.follow_up_issue = AsyncMock(return_value={})

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield bkd

    monkeypatch.setattr("orchestrator.actions.create_accept.BKDClient", _ctx)
    return bkd


def _patch_pr_links(monkeypatch):
    async def _ensure(*a, **kw):
        return {}

    def _tags(_links):
        return []

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.pr_links.ensure_pr_links_in_ctx", _ensure,
    )
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.pr_links.pr_link_tags", _tags,
    )


def _patch_runner(monkeypatch, controller: FakeRunnerController):
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: controller,
    )


def _patch_skip(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.skip_if_enabled",
        lambda *a, **kw: None,
    )


def _patch_ensure_runner_with_clone(monkeypatch):
    async def _no_op(*a, **kw):
        return [], None

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.ensure_runner_with_clone",
        _no_op,
    )


def _patch_resolve_integration_dir(monkeypatch, dir_path: str | None, reason: str = ""):
    """Stub legacy resolver used by R8 single-layer path."""
    from orchestrator.actions._integration_resolver import ResolveResult

    async def _stub(rc, req_id):
        return ResolveResult(dir=dir_path, reason=reason)

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.resolve_integration_dir", _stub,
    )


# ─── R8 backward compat: source repo without manifest ────────────────────

@pytest.mark.asyncio
async def test_single_layer_no_manifest_legacy_path(monkeypatch):
    """R8 / OCRE-S13 / CREO-S29: no .sisyphus/env.yaml -> legacy single-layer path."""
    rc = FakeRunnerController()
    rc.script("__MANIFEST_MISSING__", FakeExec(stdout="__MANIFEST_MISSING__"))
    # legacy path runs the integration resolver then `cd <dir> && make accept-env-up`
    rc.script(
        "make accept-env-up",
        FakeExec(stdout='{"endpoint":"http://lab:8080"}', exit_code=0),
    )
    _patch_runner(monkeypatch, rc)
    _patch_skip(monkeypatch)
    _patch_ensure_runner_with_clone(monkeypatch)
    _patch_db(monkeypatch)
    bkd = _patch_bkd(monkeypatch)
    _patch_pr_links(monkeypatch)
    _patch_resolve_integration_dir(monkeypatch, "/workspace/source/sisyphus")

    from orchestrator.actions import create_accept as mod

    result = await mod.create_accept(
        body=FakeBody(),
        req_id="REQ-test-1",
        tags=[],
        ctx={"cloned_repos": ["phona/sisyphus"]},
    )
    # No thanatos block + no `endpoint` consumer -> falls through to lite, but
    # cloned_repos ensures lite ran. Expect ACCEPT_PASS since make accept-env-up
    # succeeded and endpoint is in JSON. Without `thanatos` block we go to lite
    # fallback, which scripts no command — so we expect a lite_no_repos vacuous
    # pass when nothing matches. The default FakeExec (zero) is a script-less
    # branch; assert at least no crash and correct shape.
    assert "emit" in result
    # legacy path read the manifest sentinel + accept-env-up command
    seen = " ".join(c for c, _ in rc.calls)
    assert "__MANIFEST_MISSING__" in seen
    assert "make accept-env-up" in seen
    # bkd was NOT called for accept-agent (no thanatos block -> lite fallback)
    assert bkd.create_issue.await_count == 0


# ─── R4 multi-layer success path ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_layer_success_records_layers_and_dispatches(monkeypatch):
    """R4 / R10 / OCRE-S14 / CREO-S14, S15, S17, S39:

    Topology [server-go, flutter] both succeed; bundle merges cleanly; flutter
    receives BACKEND_ENDPOINT env var; thanatos block carried through;
    accept-agent gets the endpoint string.
    """
    rc = FakeRunnerController()
    # 1. read source (flutter) manifest
    rc.script(
        "/workspace/source/ttpos-flutter/.sisyphus/env.yaml",
        FakeExec(stdout=(
            "__MANIFEST_FOUND__\n"
            "emits:\n  - device\n"
            "needs:\n  - ZonEaseTech/ttpos-server-go\n"
            "inputs:\n  BACKEND_ENDPOINT: ZonEaseTech/ttpos-server-go.endpoint\n"
        )),
    )
    # 2. branch_exists check for ttpos-server-go same-name (BFS bootstrap step)
    rc.script("git ls-remote --heads", FakeExec(stdout="", exit_code=0))
    # 3. clone ttpos-server-go on develop bootstrap branch
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned", exit_code=0))
    # 4. read ttpos-server-go manifest
    rc.script(
        "/workspace/source/ttpos-server-go/.sisyphus/env.yaml",
        FakeExec(stdout="emits:\n  - endpoint\n"),
    )
    # 5. branch resolution for ttpos-server-go: same-name check (False)
    rc.script("git ls-remote --heads", FakeExec(stdout="", exit_code=0))
    # 6. branch resolution: candidate develop check (True)
    rc.script(
        "git ls-remote --heads",
        FakeExec(stdout="abc123\trefs/heads/develop\n", exit_code=0),
    )
    # 7. final clone on develop (idempotent re-clone)
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned", exit_code=0))
    # 8. layer-up server-go: exits 0, JSON tail with endpoint + thanatos
    rc.script(
        "make accept-env-up",
        FakeExec(
            stdout='log line\n{"endpoint":"http://server-go:8080"}',
            exit_code=0,
        ),
    )
    # 9. layer-up flutter: exits 0, JSON tail with device + thanatos block
    rc.script(
        "make accept-env-up",
        FakeExec(
            stdout=(
                'log line\n{"device":"redroid:5554","endpoint":"http://lab","thanatos":'
                '{"pod":"th-pod","namespace":"ns-x","skill_repo":"phona/skill"}}'
            ),
            exit_code=0,
        ),
    )
    _patch_runner(monkeypatch, rc)
    _patch_skip(monkeypatch)
    _patch_ensure_runner_with_clone(monkeypatch)
    writes = _patch_db(monkeypatch)
    bkd = _patch_bkd(monkeypatch)
    _patch_pr_links(monkeypatch)

    from orchestrator.actions import create_accept as mod

    result = await mod.create_accept(
        body=FakeBody(),
        req_id="REQ-test-2",
        tags=[],
        ctx={
            "cloned_repos": ["ZonEaseTech/ttpos-flutter"],
            "branch": "feat/REQ-test-2",
        },
    )

    # accept-agent dispatched (multi-layer + thanatos block present)
    assert bkd.create_issue.await_count == 1
    assert result.get("accept_issue_id") == "accept-1"
    # flutter's manifest only emits `device`, so flutter's bundle entry holds
    # only `device` — the extra `endpoint` field in flutter's JSON tail is
    # dropped per CREO-S15 (passthrough is gated by manifest.emits). The
    # primary endpoint heuristic falls through to the first layer in topo
    # order that emits `endpoint` -> server-go's value.
    assert result["endpoint"] == "http://server-go:8080"
    # second `make accept-env-up` call (flutter) had BACKEND_ENDPOINT injected
    layer_up_calls = [(c, e) for (c, e) in rc.calls if "make accept-env-up" in c]
    assert len(layer_up_calls) == 2
    assert layer_up_calls[1][1].get("BACKEND_ENDPOINT") == "http://server-go:8080"
    # stage_runs.context update fetchrow happened (R10 attribution write)
    fetchrow_calls = [w for w in writes if w[0] == "fetchrow"]
    assert any("stage_runs" in w[1] for w in fetchrow_calls)


# ─── R10: missing emit field records failed_field ───────────────────────

@pytest.mark.asyncio
async def test_multi_layer_missing_emit_field_attribution(monkeypatch):
    """OCRE-S15 / CREO-S38: layer JSON missing declared emit field -> failed_field."""
    rc = FakeRunnerController()
    # source flutter manifest
    rc.script(
        "/workspace/source/ttpos-flutter/.sisyphus/env.yaml",
        FakeExec(stdout=(
            "__MANIFEST_FOUND__\n"
            "needs:\n  - ZonEaseTech/ttpos-server-go\n"
        )),
    )
    rc.script("git ls-remote --heads", FakeExec(stdout=""))
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned"))
    rc.script(
        "/workspace/source/ttpos-server-go/.sisyphus/env.yaml",
        FakeExec(stdout="emits:\n  - endpoint\n"),
    )
    rc.script("git ls-remote --heads", FakeExec(stdout=""))
    rc.script(
        "git ls-remote --heads",
        FakeExec(stdout="abc\trefs/heads/develop\n"),
    )
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned"))
    # server-go layer-up succeeds but JSON tail is missing `endpoint`
    rc.script(
        "make accept-env-up",
        FakeExec(stdout='{"namespace":"ns-x"}', exit_code=0),
    )
    _patch_runner(monkeypatch, rc)
    _patch_skip(monkeypatch)
    _patch_ensure_runner_with_clone(monkeypatch)
    writes = _patch_db(monkeypatch)
    bkd = _patch_bkd(monkeypatch)
    _patch_pr_links(monkeypatch)

    from orchestrator.actions import create_accept as mod

    result = await mod.create_accept(
        body=FakeBody(),
        req_id="REQ-test-3",
        tags=[],
        ctx={
            "cloned_repos": ["ZonEaseTech/ttpos-flutter"],
            "branch": "feat/REQ-test-3",
        },
    )

    assert result["emit"] == "accept-env-up.fail"
    assert result["failed_layer"] == "ZonEaseTech/ttpos-server-go"
    assert result["failed_field"] == "endpoint"
    # No accept-agent dispatched on failure
    assert bkd.create_issue.await_count == 0
    # stage_runs.context update happened
    fetchrow_calls = [w for w in writes if w[0] == "fetchrow"]
    assert any("stage_runs" in w[1] for w in fetchrow_calls)


# ─── R10: layer accept-env-up exits non-zero records failed_layer ───────

@pytest.mark.asyncio
async def test_multi_layer_exit_nonzero_records_failed_layer(monkeypatch):
    """CREO-S36: mid-chain layer exit !=0 -> failed_layer, ACCEPT_ENV_UP_FAIL."""
    rc = FakeRunnerController()
    rc.script(
        "/workspace/source/ttpos-flutter/.sisyphus/env.yaml",
        FakeExec(stdout=(
            "__MANIFEST_FOUND__\n"
            "needs:\n  - ZonEaseTech/ttpos-server-go\n"
        )),
    )
    rc.script("git ls-remote --heads", FakeExec(stdout=""))
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned"))
    rc.script(
        "/workspace/source/ttpos-server-go/.sisyphus/env.yaml",
        FakeExec(stdout=""),  # leaf manifest empty -> no emits
    )
    rc.script("git ls-remote --heads", FakeExec(stdout=""))
    rc.script(
        "git ls-remote --heads",
        FakeExec(stdout="abc\trefs/heads/develop\n"),
    )
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned"))
    # server-go layer fails
    rc.script(
        "make accept-env-up",
        FakeExec(stdout="oops", stderr="boom", exit_code=2),
    )
    _patch_runner(monkeypatch, rc)
    _patch_skip(monkeypatch)
    _patch_ensure_runner_with_clone(monkeypatch)
    _patch_db(monkeypatch)
    _patch_bkd(monkeypatch)
    _patch_pr_links(monkeypatch)

    from orchestrator.actions import create_accept as mod

    result = await mod.create_accept(
        body=FakeBody(),
        req_id="REQ-test-4",
        tags=[],
        ctx={
            "cloned_repos": ["ZonEaseTech/ttpos-flutter"],
            "branch": "feat/REQ-test-4",
        },
    )
    assert result["emit"] == "accept-env-up.fail"
    assert result["failed_layer"] == "ZonEaseTech/ttpos-server-go"
    assert result["exit_code"] == 2


# ─── R6 branch resolution failure escalates ─────────────────────────────

@pytest.mark.asyncio
async def test_multi_layer_branch_resolution_failure(monkeypatch):
    """CREO-S24: no same-name + no class branch in needs repo -> ACCEPT_ENV_UP_FAIL."""
    rc = FakeRunnerController()
    rc.script(
        "/workspace/source/ttpos-flutter/.sisyphus/env.yaml",
        FakeExec(stdout=(
            "__MANIFEST_FOUND__\n"
            "needs:\n  - ZonEaseTech/ttpos-server-go\n"
        )),
    )
    # bootstrap same-name check (False)
    rc.script("git ls-remote --heads", FakeExec(stdout=""))
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned"))
    rc.script(
        "/workspace/source/ttpos-server-go/.sisyphus/env.yaml",
        FakeExec(stdout=""),  # empty leaf manifest
    )
    # branch resolution: same-name (False) + candidate develop (False)
    rc.script("git ls-remote --heads", FakeExec(stdout=""))
    rc.script("git ls-remote --heads", FakeExec(stdout=""))
    _patch_runner(monkeypatch, rc)
    _patch_skip(monkeypatch)
    _patch_ensure_runner_with_clone(monkeypatch)
    _patch_db(monkeypatch)
    _patch_bkd(monkeypatch)
    _patch_pr_links(monkeypatch)

    from orchestrator.actions import create_accept as mod

    result = await mod.create_accept(
        body=FakeBody(),
        req_id="REQ-test-5",
        tags=[],
        ctx={
            "cloned_repos": ["ZonEaseTech/ttpos-flutter"],
            "branch": "feat/REQ-test-5",
        },
    )
    assert result["emit"] == "accept-env-up.fail"
    assert "branch_resolution_failed" in result["reason"]
    assert "ttpos-server-go" in result["reason"]


# ─── teardown_accept_env multi-layer reverse-order ──────────────────────

@pytest.mark.asyncio
async def test_teardown_multi_layer_reverse_order(monkeypatch):
    """R7 / OCRE-S16 / CREO-S26, S27: reverse-order best-effort teardown.

    Topology (leaves first): [ttpos-server-go, ttpos-flutter] — flutter is
    source. Reverse order = flutter first, then server-go. Flutter teardown
    fails; server-go must still run.
    """
    rc = FakeRunnerController()
    # flutter teardown fails (exit 1)
    rc.script(
        "/workspace/source/ttpos-flutter && make accept-env-down",
        FakeExec(stderr="oops", exit_code=1),
    )
    # server-go teardown succeeds despite flutter failure
    rc.script(
        "/workspace/source/ttpos-server-go && make accept-env-down",
        FakeExec(exit_code=0),
    )

    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.k8s_runner.get_controller",
        lambda: rc,
    )
    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.skip_if_enabled",
        lambda *a, **kw: None,
    )

    writes = []

    class P:
        async def execute(self, sql, *args):
            writes.append(("execute", sql.strip()[:60], args))

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.db.get_pool", lambda: P(),
    )

    from orchestrator.actions import teardown_accept_env as mod

    result = await mod.teardown_accept_env(
        body=FakeBody(),
        req_id="REQ-test-7",
        tags=[],
        ctx={
            "accept_result": "pass",
            "accept_layers": [
                "ZonEaseTech/ttpos-server-go",
                "ZonEaseTech/ttpos-flutter",
            ],
        },
    )

    # next-event derived from accept_result, NOT teardown outcome
    assert result["emit"] == "teardown-done.pass"
    # both layers' make accept-env-down were attempted, in reverse order
    cmds = [c for (c, _e) in rc.calls]
    flutter_idx = next(i for i, c in enumerate(cmds) if "ttpos-flutter" in c)
    server_go_idx = next(i for i, c in enumerate(cmds) if "ttpos-server-go" in c)
    assert flutter_idx < server_go_idx, "flutter teardown must run before server-go"
    # env_down_ok reflects partial failure
    assert result["env_down_ok"] is False


# ─── teardown single-layer fallback unchanged ────────────────────────────

@pytest.mark.asyncio
async def test_teardown_single_layer_falls_back_to_resolver(monkeypatch):
    """no accept_layers in ctx -> existing single-layer resolver path runs."""
    rc = FakeRunnerController()
    rc.script(
        "/workspace/source/sisyphus && make accept-env-down",
        FakeExec(exit_code=0),
    )
    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.k8s_runner.get_controller",
        lambda: rc,
    )
    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.skip_if_enabled",
        lambda *a, **kw: None,
    )

    class P:
        async def execute(self, sql, *args):
            return None

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.db.get_pool", lambda: P(),
    )
    from orchestrator.actions._integration_resolver import ResolveResult

    async def _stub(rc, req_id):
        return ResolveResult(dir="/workspace/source/sisyphus")

    monkeypatch.setattr(
        "orchestrator.actions.teardown_accept_env.resolve_integration_dir", _stub,
    )

    from orchestrator.actions import teardown_accept_env as mod

    result = await mod.teardown_accept_env(
        body=FakeBody(),
        req_id="REQ-test-8",
        tags=[],
        ctx={"accept_result": "pass"},
    )
    assert result["emit"] == "teardown-done.pass"
    assert result["env_down_ok"] is True
    cmds = [c for (c, _e) in rc.calls]
    assert any("ttpos-flutter" not in c and "make accept-env-down" in c for c in cmds)


# ─── R12: pattern-form emit + pre-resolve wiring (IMPL-S7 / IMPL-S8) ─────

@pytest.mark.asyncio
async def test_multi_layer_pattern_form_emit_pre_resolved_seeds_bundle(monkeypatch):
    """IMPL-S8: pattern-form `endpoint` is pre-resolved (R12); flutter's accept-env-up
    JSON only needs to emit `device` (the bare-string emit). pre-resolved value reaches
    flutter's BACKEND_ENDPOINT input without touching server-go's accept-env-up output.
    """
    rc = FakeRunnerController()
    # 1. read source (flutter) manifest
    rc.script(
        "/workspace/source/ttpos-flutter/.sisyphus/env.yaml",
        FakeExec(stdout=(
            "__MANIFEST_FOUND__\n"
            "emits:\n  - device\n"
            "needs:\n  - ZonEaseTech/ttpos-server-go\n"
            "inputs:\n  BACKEND_ENDPOINT: ZonEaseTech/ttpos-server-go.endpoint\n"
        )),
    )
    # 2. branch_exists check for ttpos-server-go same-name
    rc.script("git ls-remote --heads", FakeExec(stdout="", exit_code=0))
    # 3. clone server-go on develop bootstrap branch
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned", exit_code=0))
    # 4. read server-go manifest with pattern-form endpoint
    rc.script(
        "/workspace/source/ttpos-server-go/.sisyphus/env.yaml",
        FakeExec(stdout=(
            "emits:\n"
            "  - endpoint:\n"
            '      pattern: "ttpos-server-go.{NS}.svc.cluster.local:{PORT}"\n'
            "      vars:\n"
            '        NS: "${SISYPHUS_NAMESPACE}"\n'
            '        PORT: "8080"\n'
        )),
    )
    # 5. branch resolution: same-name check (False)
    rc.script("git ls-remote --heads", FakeExec(stdout="", exit_code=0))
    # 6. branch resolution: candidate develop check (True)
    rc.script(
        "git ls-remote --heads",
        FakeExec(stdout="abc123\trefs/heads/develop\n", exit_code=0),
    )
    # 7. final clone on develop
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned", exit_code=0))
    # 8. server-go layer-up: stdout JSON intentionally omits the pattern-form `endpoint`
    #    field — pre-resolved value MUST take precedence (R12).
    rc.script(
        "make accept-env-up",
        FakeExec(stdout='log\n{"unrelated":"x"}', exit_code=0),
    )
    # 9. flutter layer-up: emits device + thanatos block
    rc.script(
        "make accept-env-up",
        FakeExec(
            stdout=(
                'log\n{"device":"redroid:5554","thanatos":'
                '{"pod":"th","namespace":"ns","skill_repo":"phona/skill"}}'
            ),
            exit_code=0,
        ),
    )
    _patch_runner(monkeypatch, rc)
    _patch_skip(monkeypatch)
    _patch_ensure_runner_with_clone(monkeypatch)
    _patch_db(monkeypatch)
    bkd = _patch_bkd(monkeypatch)
    _patch_pr_links(monkeypatch)

    from orchestrator.actions import create_accept as mod

    result = await mod.create_accept(
        body=FakeBody(),
        req_id="REQ-test-pattern-1",
        tags=[],
        ctx={
            "cloned_repos": ["ZonEaseTech/ttpos-flutter"],
            "branch": "feat/REQ-test-pattern-1",
        },
    )

    # accept dispatched (no missing emit field for pattern-form endpoint)
    assert bkd.create_issue.await_count == 1
    expected_pre = "ttpos-server-go.accept-req-test-pattern-1.svc.cluster.local:8080"
    # primary endpoint comes from pre-resolved bundle (server-go.endpoint)
    assert result["endpoint"] == expected_pre
    # flutter received BACKEND_ENDPOINT from pre-resolved bundle
    layer_up_calls = [(c, e) for (c, e) in rc.calls if "make accept-env-up" in c]
    assert len(layer_up_calls) == 2
    assert layer_up_calls[1][1].get("BACKEND_ENDPOINT") == expected_pre


@pytest.mark.asyncio
async def test_multi_layer_pre_resolve_unresolved_var_aborts_before_layer_up(monkeypatch):
    """IMPL-S7: PreResolveError before any `make accept-env-up` call."""
    rc = FakeRunnerController()
    # source flutter manifest
    rc.script(
        "/workspace/source/ttpos-flutter/.sisyphus/env.yaml",
        FakeExec(stdout=(
            "__MANIFEST_FOUND__\n"
            "needs:\n  - ZonEaseTech/ttpos-server-go\n"
            "inputs:\n  BACKEND_ENDPOINT: ZonEaseTech/ttpos-server-go.endpoint\n"
        )),
    )
    rc.script("git ls-remote --heads", FakeExec(stdout="", exit_code=0))
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned", exit_code=0))
    # server-go manifest references a SISYPHUS var that won't be in REQ context
    rc.script(
        "/workspace/source/ttpos-server-go/.sisyphus/env.yaml",
        FakeExec(stdout=(
            "emits:\n"
            "  - endpoint:\n"
            '      pattern: "svc.{NS}"\n'
            "      vars:\n"
            '        NS: "${SISYPHUS_DOES_NOT_EXIST}"\n'
        )),
    )
    rc.script("git ls-remote --heads", FakeExec(stdout="", exit_code=0))
    rc.script(
        "git ls-remote --heads",
        FakeExec(stdout="abc\trefs/heads/develop\n", exit_code=0),
    )
    rc.script("sisyphus-clone-repos.sh", FakeExec(stdout="cloned", exit_code=0))
    _patch_runner(monkeypatch, rc)
    _patch_skip(monkeypatch)
    _patch_ensure_runner_with_clone(monkeypatch)
    _patch_db(monkeypatch)
    _patch_bkd(monkeypatch)
    _patch_pr_links(monkeypatch)

    from orchestrator.actions import create_accept as mod

    result = await mod.create_accept(
        body=FakeBody(),
        req_id="REQ-test-pattern-fail",
        tags=[],
        ctx={
            "cloned_repos": ["ZonEaseTech/ttpos-flutter"],
            "branch": "feat/REQ-test-pattern-fail",
        },
    )

    assert result["emit"].endswith("env-up.fail") or "fail" in result["emit"]
    assert result["failed_phase"] == "pre_resolve"
    assert result["failed_layer"] == "ZonEaseTech/ttpos-server-go"
    # critical: NO `make accept-env-up` invocation should have occurred
    assert not any("make accept-env-up" in c for (c, _e) in rc.calls)
