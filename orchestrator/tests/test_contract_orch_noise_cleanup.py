"""Contract tests for orch-noise-cleanup (REQ-orch-noise-cleanup-1777078500).

Black-box behavioral contracts derived from:
  openspec/changes/REQ-orch-noise-cleanup-1777078500/specs/orch-noise-cleanup/spec.md

Scenarios covered:
  ORCHN-S1  排除单个项目时跳过 BKD 调用
  ORCHN-S2  排除清单为空时保持原行为
  ORCHN-S3  全部 project_id 都被排除时短路返回 0
  ORCHN-S4  首次 403 时 info 一次并禁用后续 disk-check
  ORCHN-S5  disk-check 已禁用后 gc_once 不再调 list_node
  ORCHN-S6  非 403 异常仍走 debug 不禁用
  ORCHN-S7  disk-check 正常 ratio > threshold 时仍能触发紧急清理
  ORCHN-S8  alert 看板按 level=warning 过滤时看不到 rbac_denied
"""
from __future__ import annotations

import structlog.testing

# ─── Helpers ──────────────────────────────────────────────────────────────────


class _FakePool:
    """asyncpg pool stub: returns preset project_id rows, captures execute calls."""

    def __init__(self, project_ids=()):
        self._project_ids = list(project_ids)
        self.query_log: list = []

    async def fetch(self, sql: str, *args):
        self.query_log.append(("fetch", sql, args))
        return [{"project_id": pid} for pid in self._project_ids]

    async def fetchval(self, sql: str, *args):
        self.query_log.append(("fetchval", sql, args))
        return None

    async def fetchrow(self, sql: str, *args):
        self.query_log.append(("fetchrow", sql, args))
        return None

    async def execute(self, sql: str, *args):
        self.query_log.append(("execute", sql, args))


class _FakeSettings:
    """Minimal settings stub for snapshot/runner_gc contract tests."""

    def __init__(self, exclude=(), runner_gc_disk_pressure_threshold=0.8,
                 pvc_retain_on_escalate_days=1, **kw):
        self.snapshot_exclude_project_ids = list(exclude)
        self.runner_gc_disk_pressure_threshold = runner_gc_disk_pressure_threshold
        self.pvc_retain_on_escalate_days = pvc_retain_on_escalate_days
        self.bkd_base_url = "https://bkd.example.test/api"
        self.bkd_token = "test-token"
        self.snapshot_interval_sec = 300
        for k, v in kw.items():
            setattr(self, k, v)


# ─── ORCHN-S1: 排除单个项目时跳过 BKD 调用 ──────────────────────────────────


async def test_orchn_s1_excluded_project_not_called(monkeypatch):
    """
    ORCHN-S1: snapshot_exclude_project_ids=["77k9z58j"] 时，
    sync_once 必须不以 "77k9z58j" 调用 BKDClient.list_issues，
    只以 "alive-1" 调用一次。
    """
    from orchestrator import snapshot
    from orchestrator.store import db

    list_issues_calls: list[str] = []

    class _FakeBKD:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def list_issues(self, project_id, **kw):
            list_issues_calls.append(project_id)
            return []

    pool = _FakePool(project_ids=["alive-1", "77k9z58j"])
    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(db, "get_obs_pool", lambda: _FakePool())
    monkeypatch.setattr(snapshot, "BKDClient", _FakeBKD)
    monkeypatch.setattr(snapshot, "settings", _FakeSettings(exclude=["77k9z58j"]))

    await snapshot.sync_once()

    assert "77k9z58j" not in list_issues_calls, (
        "ORCHN-S1: BKDClient.list_issues MUST NOT be called for excluded '77k9z58j'; "
        f"actual calls: {list_issues_calls}"
    )
    assert "alive-1" in list_issues_calls, (
        f"ORCHN-S1: BKDClient.list_issues must be called for non-excluded 'alive-1'; "
        f"actual calls: {list_issues_calls}"
    )


# ─── ORCHN-S2: 排除清单为空时保持原行为 ──────────────────────────────────────


