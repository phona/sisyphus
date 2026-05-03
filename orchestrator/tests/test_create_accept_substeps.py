"""REQ-feat-accept-env-substep-timing: sub_steps optional JSON → stage_runs rows.

Covers spec scenarios SUBSTEP-S1..S4 from
openspec/changes/REQ-feat-accept-env-substep-timing-1777812776/specs/
accept-env-observability/spec.md.

Test surface: `_record_sub_steps` (pure helper, isolated from k8s/BKD I/O) and
`create_accept` end-to-end with the helper wired in.
"""
from __future__ import annotations

import pytest

from orchestrator.actions import create_accept as mod
from orchestrator.actions._integration_resolver import ResolveResult
from orchestrator.k8s_runner import ExecResult
from orchestrator.state import Event


class _RecordingPool:
    """Capture every stage_runs.insert + update call."""

    def __init__(self):
        self.inserts: list[dict] = []
        self.updates: list[dict] = []
        self._next_id = 1

    async def fetchrow(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("INSERT INTO stage_runs"):
            row = {
                "req_id": args[0], "stage": args[1],
                "parallel_id": args[2], "agent_type": args[3],
                "model": args[4], "started_at": args[5],
            }
            self.inserts.append(row)
            rid = self._next_id
            self._next_id += 1
            return {"id": rid}
        return None

    async def execute(self, sql, *args):
        sql_norm = " ".join(sql.split())
        if sql_norm.startswith("UPDATE stage_runs SET"):
            self.updates.append({
                "id": args[0], "ended_at": args[1],
                "outcome": args[2], "fail_reason": args[3],
            })


# ─── Pure helper tests (SUBSTEP-S1..S3) ────────────────────────────────────

@pytest.mark.asyncio
async def test_substep_s1_well_formed_array_inserts_rows_per_entry():
    """SUBSTEP-S1: well-formed sub_steps → one stage_runs row per entry."""
    pool = _RecordingPool()
    accept_env = {
        "endpoint": "http://x",
        "namespace": "y",
        "sub_steps": [
            {"name": "lab-helm", "duration_sec": 45.2},
            {"name": "apk", "duration_sec": 31.7},
        ],
    }
    n = await mod._record_sub_steps(pool, "REQ-1", accept_env)
    assert n == 2
    assert len(pool.inserts) == 2
    stages = [row["stage"] for row in pool.inserts]
    assert stages == ["accept-env-up.lab-helm", "accept-env-up.apk"]
    assert all(row["req_id"] == "REQ-1" for row in pool.inserts)
    # one update per insert with outcome=pass
    assert len(pool.updates) == 2
    assert all(u["outcome"] == "pass" for u in pool.updates)


@pytest.mark.asyncio
async def test_substep_s2_missing_field_zero_inserts():
    """SUBSTEP-S2: no sub_steps key → no inserts, no error."""
    pool = _RecordingPool()
    accept_env = {"endpoint": "http://x", "namespace": "y"}
    n = await mod._record_sub_steps(pool, "REQ-1", accept_env)
    assert n == 0
    assert pool.inserts == []
    assert pool.updates == []


@pytest.mark.asyncio
async def test_substep_s3_malformed_payloads_zero_inserts(caplog):
    """SUBSTEP-S3: not-a-list / wrong-type entries → skipped, no exception."""
    pool = _RecordingPool()

    # not-a-list
    n = await mod._record_sub_steps(pool, "REQ-1", {"sub_steps": "nope"})
    assert n == 0

    # entry not dict
    n = await mod._record_sub_steps(pool, "REQ-1", {"sub_steps": ["x"]})
    assert n == 0

    # entry missing name
    n = await mod._record_sub_steps(pool, "REQ-1", {"sub_steps": [{"duration_sec": 1.0}]})
    assert n == 0

    # entry missing duration_sec
    n = await mod._record_sub_steps(pool, "REQ-1", {"sub_steps": [{"name": "a"}]})
    assert n == 0

    # entry duration_sec wrong type (bool is technically int but excluded)
    n = await mod._record_sub_steps(pool, "REQ-1", {"sub_steps": [{"name": "a", "duration_sec": True}]})
    assert n == 0

    # entry name empty string
    n = await mod._record_sub_steps(pool, "REQ-1", {"sub_steps": [{"name": "", "duration_sec": 1.0}]})
    assert n == 0

    assert pool.inserts == []
    assert pool.updates == []


@pytest.mark.asyncio
async def test_substep_partial_payload_inserts_only_valid_entries():
    """Mixed valid + invalid entries → only valid persisted, no exception."""
    pool = _RecordingPool()
    accept_env = {
        "sub_steps": [
            {"name": "lab-helm", "duration_sec": 10.0},
            {"name": "bogus"},  # missing duration_sec → skip
            "not-a-dict",       # skip
            {"name": "apk", "duration_sec": 20.0},
        ],
    }
    n = await mod._record_sub_steps(pool, "REQ-1", accept_env)
    assert n == 2
    assert [r["stage"] for r in pool.inserts] == [
        "accept-env-up.lab-helm",
        "accept-env-up.apk",
    ]


@pytest.mark.asyncio
async def test_record_sub_steps_handles_non_dict_accept_env():
    """defensive: accept_env=None or list → zero inserts, no error."""
    pool = _RecordingPool()
    assert await mod._record_sub_steps(pool, "REQ-1", None) == 0
    assert await mod._record_sub_steps(pool, "REQ-1", []) == 0
    assert pool.inserts == []


# ─── End-to-end via create_accept (SUBSTEP-S1 + SUBSTEP-S4) ────────────────

class _FakeRC:
    def __init__(self, env_up_exit=0, env_up_stdout='{"endpoint": "http://x"}\n'):
        self.env_up_exit = env_up_exit
        self.env_up_stdout = env_up_stdout
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
        if "make accept-env-up" in command:
            return ExecResult(
                exit_code=self.env_up_exit, stdout=self.env_up_stdout,
                stderr="", duration_sec=0.5,
            )
        # lite fallback (no thanatos block) — short-circuit pass
        return ExecResult(exit_code=0, stdout="PASS\n", stderr="", duration_sec=0.5)


def _body():
    return type("B", (), {
        "issueId": "i-1", "projectId": "p", "event": "pr-ci.pass",
        "title": "T", "tags": [], "issueNumber": None,
    })()


def _patch(monkeypatch, rc, pool, recorded_substep_calls):
    monkeypatch.setattr("orchestrator.actions.create_accept.k8s_runner.get_controller", lambda: rc)
    monkeypatch.setattr("orchestrator.actions.create_accept.db.get_pool", lambda: pool)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.accept_smoke_delay_sec", 0)

    async def fake_resolve_integration_dir(rc, req_id):
        return ResolveResult(dir="/workspace/source/test")
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.resolve_integration_dir",
        fake_resolve_integration_dir,
    )

    async def fake_update_ctx(p, req_id, updates):
        pass
    monkeypatch.setattr(
        "orchestrator.actions.create_accept.req_state.update_context",
        fake_update_ctx,
    )

    # spy on _record_sub_steps to confirm wiring without exercising DB SQL twice
    real = mod._record_sub_steps

    async def spy(pool_arg, req_id_arg, accept_env_arg):
        recorded_substep_calls.append({"req_id": req_id_arg, "accept_env": accept_env_arg})
        return await real(pool_arg, req_id_arg, accept_env_arg)
    monkeypatch.setattr("orchestrator.actions.create_accept._record_sub_steps", spy)


