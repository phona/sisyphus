"""Contract tests for REQ-accept-env-gc-1777377950.

Black-box challenger. Derived exclusively from:
  openspec/changes/REQ-accept-env-gc-1777377950/specs/accept-env-gc/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  AEGC-S1   active REQ keeps its accept namespace
  AEGC-S2   done REQ causes namespace deletion
  AEGC-S3   escalated REQ causes namespace deletion with no retention
  AEGC-S4   orphan namespace (no req_state row) is cleaned
  AEGC-S5   empty namespace list is a no-op
  AEGC-S6   delete_namespace 404 counts as cleaned
  AEGC-S7   label selector returns matching namespaces
  AEGC-S8   empty label result triggers prefix fallback
  AEGC-S9   successful deletion logs and returns
  AEGC-S10  404 is silently ignored
  AEGC-S11  normal tick logs result and continues
  AEGC-S12  exception in gc_once is logged but loop continues
  AEGC-S13  startup starts loop when controller OK and interval > 0
  AEGC-S14  startup skips loop when controller fails
  AEGC-S15  manual trigger returns GC result
  AEGC-S16  status endpoint returns last result without auth
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from kubernetes.client.exceptions import ApiException

# ─── Shared fakes ───────────────────────────────────────────────────────────

@dataclass
class _FakePool:
    rows: list[dict] = field(default_factory=list)

    async def fetch(self, sql: str, *args):
        return self.rows


class _FakeController:
    """Fake K8s controller that records delete calls."""

    def __init__(self, namespaces: list[str] | None = None):
        self.namespaces = namespaces or []
        self.delete_calls: list[str] = []
        self._delete_raises: dict[str, Exception] = {}

    async def list_accept_env_namespaces(self) -> list[str]:
        return list(self.namespaces)

    def raise_on_delete(self, name: str, exc: Exception):
        self._delete_raises[name] = exc

    async def delete_namespace(self, name: str) -> None:
        if name in self._delete_raises:
            raise self._delete_raises.pop(name)
        self.delete_calls.append(name)


def _ns_list(names: list[str]):
    """Return a mock namespace list response."""
    items = []
    for name in names:
        m = MagicMock()
        m.metadata.name = name
        items.append(m)
    result = MagicMock()
    result.items = items
    return result


def _reset_aegc_module(monkeypatch):
    from orchestrator import accept_env_gc as aegc_mod
    monkeypatch.setattr(aegc_mod, "_last_gc_result", None, raising=False)


def _set_controller(ctrl):
    from orchestrator import k8s_runner
    k8s_runner.set_controller(ctrl)


def _clear_controller():
    from orchestrator import k8s_runner
    k8s_runner.set_controller(None)


# ─── Requirement 1: gc_once ─────────────────────────────────────────────────

# ── AEGC-S1 ────────────────────────────────────────────────────────────────


async def test_aegc_s1_active_req_keeps_namespace(monkeypatch):
    """
    AEGC-S1: GIVEN req_state rows REQ-1 accept-running and REQ-2 analyzing,
    AND K8s lists namespaces ["accept-req-1", "accept-req-2"],
    WHEN gc_once() is awaited,
    THEN both namespaces MUST be in kept_namespaces,
    AND cleaned_namespaces MUST be empty,
    AND delete_namespace MUST NOT be invoked.
    """
    from orchestrator import accept_env_gc as aegc_mod

    _reset_aegc_module(monkeypatch)

    pool = _FakePool(rows=[
        {"req_id": "REQ-1", "state": "accept-running"},
        {"req_id": "REQ-2", "state": "analyzing"},
    ])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)

    ctrl = _FakeController(namespaces=["accept-req-1", "accept-req-2"])
    _set_controller(ctrl)

    result = await aegc_mod.gc_once()

    assert result["kept_namespaces"] == ["accept-req-1", "accept-req-2"], (
        f"AEGC-S1: kept_namespaces MUST contain both active namespaces; got {result['kept_namespaces']!r}"
    )
    assert result["cleaned_namespaces"] == [], (
        f"AEGC-S1: cleaned_namespaces MUST be empty; got {result['cleaned_namespaces']!r}"
    )
    assert ctrl.delete_calls == [], (
        f"AEGC-S1: delete_namespace MUST NOT be invoked; got {ctrl.delete_calls}"
    )

    _clear_controller()


# ── AEGC-S2 ────────────────────────────────────────────────────────────────


async def test_aegc_s2_done_req_causes_deletion(monkeypatch):
    """
    AEGC-S2: GIVEN req_state row REQ-1 state done,
    AND K8s lists namespace ["accept-req-1"],
    WHEN gc_once() is awaited,
    THEN delete_namespace("accept-req-1") MUST be invoked exactly once,
    AND cleaned_namespaces MUST equal ["accept-req-1"],
    AND kept_namespaces MUST be empty.
    """
    from orchestrator import accept_env_gc as aegc_mod

    _reset_aegc_module(monkeypatch)

    pool = _FakePool(rows=[{"req_id": "REQ-1", "state": "done"}])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)

    ctrl = _FakeController(namespaces=["accept-req-1"])
    _set_controller(ctrl)

    result = await aegc_mod.gc_once()

    assert ctrl.delete_calls == ["accept-req-1"], (
        f"AEGC-S2: delete_namespace MUST be called exactly once with 'accept-req-1'; got {ctrl.delete_calls}"
    )
    assert result["cleaned_namespaces"] == ["accept-req-1"], (
        f"AEGC-S2: cleaned_namespaces MUST equal ['accept-req-1']; got {result['cleaned_namespaces']!r}"
    )
    assert result["kept_namespaces"] == [], (
        f"AEGC-S2: kept_namespaces MUST be empty; got {result['kept_namespaces']!r}"
    )

    _clear_controller()


# ── AEGC-S3 ────────────────────────────────────────────────────────────────


async def test_aegc_s3_escalated_req_deletes_with_no_retention(monkeypatch):
    """
    AEGC-S3: GIVEN req_state row REQ-1 state escalated with updated_at within
    the default runner PVC retention window,
    AND K8s lists namespace ["accept-req-1"],
    WHEN gc_once() is awaited,
    THEN delete_namespace("accept-req-1") MUST be invoked exactly once,
    AND the namespace MUST be in cleaned_namespaces (NOT kept_namespaces),
    AND the behavior MUST differ from runner_gc PVC retention.
    """
    from orchestrator import accept_env_gc as aegc_mod

    _reset_aegc_module(monkeypatch)

    pool = _FakePool(rows=[{
        "req_id": "REQ-1",
        "state": "escalated",
        "updated_at": datetime.now(UTC),
    }])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)

    ctrl = _FakeController(namespaces=["accept-req-1"])
    _set_controller(ctrl)

    result = await aegc_mod.gc_once()

    assert ctrl.delete_calls == ["accept-req-1"], (
        f"AEGC-S3: delete_namespace MUST be called for escalated REQ; got {ctrl.delete_calls}"
    )
    assert "accept-req-1" in result["cleaned_namespaces"], (
        f"AEGC-S3: 'accept-req-1' MUST be in cleaned_namespaces; got {result['cleaned_namespaces']!r}"
    )
    assert "accept-req-1" not in result["kept_namespaces"], (
        f"AEGC-S3: 'accept-req-1' MUST NOT be in kept_namespaces (no retention); got {result['kept_namespaces']!r}"
    )

    _clear_controller()


# ── AEGC-S4 ────────────────────────────────────────────────────────────────


async def test_aegc_s4_orphan_namespace_cleaned(monkeypatch):
    """
    AEGC-S4: GIVEN req_state has only REQ-1 state analyzing,
    AND K8s lists namespaces ["accept-req-1", "accept-req-orphan"],
    WHEN gc_once() is awaited,
    THEN "accept-req-1" MUST be in kept_namespaces,
    AND "accept-req-orphan" MUST be in cleaned_namespaces,
    AND delete_namespace MUST be invoked exactly once with "accept-req-orphan".
    """
    from orchestrator import accept_env_gc as aegc_mod

    _reset_aegc_module(monkeypatch)

    pool = _FakePool(rows=[{"req_id": "REQ-1", "state": "analyzing"}])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)

    ctrl = _FakeController(namespaces=["accept-req-1", "accept-req-orphan"])
    _set_controller(ctrl)

    result = await aegc_mod.gc_once()

    assert "accept-req-1" in result["kept_namespaces"], (
        f"AEGC-S4: 'accept-req-1' MUST be in kept_namespaces; got {result['kept_namespaces']!r}"
    )
    assert "accept-req-orphan" in result["cleaned_namespaces"], (
        f"AEGC-S4: 'accept-req-orphan' MUST be in cleaned_namespaces; got {result['cleaned_namespaces']!r}"
    )
    assert ctrl.delete_calls == ["accept-req-orphan"], (
        f"AEGC-S4: delete_namespace MUST be called exactly once with 'accept-req-orphan'; got {ctrl.delete_calls}"
    )

    _clear_controller()


# ── AEGC-S5 ────────────────────────────────────────────────────────────────


async def test_aegc_s5_empty_namespace_list_no_op(monkeypatch):
    """
    AEGC-S5: GIVEN any req_state contents,
    AND K8s controller returns an empty namespace list,
    WHEN gc_once() is awaited,
    THEN both cleaned_namespaces and kept_namespaces MUST be empty,
    AND delete_namespace MUST NOT be invoked.
    """
    from orchestrator import accept_env_gc as aegc_mod

    _reset_aegc_module(monkeypatch)

    pool = _FakePool(rows=[{"req_id": "REQ-1", "state": "analyzing"}])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)

    ctrl = _FakeController(namespaces=[])
    _set_controller(ctrl)

    result = await aegc_mod.gc_once()

    assert result["cleaned_namespaces"] == [], (
        f"AEGC-S5: cleaned_namespaces MUST be empty; got {result['cleaned_namespaces']!r}"
    )
    assert result["kept_namespaces"] == [], (
        f"AEGC-S5: kept_namespaces MUST be empty; got {result['kept_namespaces']!r}"
    )
    assert ctrl.delete_calls == [], (
        f"AEGC-S5: delete_namespace MUST NOT be invoked; got {ctrl.delete_calls}"
    )

    _clear_controller()


# ── AEGC-S6 ────────────────────────────────────────────────────────────────


async def test_aegc_s6_delete_404_counts_as_cleaned(monkeypatch):
    """
    AEGC-S6: GIVEN req_state row REQ-1 state done,
    AND K8s lists namespace ["accept-req-1"],
    AND delete_namespace raises ApiException(status=404),
    WHEN gc_once() is awaited,
    THEN the exception MUST be caught and swallowed,
    AND "accept-req-1" MUST be in cleaned_namespaces,
    AND no exception MUST propagate to the caller.
    """
    from orchestrator import accept_env_gc as aegc_mod

    _reset_aegc_module(monkeypatch)

    pool = _FakePool(rows=[{"req_id": "REQ-1", "state": "done"}])
    monkeypatch.setattr("orchestrator.accept_env_gc.db.get_pool", lambda: pool)

    ctrl = _FakeController(namespaces=["accept-req-1"])
    ctrl.raise_on_delete("accept-req-1", ApiException(status=404))
    _set_controller(ctrl)

    result = await aegc_mod.gc_once()

    assert "accept-req-1" in result["cleaned_namespaces"], (
        f"AEGC-S6: 'accept-req-1' MUST be in cleaned_namespaces after 404; got {result['cleaned_namespaces']!r}"
    )
    assert "accept-req-1" not in result["kept_namespaces"], (
        f"AEGC-S6: 'accept-req-1' MUST NOT be in kept_namespaces; got {result['kept_namespaces']!r}"
    )

    _clear_controller()


# ─── Requirement 2: list_accept_env_namespaces ──────────────────────────────

# ── AEGC-S7 ────────────────────────────────────────────────────────────────


async def test_aegc_s7_label_selector_returns_matches(monkeypatch):
    """
    AEGC-S7: GIVEN the K8s API returns namespaces ["accept-req-1", "accept-req-2"]
    when queried with label selector sisyphus/role=accept-env,
    WHEN RunnerController.list_accept_env_namespaces() is awaited,
    THEN the returned list MUST equal ["accept-req-1", "accept-req-2"],
    AND the fallback prefix filter MUST NOT be invoked.
    """
    from orchestrator.k8s_runner import RunnerController

    core_v1 = MagicMock()
    core_v1.list_namespace = MagicMock(return_value=_ns_list(["accept-req-1", "accept-req-2"]))

    ctrl = RunnerController(
        namespace="sisyphus-runners",
        runner_image="ghcr.io/phona/sisyphus-runner:main",
        runner_sa="sisyphus-runner",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="sisyphus-runner-secret",
        in_cluster=False,
        ready_timeout_sec=10,
        ready_attempts=1,
        core_v1=core_v1,
    )

    result = await ctrl.list_accept_env_namespaces()

    assert result == ["accept-req-1", "accept-req-2"], (
        f"AEGC-S7: MUST return ['accept-req-1', 'accept-req-2']; got {result!r}"
    )
    # label selector path MUST be used
    label_calls = [
        c for c in core_v1.list_namespace.call_args_list
        if c.kwargs.get("label_selector") == "sisyphus/role=accept-env"
    ]
    assert len(label_calls) >= 1, (
        f"AEGC-S7: list_namespace MUST be called with label_selector; got {core_v1.list_namespace.call_args_list}"
    )


# ── AEGC-S8 ────────────────────────────────────────────────────────────────


async def test_aegc_s8_empty_label_triggers_prefix_fallback(monkeypatch):
    """
    AEGC-S8: GIVEN the K8s API returns an empty list for label selector
    sisyphus/role=accept-env, but returns ["accept-req-1", "other-ns"] when
    listing all namespaces,
    WHEN RunnerController.list_accept_env_namespaces() is awaited,
    THEN the returned list MUST equal ["accept-req-1"],
    AND "other-ns" MUST be excluded because it does not match the prefix.
    """
    from orchestrator.k8s_runner import RunnerController

    def _list_namespace(*, label_selector=None, **kwargs):
        if label_selector == "sisyphus/role=accept-env":
            return _ns_list([])
        return _ns_list(["accept-req-1", "other-ns"])

    core_v1 = MagicMock()
    core_v1.list_namespace = MagicMock(side_effect=_list_namespace)

    ctrl = RunnerController(
        namespace="sisyphus-runners",
        runner_image="ghcr.io/phona/sisyphus-runner:main",
        runner_sa="sisyphus-runner",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="sisyphus-runner-secret",
        in_cluster=False,
        ready_timeout_sec=10,
        ready_attempts=1,
        core_v1=core_v1,
    )

    result = await ctrl.list_accept_env_namespaces()

    assert result == ["accept-req-1"], (
        f"AEGC-S8: MUST return ['accept-req-1']; got {result!r}"
    )
    assert "other-ns" not in result, (
        f"AEGC-S8: 'other-ns' MUST be excluded by prefix filter; got {result!r}"
    )


# ─── Requirement 3: delete_namespace ────────────────────────────────────────

# ── AEGC-S9 ────────────────────────────────────────────────────────────────


async def test_aegc_s9_successful_deletion_logs_and_returns(monkeypatch):
    """
    AEGC-S9: GIVEN a namespace named "accept-req-1" exists in the cluster,
    WHEN RunnerController.delete_namespace("accept-req-1") is awaited,
    THEN the K8s delete_namespace API MUST be invoked exactly once,
    AND an INFO log containing "runner.namespace.deleted" MUST be emitted.
    """
    from orchestrator.k8s_runner import RunnerController

    core_v1 = MagicMock()
    core_v1.delete_namespace = MagicMock(return_value=None)

    ctrl = RunnerController(
        namespace="sisyphus-runners",
        runner_image="ghcr.io/phona/sisyphus-runner:main",
        runner_sa="sisyphus-runner",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="sisyphus-runner-secret",
        in_cluster=False,
        ready_timeout_sec=10,
        ready_attempts=1,
        core_v1=core_v1,
    )

    log_mock = MagicMock()
    monkeypatch.setattr("orchestrator.k8s_runner.log", log_mock)

    await ctrl.delete_namespace("accept-req-1")

    assert core_v1.delete_namespace.call_count == 1, (
        f"AEGC-S9: delete_namespace API MUST be called exactly once; got {core_v1.delete_namespace.call_count}"
    )
    info_calls = [c for c in log_mock.info.call_args_list if "runner.namespace.deleted" in str(c)]
    assert len(info_calls) >= 1, (
        f"AEGC-S9: INFO log containing 'runner.namespace.deleted' MUST be emitted; got {log_mock.info.call_args_list}"
    )


# ── AEGC-S10 ───────────────────────────────────────────────────────────────


async def test_aegc_s10_404_silently_ignored(monkeypatch):
    """
    AEGC-S10: GIVEN a namespace named "accept-req-1" does not exist
    (K8s returns 404),
    WHEN RunnerController.delete_namespace("accept-req-1") is awaited,
    THEN the method MUST return without raising,
    AND no error log MUST be emitted.
    """
    from orchestrator.k8s_runner import RunnerController

    def _raise_404(*args, **kwargs):
        raise ApiException(status=404)

    core_v1 = MagicMock()
    core_v1.delete_namespace = MagicMock(side_effect=_raise_404)

    ctrl = RunnerController(
        namespace="sisyphus-runners",
        runner_image="ghcr.io/phona/sisyphus-runner:main",
        runner_sa="sisyphus-runner",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="sisyphus-runner-secret",
        in_cluster=False,
        ready_timeout_sec=10,
        ready_attempts=1,
        core_v1=core_v1,
    )

    log_mock = MagicMock()
    monkeypatch.setattr("orchestrator.k8s_runner.log", log_mock)

    await ctrl.delete_namespace("accept-req-1")

    assert core_v1.delete_namespace.call_count == 1, (
        f"AEGC-S10: delete_namespace API MUST be called once; got {core_v1.delete_namespace.call_count}"
    )
    error_calls = [c for c in log_mock.error.call_args_list if "accept-req-1" in str(c)]
    assert error_calls == [], (
        f"AEGC-S10: no error log MUST be emitted for 404; got {log_mock.error.call_args_list}"
    )


# ─── Requirement 4: run_loop ────────────────────────────────────────────────

# ── AEGC-S11 ───────────────────────────────────────────────────────────────


async def test_aegc_s11_normal_tick_logs_result_and_continues(monkeypatch):
    """
    AEGC-S11: GIVEN accept_env_gc_interval_sec is set to a small value,
    AND gc_once() returns {"cleaned_namespaces": []} on the first tick,
    WHEN run_loop() is started and allowed to run for at least 2 ticks,
    THEN each tick MUST call gc_once(),
    AND a DEBUG log accept_env_gc.tick MUST be emitted for each tick.
    """
    from orchestrator import accept_env_gc as aegc_mod

    _reset_aegc_module(monkeypatch)
    monkeypatch.setattr("orchestrator.accept_env_gc.settings.accept_env_gc_interval_sec", 0.001)

    tick_count = 0

    async def _gc_once():
        nonlocal tick_count
        tick_count += 1
        if tick_count >= 3:
            task.cancel()
        return {"cleaned_namespaces": []}

    monkeypatch.setattr(aegc_mod, "gc_once", _gc_once)
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_kw: asyncio.sleep(0))

    log_mock = MagicMock()
    monkeypatch.setattr(aegc_mod, "log", log_mock)

    task = asyncio.create_task(aegc_mod.run_loop())
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert tick_count >= 2, (
        f"AEGC-S11: gc_once MUST be called at least twice; got {tick_count} call(s)"
    )
    tick_logs = [c for c in log_mock.debug.call_args_list if "accept_env_gc.tick" in str(c)]
    assert len(tick_logs) >= 2, (
        f"AEGC-S11: DEBUG log 'accept_env_gc.tick' MUST be emitted per tick; got {log_mock.debug.call_args_list}"
    )


# ── AEGC-S12 ───────────────────────────────────────────────────────────────


async def test_aegc_s12_exception_in_gc_once_logged_but_continues(monkeypatch):
    """
    AEGC-S12: GIVEN accept_env_gc_interval_sec is set to a small value,
    AND the first gc_once() call raises RuntimeError("boom"),
    WHEN run_loop() is started and allowed to run for at least 2 ticks,
    THEN an ERROR log containing "accept_env_gc.loop.error" MUST be emitted,
    AND the loop MUST continue to the second tick.
    """
    from orchestrator import accept_env_gc as aegc_mod

    _reset_aegc_module(monkeypatch)
    monkeypatch.setattr("orchestrator.accept_env_gc.settings.accept_env_gc_interval_sec", 0.001)

    tick_count = 0

    async def _gc_once():
        nonlocal tick_count
        tick_count += 1
        if tick_count == 1:
            raise RuntimeError("boom")
        if tick_count >= 3:
            task.cancel()
        return {"cleaned_namespaces": []}

    monkeypatch.setattr(aegc_mod, "gc_once", _gc_once)
    monkeypatch.setattr(asyncio, "sleep", lambda *_a, **_kw: asyncio.sleep(0))

    log_mock = MagicMock()
    monkeypatch.setattr(aegc_mod, "log", log_mock)

    task = asyncio.create_task(aegc_mod.run_loop())
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert tick_count >= 2, (
        f"AEGC-S12: loop MUST continue to second tick; got {tick_count} call(s)"
    )
    error_logs = [c for c in log_mock.error.call_args_list if "accept_env_gc.loop.error" in str(c)]
    assert len(error_logs) >= 1, (
        f"AEGC-S12: ERROR log 'accept_env_gc.loop.error' MUST be emitted; got {log_mock.error.call_args_list}"
    )


# ─── Requirement 5: main.py startup ─────────────────────────────────────────

# ── AEGC-S13 ───────────────────────────────────────────────────────────────


async def test_aegc_s13_startup_starts_loop_when_controller_ok_and_interval_positive(monkeypatch):
    """
    AEGC-S13: GIVEN K8s controller initialization succeeds
    AND accept_env_gc_interval_sec = 900,
    WHEN startup() is called,
    THEN an asyncio Task named "accept_env_gc" MUST be created
    AND appended to _bg_tasks.
    """
    import orchestrator.main as main_mod
    from orchestrator import k8s_runner

    monkeypatch.setattr(main_mod, "_bg_tasks", [])
    monkeypatch.setattr("orchestrator.main.apply_pending", lambda dsn: None)
    monkeypatch.setattr("orchestrator.main.db.init_pool", AsyncMock())
    monkeypatch.setattr("orchestrator.main.db.init_obs_pool", AsyncMock())
    monkeypatch.setattr("orchestrator.main.settings.accept_env_gc_interval_sec", 900)
    monkeypatch.setattr("orchestrator.main.settings.runner_gc_interval_sec", 0)
    monkeypatch.setattr("orchestrator.main.settings.snapshot_interval_sec", 0)
    monkeypatch.setattr("orchestrator.main.settings.watchdog_enabled", False)
    monkeypatch.setattr("orchestrator.main.settings.ttl_cleanup_enabled", False)

    # Ensure any previous controller is cleared
    k8s_runner.set_controller(None)

    await main_mod.startup()

    task_names = [t.get_name() for t in main_mod._bg_tasks if hasattr(t, "get_name")]
    assert "accept_env_gc" in task_names, (
        f"AEGC-S13: _bg_tasks MUST contain a task named 'accept_env_gc'; got {task_names}"
    )

    # Cancel tasks to avoid background noise
    for t in main_mod._bg_tasks:
        t.cancel()
    for t in main_mod._bg_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    main_mod._bg_tasks.clear()
    k8s_runner.set_controller(None)


# ── AEGC-S14 ───────────────────────────────────────────────────────────────


async def test_aegc_s14_startup_skips_loop_when_controller_fails(monkeypatch):
    """
    AEGC-S14: GIVEN K8s controller initialization raises an exception
    (e.g. no kubeconfig),
    WHEN startup() is called,
    THEN no accept_env_gc background task MUST be created,
    AND a WARNING log MUST be emitted but startup MUST succeed.
    """
    import orchestrator.main as main_mod

    monkeypatch.setattr(main_mod, "_bg_tasks", [])
    monkeypatch.setattr("orchestrator.main.apply_pending", lambda dsn: None)
    monkeypatch.setattr("orchestrator.main.db.init_pool", AsyncMock())
    monkeypatch.setattr("orchestrator.main.db.init_obs_pool", AsyncMock())
    monkeypatch.setattr("orchestrator.main.settings.accept_env_gc_interval_sec", 900)
    monkeypatch.setattr("orchestrator.main.settings.runner_gc_interval_sec", 0)
    monkeypatch.setattr("orchestrator.main.settings.snapshot_interval_sec", 0)
    monkeypatch.setattr("orchestrator.main.settings.watchdog_enabled", False)
    monkeypatch.setattr("orchestrator.main.settings.ttl_cleanup_enabled", False)

    # Force controller init to fail
    def _raise(*a, **kw):
        raise RuntimeError("no kubeconfig")

    monkeypatch.setattr("orchestrator.main.k8s_runner.RunnerController", _raise)

    log_mock = MagicMock()
    monkeypatch.setattr("orchestrator.main.log", log_mock)

    await main_mod.startup()

    task_names = [t.get_name() for t in main_mod._bg_tasks if hasattr(t, "get_name")]
    assert "accept_env_gc" not in task_names, (
        f"AEGC-S14: _bg_tasks MUST NOT contain 'accept_env_gc' when controller fails; got {task_names}"
    )
    warning_logs = [c for c in log_mock.warning.call_args_list if "k8s_runner.init_failed" in str(c)]
    assert len(warning_logs) >= 1, (
        f"AEGC-S14: WARNING log 'k8s_runner.init_failed' MUST be emitted; got {log_mock.warning.call_args_list}"
    )

    # Cancel any tasks that may have started (none expected)
    for t in main_mod._bg_tasks:
        t.cancel()
    for t in main_mod._bg_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    main_mod._bg_tasks.clear()


# ─── Requirement 6: admin endpoints ─────────────────────────────────────────

# ── AEGC-S15 ───────────────────────────────────────────────────────────────


async def test_aegc_s15_manual_trigger_returns_gc_result(monkeypatch):
    """
    AEGC-S15: GIVEN a valid Bearer token in the Authorization header,
    WHEN POST /admin/accept-env-gc is called,
    THEN gc_once() MUST be invoked exactly once,
    AND the HTTP response MUST contain the result dict.
    """
    from orchestrator import accept_env_gc as aegc_mod
    from orchestrator import admin as admin_mod

    _reset_aegc_module(monkeypatch)
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    gc_calls: list[int] = []

    async def _gc_once():
        gc_calls.append(1)
        return {
            "cleaned_namespaces": ["accept-req-x"],
            "kept_namespaces": [],
            "cleaned_count": 1,
            "kept_count": 0,
            "ran_at": datetime.now(UTC).isoformat(),
        }

    monkeypatch.setattr(aegc_mod, "gc_once", _gc_once)

    result = await admin_mod.trigger_accept_env_gc(authorization="Bearer test-token")

    assert len(gc_calls) == 1, (
        f"AEGC-S15: gc_once MUST be invoked exactly once; got {len(gc_calls)} call(s)"
    )
    assert "cleaned_namespaces" in result, (
        f"AEGC-S15: response MUST contain result dict with 'cleaned_namespaces'; got {result!r}"
    )
    assert result["cleaned_namespaces"] == ["accept-req-x"], (
        f"AEGC-S15: cleaned_namespaces MUST match; got {result['cleaned_namespaces']!r}"
    )


# ── AEGC-S16 ───────────────────────────────────────────────────────────────


async def test_aegc_s16_status_endpoint_returns_last_result_without_auth(monkeypatch):
    """
    AEGC-S16: GIVEN no Authorization header,
    WHEN GET /admin/accept-env-gc/status is called,
    THEN the endpoint MUST return HTTP 200,
    AND the response body MUST contain {"last": null} before any GC has run.
    """
    from orchestrator import accept_env_gc as aegc_mod
    from orchestrator import admin as admin_mod

    monkeypatch.setattr(aegc_mod, "_last_gc_result", None, raising=False)

    result = await admin_mod.accept_env_gc_status()

    assert result == {"last": None}, (
        f"AEGC-S16: status endpoint MUST return {{'last': None}} before GC; got {result!r}"
    )