async def test_orchn_s2_empty_exclude_calls_all_projects(monkeypatch):
    """
    ORCHN-S2: snapshot_exclude_project_ids=[] 时，
    sync_once 必须为每个 project_id 调用 BKDClient.list_issues（共 2 次）。
    """
    from orchestrator import snapshot
    from orchestrator.store import db

    list_issues_calls: list[str] = []

    class _FakeBKD:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def list_issues(self, project_id, **kw):
            list_issues_calls.append(project_id)
            return []

    pool = _FakePool(project_ids=["alive-1", "alive-2"])
    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(db, "get_obs_pool", lambda: _FakePool())
    monkeypatch.setattr(snapshot, "BKDClient", _FakeBKD)
    monkeypatch.setattr(snapshot, "settings", _FakeSettings(exclude=[]))

    await snapshot.sync_once()

    assert "alive-1" in list_issues_calls, (
        f"ORCHN-S2: 'alive-1' must be included in calls; got: {list_issues_calls}"
    )
    assert "alive-2" in list_issues_calls, (
        f"ORCHN-S2: 'alive-2' must be included in calls; got: {list_issues_calls}"
    )
    assert len(list_issues_calls) == 2, (
        f"ORCHN-S2: exactly 2 list_issues calls expected, got {len(list_issues_calls)}: "
        f"{list_issues_calls}"
    )


# ─── ORCHN-S3: 全部 project_id 都被排除时短路返回 0 ─────────────────────────