@pytest.mark.asyncio
async def test_substep_e2e_envup_pass_with_substeps_calls_record(monkeypatch):
    """SUBSTEP-S1 e2e: env-up exits 0 + JSON has sub_steps → helper invoked."""
    rc = _FakeRC(env_up_stdout=(
        '{"endpoint": "http://x", "namespace": "y", '
        '"sub_steps": [{"name": "lab-helm", "duration_sec": 1.5}]}\n'
    ))
    pool = _RecordingPool()
    calls: list[dict] = []
    _patch(monkeypatch, rc, pool, calls)

    out = await mod.create_accept(
        body=_body(), req_id="REQ-1", tags=[], ctx={"cloned_repos": []},
    )
    assert out["emit"] == Event.ACCEPT_PASS.value
    assert len(calls) == 1, "_record_sub_steps must be called once after env-up parse"
    assert calls[0]["accept_env"]["sub_steps"][0]["name"] == "lab-helm"


@pytest.mark.asyncio
async def test_substep_s4_envup_fail_does_not_call_record(monkeypatch):
    """SUBSTEP-S4: env-up exits non-zero → helper NEVER called."""
    rc = _FakeRC(
        env_up_exit=1,
        env_up_stdout='{"endpoint": "http://x", "sub_steps": [{"name": "lab", "duration_sec": 5}]}\n',
    )
    pool = _RecordingPool()
    calls: list[dict] = []
    _patch(monkeypatch, rc, pool, calls)

    out = await mod.create_accept(
        body=_body(), req_id="REQ-1", tags=[], ctx={"cloned_repos": []},
    )
    assert out["emit"] == Event.ACCEPT_ENV_UP_FAIL.value
    assert calls == [], "sub_steps recorder must not run on env-up failure"
