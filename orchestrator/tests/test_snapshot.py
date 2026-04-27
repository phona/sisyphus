"""snapshot._flatten + sync_once + orphan recovery 测试。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import ClassVar
from unittest.mock import AsyncMock

import pytest

from orchestrator import snapshot
from orchestrator.bkd import Issue
from orchestrator.state import Event, ReqState


def make_issue(**kw):
    base = dict(
        id="i1", project_id="p", issue_number=1, title="t", status_id="working",
        tags=[], session_status=None, description=None,
        created_at="2026-04-21T01:00:00Z", updated_at="2026-04-21T01:05:00Z",
    )
    base.update(kw)
    return Issue(**base)


def test_flatten_basic():
    i = make_issue(id="dev-9", tags=["dev", "REQ-9"])
    row = snapshot._flatten(i)
    assert row["issue_id"] == "dev-9"
    assert row["req_id"] == "REQ-9"
    assert row["stage"] == "dev"
    assert row["round"] is None
    assert row["target"] is None


def test_flatten_ci_with_round():
    i = make_issue(id="ci-1", tags=["ci", "REQ-9", "target:integration", "parent:dev",
                                     "parent-id:dev-9", "round-2"])
    row = snapshot._flatten(i)
    assert row["stage"] == "ci"
    assert row["round"] == 2
    assert row["target"] == "integration"
    assert row["parent_stage"] == "dev"
    assert row["parent_issue_id"] == "dev-9"


def test_flatten_no_recognized_tags():
    i = make_issue(id="x", tags=["weird"])
    row = snapshot._flatten(i)
    assert row["stage"] is None
    assert row["req_id"] is None


class FakeMainPool:
    """模拟 main pool 的 fetch SELECT DISTINCT project_id。"""
    def __init__(self, project_ids: list[str]):
        self._rows = [{"project_id": p} for p in project_ids]

    async def fetch(self, sql, *args):
        return self._rows


@pytest.mark.asyncio
async def test_sync_once_no_projects_yet(monkeypatch):
    """req_state 没记录时返 0，不报错。"""
    class _ObsPool:
        pass
    monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: _ObsPool())
    monkeypatch.setattr(snapshot.db, "get_pool", lambda: FakeMainPool([]))
    n = await snapshot.sync_once()
    assert n == 0


@pytest.mark.asyncio
async def test_sync_once_filters_excluded_projects(monkeypatch):
    """exclude 清单里的 project_id 不发 list_issues，也不报错。"""
    captured: list[tuple] = []

    class FakeConn:
        async def execute(self, sql, *args):
            captured.append((sql.strip()[:30], args))

        def transaction(self):
            @asynccontextmanager
            async def _t():
                yield
            return _t()

    class FakeObsPool:
        def acquire(self):
            @asynccontextmanager
            async def _a():
                yield FakeConn()
            return _a()

    fake_bkd = AsyncMock()
    fake_bkd.list_issues = AsyncMock(return_value=[
        make_issue(id="a", tags=["dev", "REQ-1"]),
    ])

    @asynccontextmanager
    async def _client_ctx(*a, **kw):
        yield fake_bkd

    # orphan pass 上 req_state.get → 模拟"不是 orphan"（已有 row）
    async def _stub_get(pool, req_id):
        return object()
    monkeypatch.setattr(snapshot.req_state, "get", _stub_get)

    monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: FakeObsPool())
    monkeypatch.setattr(snapshot.db, "get_pool",
                        lambda: FakeMainPool(["alive-1", "77k9z58j"]))
    monkeypatch.setattr(snapshot, "BKDClient", _client_ctx)
    monkeypatch.setattr(snapshot.settings, "snapshot_exclude_project_ids",
                        ["77k9z58j"])

    n = await snapshot.sync_once()

    assert n == 1
    # 只调一次：alive-1。77k9z58j 被过滤
    assert fake_bkd.list_issues.await_count == 1
    assert fake_bkd.list_issues.await_args.args[0] == "alive-1"


@pytest.mark.asyncio
async def test_sync_once_all_projects_excluded(monkeypatch):
    """所有 project 都被排除 → 不调 BKD，返 0。"""
    fake_bkd = AsyncMock()
    fake_bkd.list_issues = AsyncMock()

    @asynccontextmanager
    async def _client_ctx(*a, **kw):
        yield fake_bkd

    monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: object())
    monkeypatch.setattr(snapshot.db, "get_pool",
                        lambda: FakeMainPool(["only-proj"]))
    monkeypatch.setattr(snapshot, "BKDClient", _client_ctx)
    monkeypatch.setattr(snapshot.settings, "snapshot_exclude_project_ids",
                        ["only-proj"])

    n = await snapshot.sync_once()

    assert n == 0
    fake_bkd.list_issues.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_once_upserts_per_project(monkeypatch):
    """两个 project 各 1 issue，应当 UPSERT 2 行。"""
    captured: list[tuple] = []

    class FakeConn:
        async def execute(self, sql, *args):
            captured.append((sql.strip()[:30], args))

        def transaction(self):
            @asynccontextmanager
            async def _t():
                yield
            return _t()

    class FakeObsPool:
        def acquire(self):
            @asynccontextmanager
            async def _a():
                yield FakeConn()
            return _a()

    fake_bkd = AsyncMock()
    # 每次 list_issues 返一条
    fake_bkd.list_issues = AsyncMock(side_effect=[
        [make_issue(id="a", tags=["dev", "REQ-1"])],
        [make_issue(id="b", tags=["accept", "REQ-2", "result:pass"])],
    ])

    @asynccontextmanager
    async def _client_ctx(*a, **kw):
        yield fake_bkd

    # 两个 issue 都不是 intent:analyze orphan
    async def _stub_get(pool, req_id):
        return object()
    monkeypatch.setattr(snapshot.req_state, "get", _stub_get)

    monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: FakeObsPool())
    monkeypatch.setattr(snapshot.db, "get_pool", lambda: FakeMainPool(["proj-A", "proj-B"]))
    monkeypatch.setattr(snapshot, "BKDClient", _client_ctx)
    monkeypatch.setattr(snapshot.settings, "snapshot_exclude_project_ids", [])

    n = await snapshot.sync_once()
    assert n == 2
    assert fake_bkd.list_issues.await_count == 2  # 每个 project 一次
    assert len(captured) == 2
    assert all(sql.startswith("INSERT INTO bkd_snapshot") for sql, _ in captured)


# ─── orphan intent:analyze 恢复 ──────────────────────────────────────


class _StubInitRow:
    """fake req_state 行。class-level 字段用 ClassVar 满足 ruff RUF012。"""
    state: ClassVar[ReqState] = ReqState.INIT
    context: ClassVar[dict] = {}


class _StubExistingRow:
    state: ClassVar[ReqState] = ReqState.ANALYZING
    context: ClassVar[dict] = {}


def _orphan_test_setup(monkeypatch, *, issues, obs_present=True,
                       existing_req_ids: tuple[str, ...] = ()):
    """共享脚手架：固定 BKD list 返 issues，stub req_state / engine.step。

    返回 (engine_step_mock, insert_init_mock, fake_bkd)。
    """
    fake_bkd = AsyncMock()
    fake_bkd.list_issues = AsyncMock(return_value=list(issues))

    @asynccontextmanager
    async def _client_ctx(*a, **kw):
        yield fake_bkd

    inserted: set[str] = set()

    async def _stub_get(pool, req_id):
        # insert_init 跑过的 → 返回 INIT row 给后续 engine.step 用
        if req_id in inserted:
            return _StubInitRow()
        if req_id in existing_req_ids:
            return _StubExistingRow()
        return None

    insert_init_mock = AsyncMock()

    async def _stub_insert_init(pool, req_id, project_id, context=None, state=None):
        inserted.add(req_id)
        await insert_init_mock(pool, req_id, project_id, context=context, state=state)

    engine_step_mock = AsyncMock(return_value={})

    if obs_present:
        class _ObsPool:
            def acquire(self):
                @asynccontextmanager
                async def _a():
                    class _Conn:
                        async def execute(self, *a, **kw):
                            return None

                        def transaction(self):
                            @asynccontextmanager
                            async def _t():
                                yield
                            return _t()
                    yield _Conn()
                return _a()
        monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: _ObsPool())
    else:
        monkeypatch.setattr(snapshot.db, "get_obs_pool", lambda: None)

    monkeypatch.setattr(snapshot.db, "get_pool", lambda: FakeMainPool(["proj-X"]))
    monkeypatch.setattr(snapshot, "BKDClient", _client_ctx)
    monkeypatch.setattr(snapshot.settings, "snapshot_exclude_project_ids", [])
    monkeypatch.setattr(snapshot.req_state, "get", _stub_get)
    monkeypatch.setattr(snapshot.req_state, "insert_init", _stub_insert_init)
    monkeypatch.setattr(snapshot.engine, "step", engine_step_mock)

    return engine_step_mock, insert_init_mock, fake_bkd


@pytest.mark.asyncio
async def test_orphan_intent_analyze_triggers_when_missing_req_state(monkeypatch):
    """SNAP-ORPHAN-S1：intent:analyze tag + 没 req_state 行 → INTENT_ANALYZE 入栈。"""
    issue = make_issue(
        id="i-9", issue_number=9, title="fix something", status_id="working",
        tags=["intent:analyze"],
    )
    engine_step, insert_init, _ = _orphan_test_setup(monkeypatch, issues=[issue])

    n = await snapshot.sync_once()

    # snapshot_rows = 1 因为 orphan 触发后还会跑 obs UPSERT 那条
    assert n == 1
    insert_init.assert_awaited_once()
    init_args = insert_init.await_args
    assert init_args.args[1] == "REQ-9"
    assert init_args.args[2] == "proj-X"
    ctx = init_args.kwargs["context"]
    assert ctx["intent_issue_id"] == "i-9"
    assert ctx["snapshot_recovered"] is True

    engine_step.assert_awaited_once()
    step_kwargs = engine_step.await_args.kwargs
    assert step_kwargs["event"] == Event.INTENT_ANALYZE
    assert step_kwargs["cur_state"] == ReqState.INIT
    assert step_kwargs["req_id"] == "REQ-9"
    assert step_kwargs["project_id"] == "proj-X"
    body = step_kwargs["body"]
    assert body.issueId == "i-9"
    assert body.projectId == "proj-X"
    assert "intent:analyze" in body.tags


@pytest.mark.asyncio
async def test_orphan_intent_analyze_skipped_when_already_in_req_state(monkeypatch):
    """SNAP-ORPHAN-S2：req_state 已有行 → 不触发。"""
    issue = make_issue(
        id="i-42", issue_number=42, title="t", status_id="working",
        tags=["intent:analyze", "REQ-42"],
    )
    engine_step, insert_init, _ = _orphan_test_setup(
        monkeypatch, issues=[issue], existing_req_ids=("REQ-42",),
    )

    await snapshot.sync_once()

    insert_init.assert_not_awaited()
    engine_step.assert_not_awaited()


@pytest.mark.asyncio
async def test_orphan_intent_analyze_skipped_when_analyze_tag_present(monkeypatch):
    """SNAP-ORPHAN-S3：已经有 analyze tag → 不触发（已 rebrand 过）。"""
    issue = make_issue(
        id="i-7", issue_number=7, title="t", status_id="working",
        tags=["intent:analyze", "analyze", "REQ-7"],
    )
    engine_step, insert_init, _ = _orphan_test_setup(monkeypatch, issues=[issue])

    await snapshot.sync_once()

    insert_init.assert_not_awaited()
    engine_step.assert_not_awaited()


@pytest.mark.parametrize("status", ["done", "cancelled"])
@pytest.mark.asyncio
async def test_orphan_intent_analyze_skipped_when_status_done(monkeypatch, status):
    """SNAP-ORPHAN-S4：BKD 状态已 done/cancelled → 不触发。"""
    issue = make_issue(
        id="i-99", issue_number=99, title="t", status_id=status,
        tags=["intent:analyze"],
    )
    engine_step, insert_init, _ = _orphan_test_setup(monkeypatch, issues=[issue])

    await snapshot.sync_once()

    insert_init.assert_not_awaited()
    engine_step.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_once_runs_orphan_pass_without_obs_pool(monkeypatch):
    """SNAP-ORPHAN-S5：obs pool 缺席时 orphan 恢复仍跑，sync_once 返 0（无 UPSERT）。"""
    issue = make_issue(
        id="i-9", issue_number=9, title="fix something", status_id="working",
        tags=["intent:analyze"],
    )
    engine_step, insert_init, _ = _orphan_test_setup(
        monkeypatch, issues=[issue], obs_present=False,
    )

    n = await snapshot.sync_once()

    assert n == 0  # obs 缺席 → snapshot_rows = 0
    insert_init.assert_awaited_once()
    engine_step.assert_awaited_once()


@pytest.mark.asyncio
async def test_orphan_intent_analyze_failure_does_not_break_loop(monkeypatch):
    """单 issue 恢复抛异常时，下一条仍被处理；整轮不挂。"""
    bad_issue = make_issue(
        id="i-bad", issue_number=11, title="bad", status_id="working",
        tags=["intent:analyze"],
    )
    good_issue = make_issue(
        id="i-good", issue_number=12, title="good", status_id="working",
        tags=["intent:analyze"],
    )
    engine_step, _, _ = _orphan_test_setup(
        monkeypatch, issues=[bad_issue, good_issue],
    )
    # 第一次 step 抛，第二次正常返回
    engine_step.side_effect = [RuntimeError("boom"), {}]

    await snapshot.sync_once()

    # 两次都尝试了 engine.step（第一条抛异常被 catch，第二条正常）
    assert engine_step.await_count == 2