async def test_orchn_s3_all_excluded_returns_zero(monkeypatch):
    """
    ORCHN-S3: 所有 project_id 都在 exclude list 时，
    sync_once 必须返回 0 且不调用 BKDClient.list_issues。
    """
    from orchestrator import snapshot
    from orchestrator.store import db

    list_issues_calls: list[str] = []

    class _FakeBKD:
        def __init__(self, *a, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def list_issues(self, project_id, **kw):
            list_issues_calls.append(project_id)
            return []

    pool = _FakePool(project_ids=["only-proj"])
    monkeypatch.setattr(db, "get_pool", lambda: pool)
    monkeypatch.setattr(db, "get_obs_pool", lambda: _FakePool())
    monkeypatch.setattr(snapshot, "BKDClient", _FakeBKD)
    monkeypatch.setattr(snapshot, "settings", _FakeSettings(exclude=["only-proj"]))

    result = await snapshot.sync_once()

    assert result == 0, (
        f"ORCHN-S3: sync_once must return 0 when all project_ids excluded; got {result!r}"
    )
    assert list_issues_calls == [], (
        f"ORCHN-S3: BKDClient.list_issues must NOT be called when all excluded; "
        f"got: {list_issues_calls}"
    )


# ─── ORCHN-S4: 首次 403 时 info 一次并禁用后续 disk-check ─────────────────────


async def test_orchn_s4_first_403_logs_info_and_disables(monkeypatch):
    """
    ORCHN-S4: gc_once 在 node_disk_usage_ratio 抛出 ApiException(status=403) 时：
    - 必须发出恰好一条 'runner_gc.disk_check_rbac_denied' 的 **INFO** 日志
      （不是 WARNING —— alert dashboards 按 level=warning 过滤时看不到）
    - 必须把进程级 _DISK_CHECK_DISABLED flag 置为 True
    - 返回结果 disk_pressure=False
    """
    from kubernetes.client.exceptions import ApiException

    import orchestrator.runner_gc as gc_mod

    monkeypatch.setattr(gc_mod, "_DISK_CHECK_DISABLED", False)

    class _FakeController:
        async def node_disk_usage_ratio(self):
            raise ApiException(status=403)
        async def gc_orphans(self, keep):
            return []

    monkeypatch.setattr(gc_mod.k8s_runner, "get_controller", lambda: _FakeController())
    monkeypatch.setattr(gc_mod.db, "get_pool", lambda: _FakePool())

    with structlog.testing.capture_logs() as log_records:
        result = await gc_mod.gc_once()

    # flag must be set
    assert gc_mod._DISK_CHECK_DISABLED is True, (
        "ORCHN-S4: _DISK_CHECK_DISABLED must be True after first 403"
    )

    # exactly one INFO record with the required event name
    rbac_denied_records = [
        r for r in log_records
        if "runner_gc.disk_check_rbac_denied" in r.get("event", "")
    ]
    assert len(rbac_denied_records) == 1, (
        f"ORCHN-S4: must log exactly one 'runner_gc.disk_check_rbac_denied'; "
        f"got {len(rbac_denied_records)}: {rbac_denied_records}"
    )
    assert rbac_denied_records[0].get("log_level") == "info", (
        f"ORCHN-S4: 'runner_gc.disk_check_rbac_denied' must be at INFO level "
        f"(not WARNING) so alert dashboards stop receiving repeated entries "
        f"across orchestrator pod restarts; got level "
        f"{rbac_denied_records[0].get('log_level')!r}"
    )

    # must NOT emit at warning level (regression guard against PR #66 contract)
    warning_events = [r["event"] for r in log_records if r.get("log_level") == "warning"]
    assert not any("runner_gc.disk_check_rbac_denied" in e for e in warning_events), (
        f"ORCHN-S4: 'runner_gc.disk_check_rbac_denied' MUST NOT appear at "
        f"WARNING level; warnings: {warning_events}"
    )

    # disk_pressure must be False
    disk_pressure = result.get("disk_pressure") if isinstance(result, dict) else getattr(result, "disk_pressure", None)
    assert disk_pressure is False, (
        f"ORCHN-S4: result disk_pressure must be False; got {result!r}"
    )


# ─── ORCHN-S5: disk-check 已禁用后 gc_once 不再调 list_node ─────────────────


async def test_orchn_s5_disabled_skips_node_api(monkeypatch):
    """
    ORCHN-S5: _DISK_CHECK_DISABLED=True 时，gc_once 必须：
    - 不调用 node_disk_usage_ratio（不消耗 K8s API 配额）
    - 不记录任何 runner_gc.disk_check_* 日志
    - 返回 disk_pressure=False
    """
    import orchestrator.runner_gc as gc_mod

    monkeypatch.setattr(gc_mod, "_DISK_CHECK_DISABLED", True)

    ratio_calls: list = []

    class _FakeController:
        async def node_disk_usage_ratio(self):
            ratio_calls.append(True)
            return 0.0
        async def gc_orphans(self, keep):
            return []

    monkeypatch.setattr(gc_mod.k8s_runner, "get_controller", lambda: _FakeController())
    monkeypatch.setattr(gc_mod.db, "get_pool", lambda: _FakePool())

    with structlog.testing.capture_logs() as log_records:
        result = await gc_mod.gc_once()

    # node_disk_usage_ratio must NOT be called
    assert ratio_calls == [], (
        f"ORCHN-S5: node_disk_usage_ratio must NOT be called when _DISK_CHECK_DISABLED=True; "
        f"was called {len(ratio_calls)} time(s)"
    )

    # no disk_check_* logs
    disk_check_events = [r for r in log_records if "runner_gc.disk_check" in r.get("event", "")]
    assert disk_check_events == [], (
        f"ORCHN-S5: no runner_gc.disk_check_* log must be emitted when disabled; "
        f"got: {[r['event'] for r in disk_check_events]}"
    )

    # disk_pressure must be False
    disk_pressure = result.get("disk_pressure") if isinstance(result, dict) else getattr(result, "disk_pressure", None)
    assert disk_pressure is False, (
        f"ORCHN-S5: result disk_pressure must be False when disk-check disabled; got {result!r}"
    )


# ─── ORCHN-S6: 非 403 异常仍走 debug 不禁用 ─────────────────────────────────


async def test_orchn_s6_non_403_debug_no_disable(monkeypatch):
    """
    ORCHN-S6: node_disk_usage_ratio 抛出 ApiException(status=500) 时：
    - 必须记录 DEBUG 日志 'runner_gc.disk_check_failed'
    - _DISK_CHECK_DISABLED 必须保持 False（下一轮仍会重试）
    - 不得发出 WARNING
    """
    from kubernetes.client.exceptions import ApiException

    import orchestrator.runner_gc as gc_mod

    monkeypatch.setattr(gc_mod, "_DISK_CHECK_DISABLED", False)

    class _FakeController:
        async def node_disk_usage_ratio(self):
            raise ApiException(status=500)
        async def gc_orphans(self, keep):
            return []

    monkeypatch.setattr(gc_mod.k8s_runner, "get_controller", lambda: _FakeController())
    monkeypatch.setattr(gc_mod.db, "get_pool", lambda: _FakePool())

    with structlog.testing.capture_logs() as log_records:
        await gc_mod.gc_once()

    # flag must remain False
    assert gc_mod._DISK_CHECK_DISABLED is False, (
        "ORCHN-S6: _DISK_CHECK_DISABLED must remain False after non-403 exception"
    )

    # must log debug with 'runner_gc.disk_check_failed'
    debug_events = [r["event"] for r in log_records if r.get("log_level") == "debug"]
    assert any("runner_gc.disk_check_failed" in e for e in debug_events), (
        f"ORCHN-S6: must log debug 'runner_gc.disk_check_failed' for non-403 exception; "
        f"debug events: {debug_events}"
    )

    # must NOT emit rbac_denied warning
    warning_events = [r["event"] for r in log_records if r.get("log_level") == "warning"]
    assert not any("runner_gc.disk_check_rbac_denied" in e for e in warning_events), (
        f"ORCHN-S6: must NOT log rbac_denied warning for non-403 exception; "
        f"warning events: {warning_events}"
    )


# ─── ORCHN-S7: 正常 ratio > threshold 触发 disk_pressure=True ───────────────


async def test_orchn_s7_high_ratio_triggers_disk_pressure(monkeypatch):
    """
    ORCHN-S7: node_disk_usage_ratio 返回 0.9（> threshold 0.8）时：
    - 必须记录 'runner_gc.disk_pressure' WARNING
    - 返回 disk_pressure=True
    """
    import orchestrator.runner_gc as gc_mod

    monkeypatch.setattr(gc_mod, "_DISK_CHECK_DISABLED", False)

    class _FakeController:
        async def node_disk_usage_ratio(self):
            return 0.9
        async def gc_orphans(self, keep):
            return []

    monkeypatch.setattr(gc_mod.k8s_runner, "get_controller", lambda: _FakeController())
    monkeypatch.setattr(gc_mod.db, "get_pool", lambda: _FakePool())

    fake_settings = _FakeSettings(runner_gc_disk_pressure_threshold=0.8)
    monkeypatch.setattr(gc_mod, "settings", fake_settings)

    with structlog.testing.capture_logs() as log_records:
        result = await gc_mod.gc_once()

    # must log disk_pressure warning
    warning_events = [r["event"] for r in log_records if r.get("log_level") == "warning"]
    assert any("runner_gc.disk_pressure" in e for e in warning_events), (
        f"ORCHN-S7: must log 'runner_gc.disk_pressure' warning when ratio=0.9 > 0.8; "
        f"warning events: {warning_events}"
    )

    # disk_pressure must be True
    disk_pressure = result.get("disk_pressure") if isinstance(result, dict) else getattr(result, "disk_pressure", None)
    assert disk_pressure is True, (
        f"ORCHN-S7: result disk_pressure must be True when ratio > threshold; got {result!r}"
    )


# ─── ORCHN-S8: alert 看板按 level=warning 过滤时看不到 rbac_denied ────────────


async def test_orchn_s8_warning_filter_excludes_rbac_denied(monkeypatch):
    """
    ORCHN-S8: 多次 gc_once 在 RBAC 缺 nodes:list 的场景下都不能让
    'runner_gc.disk_check_rbac_denied' 出现在 warning 流（哪怕第一次也不行）。
    模拟 alert dashboard / loki 按 log_level == "warning" 过滤的查询。
    """
    from kubernetes.client.exceptions import ApiException

    import orchestrator.runner_gc as gc_mod

    monkeypatch.setattr(gc_mod, "_DISK_CHECK_DISABLED", False)

    class _FakeController:
        async def node_disk_usage_ratio(self):
            raise ApiException(status=403)
        async def gc_orphans(self, keep):
            return []

    monkeypatch.setattr(gc_mod.k8s_runner, "get_controller", lambda: _FakeController())
    monkeypatch.setattr(gc_mod.db, "get_pool", lambda: _FakePool())

    with structlog.testing.capture_logs() as log_records:
        # 第一次 tick：触发 403 → log info → set flag
        await gc_mod.gc_once()
        # 模拟后续 N 次 tick（flag 已 True，应一律 short-circuit）
        for _ in range(5):
            await gc_mod.gc_once()

    warning_records = [
        r for r in log_records
        if r.get("log_level") == "warning"
        and "runner_gc.disk_check_rbac_denied" in r.get("event", "")
    ]
    assert warning_records == [], (
        f"ORCHN-S8: alert filter (level=warning) MUST NOT see "
        f"'runner_gc.disk_check_rbac_denied'; got: {warning_records}"
    )
