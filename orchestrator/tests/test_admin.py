"""admin endpoints 烟测：emit / escalate / complete / resume / get-req."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from orchestrator.admin import (
    CompleteBody,
    EmitBody,
    EscalateBody,
    ResumeBody,
    _FakeBody,
    complete_req,
    force_escalate,
    get_req,
    resume_req,
)
from orchestrator.state import Event, ReqState


def test_fakebody_shape():
    fb = _FakeBody("REQ-1", "p")
    assert fb.issueId.startswith("admin-REQ-1")
    assert fb.projectId == "p"
    assert fb.event == "admin.inject"


def test_emit_body_validates():
    from pydantic import ValidationError
    EmitBody(event="ci-int.pass")
    with pytest.raises(ValidationError):
        EmitBody()  # missing event


@pytest.mark.asyncio
async def test_emit_unknown_event_400(monkeypatch):
    """未知 event 应返 400."""
    from orchestrator import admin as admin_mod
    # token 校验跳过
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)
    with pytest.raises(HTTPException) as ei:
        await admin_mod.emit_event("REQ-1", EmitBody(event="bogus"), authorization="Bearer x")
    assert ei.value.status_code == 400
    assert "valid" in ei.value.detail


@pytest.mark.asyncio
async def test_force_escalate_404_when_not_found(monkeypatch):
    from orchestrator import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    async def _no(*a, **kw):
        return None
    monkeypatch.setattr("orchestrator.admin.req_state.get", _no)

    class FakePool:
        pass
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: FakePool())

    with pytest.raises(HTTPException) as ei:
        await force_escalate("REQ-X", authorization="Bearer x")
    assert ei.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# /admin/req/{req_id}/escalate runner-cleanup tests
# (REQ-cleanup-runner-zombie-1777170378)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_force_escalate_marks_escalated_and_triggers_cleanup(monkeypatch):
    """FRE-S1 happy path: force_escalate 改 state=escalated + schedule
    cleanup_runner_on_terminal(ReqState.ESCALATED) fire-and-forget.

    所有走 transition 进 ESCALATED 的路径都被 engine 自动清；force_escalate 是
    raw SQL UPDATE 绕开 engine，必须自己起 cleanup task — 否则 Pod 以 zombie
    存活整个 PVC retention 期。
    """
    from dataclasses import dataclass
    from datetime import UTC
    from datetime import datetime as _dt

    from orchestrator import admin as admin_mod

    @dataclass
    class _Row:
        req_id: str
        project_id: str
        state: ReqState
        context: dict
        history: list
        created_at: _dt
        updated_at: _dt

    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    row = _Row(
        req_id="REQ-X", project_id="p", state=ReqState.ANALYZING,
        context={}, history=[],
        created_at=_dt.now(UTC), updated_at=_dt.now(UTC),
    )

    async def _get(_pool, _req_id):
        return row
    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)

    executed: list[tuple] = []

    class FakePool:
        async def execute(self, sql, *args):
            executed.append((sql, args))
            return "UPDATE 1"

    pool = FakePool()
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)

    # ANALYZING は STATE_TO_STAGE に含まれるので close_latest_stage_run が呼ばれる
    async def _fake_close(_pool, req_id, stage, *, outcome, fail_reason=None):
        pass
    monkeypatch.setattr("orchestrator.admin.stage_runs.close_latest_stage_run", _fake_close)

    cleanup_calls: list[tuple] = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append((req_id, terminal_state))

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    result = await force_escalate("REQ-X", authorization="Bearer x")

    assert result == {"action": "force_escalated", "from_state": "analyzing", "kind": "admin"}
    # SQL UPDATE 跑了一次
    assert len(executed) == 1
    sql, _args = executed[0]
    assert "UPDATE req_state" in sql
    assert "state='escalated'" in sql
    # cleanup task 已 schedule，跑一帧让它执行
    await asyncio.sleep(0)
    assert cleanup_calls == [("REQ-X", ReqState.ESCALATED)], (
        "force_escalate must schedule _cleanup_runner_on_terminal with "
        "ReqState.ESCALATED so the runner Pod is deleted (PVC retained for "
        "human debug); without this, Pod stays alive for the entire PVC "
        "retention window as a zombie."
    )


@pytest.mark.asyncio
async def test_force_escalate_noop_when_already_escalated_no_cleanup(monkeypatch):
    """FRE-S2 noop: state=escalated → 200 noop, 没 SQL UPDATE 没 cleanup task.

    幂等：第二次 force_escalate 不该重复起 cleanup（POD 早已被首次 cleanup 删过；
    重起一轮纯浪费 K8s API + 多一行 warning 日志）。
    """
    from dataclasses import dataclass
    from datetime import UTC
    from datetime import datetime as _dt

    from orchestrator import admin as admin_mod

    @dataclass
    class _Row:
        req_id: str
        project_id: str
        state: ReqState
        context: dict
        history: list
        created_at: _dt
        updated_at: _dt

    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    row = _Row(
        req_id="REQ-X", project_id="p", state=ReqState.ESCALATED,
        context={}, history=[],
        created_at=_dt.now(UTC), updated_at=_dt.now(UTC),
    )

    async def _get(_pool, _req_id):
        return row
    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)

    executed: list = []

    class FakePool:
        async def execute(self, sql, *args):
            executed.append((sql, args))
            return "UPDATE 1"

    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: FakePool())

    cleanup_calls: list = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append((req_id, terminal_state))

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    result = await force_escalate("REQ-X", authorization="Bearer x")

    assert result == {"action": "noop", "state": "already escalated"}
    assert executed == []
    # 跑一帧确认没起 cleanup task
    await asyncio.sleep(0)
    assert cleanup_calls == []


@pytest.mark.asyncio
async def test_get_req_404(monkeypatch):
    from orchestrator import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    async def _no(*a, **kw):
        return None
    monkeypatch.setattr("orchestrator.admin.req_state.get", _no)

    class FakePool:
        pass
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: FakePool())

    with pytest.raises(HTTPException) as ei:
        await get_req("REQ-X", authorization="Bearer x")
    assert ei.value.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# /admin/req/{req_id}/complete tests (REQ-admin-complete-endpoint-1777117709)
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class _FakeRow:
    req_id: str
    project_id: str
    state: ReqState
    history: list = None
    context: dict = None
    created_at: datetime = None
    updated_at: datetime = None

    def __post_init__(self):
        if self.history is None:
            self.history = []
        if self.context is None:
            self.context = {}
        if self.created_at is None:
            self.created_at = datetime.now(UTC)
        if self.updated_at is None:
            self.updated_at = datetime.now(UTC)


class _FakePool:
    """记录 execute 调用 + 返回 fake 'UPDATE 1' 默认。"""

    def __init__(self, execute_return: str = "UPDATE 1"):
        self.executed: list[tuple] = []
        self._execute_return = execute_return

    async def execute(self, sql: str, *args) -> str:
        self.executed.append((sql, args))
        return self._execute_return


def _bypass_auth_and_pool(monkeypatch, pool: _FakePool, row: _FakeRow | None):
    from orchestrator import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    async def _get(_pool, _req_id):
        return row
    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)


@pytest.mark.asyncio
async def test_complete_404_when_not_found(monkeypatch):
    """ACE-S4: REQ 不存在 → 404."""
    pool = _FakePool()
    _bypass_auth_and_pool(monkeypatch, pool, row=None)

    with pytest.raises(HTTPException) as ei:
        await complete_req("REQ-MISSING", body=None, authorization="Bearer x")
    assert ei.value.status_code == 404
    assert "not found" in ei.value.detail
    assert pool.executed == []  # 没改 DB


@pytest.mark.asyncio
async def test_complete_noop_when_already_done(monkeypatch):
    """ACE-S2: state=done → 200 noop, 没 SQL UPDATE 没 cleanup task."""
    pool = _FakePool()
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.DONE)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    cleanup_calls = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append((req_id, terminal_state))

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    result = await complete_req("REQ-X", body=None, authorization="Bearer x")
    assert result == {"action": "noop", "state": "already done"}
    assert pool.executed == []
    # 让 event loop 跑一帧确认没起 task
    await asyncio.sleep(0)
    assert cleanup_calls == []


@pytest.mark.asyncio
async def test_complete_409_when_not_escalated(monkeypatch):
    """ACE-S3: state=analyzing → 409 with hint about /escalate first."""
    pool = _FakePool()
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ANALYZING)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    with pytest.raises(HTTPException) as ei:
        await complete_req("REQ-X", body=None, authorization="Bearer x")
    assert ei.value.status_code == 409
    assert "analyzing" in ei.value.detail
    assert "/escalate" in ei.value.detail
    assert pool.executed == []


@pytest.mark.asyncio
async def test_complete_marks_done_and_triggers_cleanup(monkeypatch):
    """ACE-S1: 成功路径——SQL UPDATE 写 done + cleanup task scheduled."""
    pool = _FakePool(execute_return="UPDATE 1")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    cleanup_calls = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append((req_id, terminal_state))

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    result = await complete_req("REQ-X", body=None, authorization="Bearer x")

    assert result["action"] == "completed"
    assert result["from_state"] == "escalated"
    # SQL 调一次
    assert len(pool.executed) == 1
    sql, args = pool.executed[0]
    assert "UPDATE req_state" in sql
    assert "state='done'" in sql
    assert "WHERE req_id = $1 AND state = $4" in sql
    assert args[0] == "REQ-X"
    assert args[3] == ReqState.ESCALATED.value
    # cleanup task 已 schedule，跑一帧让它执行
    await asyncio.sleep(0)
    assert cleanup_calls == [("REQ-X", ReqState.DONE)]


@pytest.mark.asyncio
async def test_complete_writes_reason_in_context(monkeypatch):
    """ACE-S5: body.reason → ctx 里写 completed_reason_detail."""
    import json as _json

    pool = _FakePool(execute_return="UPDATE 1")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    async def _fake_cleanup(req_id, terminal_state):
        pass

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    body = CompleteBody(reason="superseded by REQ-Y")
    result = await complete_req("REQ-X", body=body, authorization="Bearer x")

    assert result["reason"] == "superseded by REQ-Y"
    _sql, args = pool.executed[0]
    # args[2] 是 ctx_patch JSON
    ctx = _json.loads(args[2])
    assert ctx["completed_reason"] == "admin"
    assert ctx["completed_reason_detail"] == "superseded by REQ-Y"
    assert ctx["completed_from_state"] == "escalated"


@pytest.mark.asyncio
async def test_complete_no_reason_omits_detail_field(monkeypatch):
    """ACE-S6: 没 reason → ctx 不写 completed_reason_detail（不留 null 字段）."""
    import json as _json

    pool = _FakePool(execute_return="UPDATE 1")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    async def _fake_cleanup(req_id, terminal_state):
        pass

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    await complete_req("REQ-X", body=None, authorization="Bearer x")
    _sql, args = pool.executed[0]
    ctx = _json.loads(args[2])
    assert ctx["completed_reason"] == "admin"
    assert "completed_reason_detail" not in ctx


@pytest.mark.asyncio
async def test_complete_history_entry_appended(monkeypatch):
    """ACE-S1: history 行追加 admin.complete event marker."""
    import json as _json

    pool = _FakePool(execute_return="UPDATE 1")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)
    _bypass_auth_and_pool(monkeypatch, pool, row=row)

    async def _fake_cleanup(req_id, terminal_state):
        pass

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    await complete_req("REQ-X", body=None, authorization="Bearer x")
    _sql, args = pool.executed[0]
    history = _json.loads(args[1])
    assert isinstance(history, list) and len(history) == 1
    entry = history[0]
    assert entry["from"] == "escalated"
    assert entry["to"] == "done"
    assert entry["event"] == "admin.complete"
    assert entry["action"] is None
    assert "ts" in entry


@pytest.mark.asyncio
async def test_complete_concurrent_update_lost_returns_409(monkeypatch):
    """failure_modes.concurrent_complete: SQL UPDATE 影响 0 行（已被另一 caller 改） → 409 或 noop."""
    pool = _FakePool(execute_return="UPDATE 0")
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ESCALATED)

    from orchestrator import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    # 第一次 get 返 escalated（precondition pass），UPDATE 后 reload 返 done（被并发改了）
    call_count = {"n": 0}

    async def _get(_pool, _req_id):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return row
        return _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.DONE)

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)

    # 并发对手已经把它推到 done → 我们应回 noop（200）
    result = await complete_req("REQ-X", body=None, authorization="Bearer x")
    assert result["action"] == "noop"


@pytest.mark.asyncio
async def test_complete_auth_check_before_db(monkeypatch):
    """ACE-S7: _verify_token 是第一步，失败时不调 req_state.get / pool."""
    from orchestrator import admin as admin_mod

    def _bad_token(_):
        raise HTTPException(status_code=401, detail="bad token")

    monkeypatch.setattr(admin_mod, "_verify_token", _bad_token)

    get_called = []

    async def _get(*a, **kw):
        get_called.append(1)
        return None

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: _FakePool())

    with pytest.raises(HTTPException) as ei:
        await complete_req("REQ-X", body=None, authorization=None)
    assert ei.value.status_code == 401
    assert get_called == []


def test_complete_body_optional_reason():
    """CompleteBody 的 reason 可省略."""
    assert CompleteBody().reason is None
    assert CompleteBody(reason="x").reason == "x"


# ═══════════════════════════════════════════════════════════════════════
# /admin/req/{req_id}/resume tests (REQ-admin-resume-escalated-1777123726)
# ═══════════════════════════════════════════════════════════════════════


def _bypass_auth_pool_and_update(
    monkeypatch,
    pool: _FakePool,
    row: _FakeRow | None,
    second_row: _FakeRow | None = None,
):
    """resume_req 需要：_verify_token + db.get_pool + req_state.get（两次）+
    req_state.update_context。第二次 get 默认返跟第一次同 row（覆盖原 ctx）。
    """
    from orchestrator import admin as admin_mod
    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    calls = {"n": 0}
    second = second_row if second_row is not None else row

    async def _get(_pool, _req_id):
        calls["n"] += 1
        return row if calls["n"] == 1 else second

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)

    update_calls: list = []

    async def _update(_pool, _req_id, patch):
        update_calls.append(patch)

    monkeypatch.setattr("orchestrator.admin.req_state.update_context", _update)
    return update_calls


@pytest.mark.asyncio
async def test_resume_404_when_not_found(monkeypatch):
    """ARE-S4: REQ 不存在 → 404."""
    pool = _FakePool()
    _bypass_auth_pool_and_update(monkeypatch, pool, row=None)

    with pytest.raises(HTTPException) as ei:
        await resume_req(
            "REQ-MISSING", body=ResumeBody(action="pass"),
            authorization="Bearer x",
        )
    assert ei.value.status_code == 404
    assert "not found" in ei.value.detail


@pytest.mark.asyncio
async def test_resume_409_when_not_escalated(monkeypatch):
    """ARE-S3: state=analyzing → 409 with hint about /escalate first."""
    pool = _FakePool()
    row = _FakeRow(req_id="REQ-X", project_id="p", state=ReqState.ANALYZING)
    _bypass_auth_pool_and_update(monkeypatch, pool, row=row)

    step_calls: list = []

    async def _step(*a, **kw):
        step_calls.append((a, kw))

    monkeypatch.setattr("orchestrator.admin.engine.step", _step)

    with pytest.raises(HTTPException) as ei:
        await resume_req(
            "REQ-X", body=ResumeBody(action="pass"), authorization="Bearer x",
        )
    assert ei.value.status_code == 409
    assert "analyzing" in ei.value.detail
    assert "/escalate" in ei.value.detail
    assert step_calls == []


@pytest.mark.asyncio
async def test_resume_400_when_pass_missing_stage(monkeypatch):
    """ARE-S5: action=pass + ctx 没 verifier_stage + body 没 stage → 400."""
    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="p", state=ReqState.ESCALATED,
        context={},  # 没 verifier_stage
    )
    _bypass_auth_pool_and_update(monkeypatch, pool, row=row)

    step_calls: list = []

    async def _step(*a, **kw):
        step_calls.append((a, kw))

    monkeypatch.setattr("orchestrator.admin.engine.step", _step)

    with pytest.raises(HTTPException) as ei:
        await resume_req(
            "REQ-X", body=ResumeBody(action="pass"), authorization="Bearer x",
        )
    assert ei.value.status_code == 400
    assert "verifier_stage" in ei.value.detail
    assert step_calls == []


@pytest.mark.asyncio
async def test_resume_pass_dispatches_verify_pass_event(monkeypatch):
    """ARE-S1: state=escalated, ctx.verifier_stage=staging_test, action=pass →
    engine.step 被调一次 with event=VERIFY_PASS."""
    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="p", state=ReqState.ESCALATED,
        context={"verifier_stage": "staging_test"},
    )
    update_calls = _bypass_auth_pool_and_update(monkeypatch, pool, row=row)

    step_calls: list = []

    async def _step(*a, **kw):
        step_calls.append(kw)
        return {"action": "no-op", "next_state": "review-running"}

    monkeypatch.setattr("orchestrator.admin.engine.step", _step)

    result = await resume_req(
        "REQ-X", body=ResumeBody(action="pass"), authorization="Bearer x",
    )

    assert result["action"] == "resumed"
    assert result["from_state"] == "escalated"
    assert result["event"] == "verify.pass"
    assert "chained" in result
    # engine.step 被调一次，event 是 VERIFY_PASS
    assert len(step_calls) == 1
    assert step_calls[0]["event"] == Event.VERIFY_PASS
    assert step_calls[0]["cur_state"] == ReqState.ESCALATED
    assert step_calls[0]["req_id"] == "REQ-X"
    # ctx 被 patch（resumed_by_admin + resume_action）
    assert len(update_calls) == 1
    assert update_calls[0]["resumed_by_admin"] is True
    assert update_calls[0]["resume_action"] == "pass"
    assert "verifier_stage" not in update_calls[0]  # 没传 body.stage 不应 patch


@pytest.mark.asyncio
async def test_resume_pass_with_body_stage_overrides_ctx(monkeypatch):
    """ARE-S6: body.stage="pr_ci" → ctx 被 patch verifier_stage."""
    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="p", state=ReqState.ESCALATED,
        context={"verifier_stage": "staging_test"},  # 旧 stage
    )
    update_calls = _bypass_auth_pool_and_update(monkeypatch, pool, row=row)

    async def _step(*a, **kw):
        return {"action": "no-op"}

    monkeypatch.setattr("orchestrator.admin.engine.step", _step)

    body = ResumeBody(action="pass", stage="pr_ci")
    await resume_req("REQ-X", body=body, authorization="Bearer x")

    assert update_calls[0]["verifier_stage"] == "pr_ci"


@pytest.mark.asyncio
async def test_resume_fix_needed_dispatches_verify_fix_needed_event(monkeypatch):
    """ARE-S2: action=fix-needed → engine.step with event=VERIFY_FIX_NEEDED."""
    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="p", state=ReqState.ESCALATED,
        context={
            "verifier_stage": "dev_cross_check",
            "verifier_fixer": "dev",
        },
    )
    _bypass_auth_pool_and_update(monkeypatch, pool, row=row)

    step_calls: list = []

    async def _step(*a, **kw):
        step_calls.append(kw)
        return {"action": "start_fixer"}

    monkeypatch.setattr("orchestrator.admin.engine.step", _step)

    body = ResumeBody(action="fix-needed")
    result = await resume_req("REQ-X", body=body, authorization="Bearer x")

    assert result["event"] == "verify.fix-needed"
    assert step_calls[0]["event"] == Event.VERIFY_FIX_NEEDED


@pytest.mark.asyncio
async def test_resume_writes_audit_to_context(monkeypatch):
    """ARE-S7: body.reason="..." → ctx.resume_reason / resumed_by_admin patch."""
    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="p", state=ReqState.ESCALATED,
        context={"verifier_stage": "staging_test"},
    )
    update_calls = _bypass_auth_pool_and_update(monkeypatch, pool, row=row)

    async def _step(*a, **kw):
        return {}

    monkeypatch.setattr("orchestrator.admin.engine.step", _step)

    body = ResumeBody(action="pass", reason="GHA infra flake confirmed")
    await resume_req("REQ-X", body=body, authorization="Bearer x")

    patch = update_calls[0]
    assert patch["resumed_by_admin"] is True
    assert patch["resume_action"] == "pass"
    assert patch["resume_reason"] == "GHA infra flake confirmed"


@pytest.mark.asyncio
async def test_resume_fixer_override_in_body(monkeypatch):
    """body.fixer="spec" → ctx.verifier_fixer patched."""
    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="p", state=ReqState.ESCALATED,
        context={"verifier_stage": "spec_lint", "verifier_fixer": "dev"},
    )
    update_calls = _bypass_auth_pool_and_update(monkeypatch, pool, row=row)

    async def _step(*a, **kw):
        return {}

    monkeypatch.setattr("orchestrator.admin.engine.step", _step)

    body = ResumeBody(action="fix-needed", fixer="spec")
    await resume_req("REQ-X", body=body, authorization="Bearer x")
    assert update_calls[0]["verifier_fixer"] == "spec"


def test_resume_body_invalid_action_raises():
    """ResumeBody schema 拒绝 action ∉ {pass, fix-needed}."""
    with pytest.raises(ValidationError):
        ResumeBody(action="bogus")


def test_resume_body_invalid_fixer_raises():
    """ResumeBody schema 拒绝 fixer ∉ {dev, spec}."""
    with pytest.raises(ValidationError):
        ResumeBody(action="fix-needed", fixer="qa")


def test_resume_body_action_required():
    """ResumeBody.action 是必传，缺 → ValidationError."""
    with pytest.raises(ValidationError):
        ResumeBody()


@pytest.mark.asyncio
async def test_resume_auth_check_before_db(monkeypatch):
    """ARE-S8: bad token → 401，没 req_state.get / engine.step."""
    from orchestrator import admin as admin_mod

    def _bad_token(_):
        raise HTTPException(status_code=401, detail="bad token")

    monkeypatch.setattr(admin_mod, "_verify_token", _bad_token)

    get_called: list = []
    step_called: list = []

    async def _get(*a, **kw):
        get_called.append(1)
        return None

    async def _step(*a, **kw):
        step_called.append(1)

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: _FakePool())
    monkeypatch.setattr("orchestrator.admin.engine.step", _step)

    with pytest.raises(HTTPException) as ei:
        await resume_req(
            "REQ-X", body=ResumeBody(action="pass"), authorization=None,
        )
    assert ei.value.status_code == 401
    assert get_called == []
    assert step_called == []


# ─── runner endpoint rename：路径切到 /runner-pause / /runner-resume ─────


@pytest.mark.asyncio
async def test_force_escalate_closes_current_stage_run(monkeypatch):
    """FRE-S3: force_escalate 在 raw SQL UPDATE 前调 close_latest_stage_run 收尾当前 stage。

    force_escalate 是 raw SQL UPDATE 绕过 engine._record_stage_transitions，
    正在跑的 stage_run（ended_at IS NULL）永远不会被正常 close 路径关闭，
    留在 DB 里污染 stage_stats 指标（duration_sec=NULL / outcome=NULL）。

    修复：用 engine.STATE_TO_STAGE.get(row.state) 取当前 stage，
    调 close_latest_stage_run(outcome='escalated', fail_reason='admin-force-escalate')
    然后再做 raw SQL UPDATE。
    """
    from dataclasses import dataclass
    from datetime import UTC
    from datetime import datetime as _dt

    from orchestrator import admin as admin_mod

    @dataclass
    class _Row:
        req_id: str
        project_id: str
        state: ReqState
        context: dict
        history: list
        created_at: _dt
        updated_at: _dt

    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    row = _Row(
        req_id="REQ-X", project_id="p", state=ReqState.ANALYZING,
        context={}, history=[],
        created_at=_dt.now(UTC), updated_at=_dt.now(UTC),
    )

    async def _get(_pool, _req_id):
        return row
    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)

    close_calls: list[tuple] = []

    async def _fake_close(_pool, req_id, stage, *, outcome, fail_reason=None):
        close_calls.append((req_id, stage, outcome, fail_reason))

    monkeypatch.setattr(
        "orchestrator.admin.stage_runs.close_latest_stage_run", _fake_close,
    )

    call_order: list[str] = []

    class FakePool:
        async def execute(self, sql, *args):
            call_order.append("sql_update")
            return "UPDATE 1"

    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: FakePool())

    async def _fake_cleanup(req_id, terminal_state):
        pass
    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    # intercept close to track order
    original_fake_close = _fake_close

    async def _ordered_close(_pool, req_id, stage, *, outcome, fail_reason=None):
        call_order.append("stage_run_close")
        await original_fake_close(_pool, req_id, stage, outcome=outcome, fail_reason=fail_reason)

    monkeypatch.setattr(
        "orchestrator.admin.stage_runs.close_latest_stage_run", _ordered_close,
    )

    result = await force_escalate("REQ-X", authorization="Bearer x")

    assert result == {"action": "force_escalated", "from_state": "analyzing", "kind": "admin"}
    # close_latest_stage_run 调了一次：req_id, stage="analyze", outcome="escalated"
    assert close_calls == [("REQ-X", "analyze", "escalated", "admin-force-escalate")], (
        "force_escalate must call close_latest_stage_run with the stage derived "
        "from STATE_TO_STAGE, outcome='escalated', fail_reason='admin-force-escalate'"
    )
    # close 必须在 SQL UPDATE 之前执行（先收尾 stage_run 再改 state）
    assert call_order == ["stage_run_close", "sql_update"], (
        "close_latest_stage_run must be called BEFORE the raw SQL UPDATE "
        "so the stage_run is closed with the correct start→end duration"
    )


@pytest.mark.asyncio
async def test_force_escalate_no_close_when_state_has_no_stage(monkeypatch):
    """FRE-S4: INIT / 无 stage 对应 state 调 force_escalate → 不调 close_latest_stage_run。

    STATE_TO_STAGE 只覆盖 *_RUNNING 类 state；INIT/DONE/ESCALATED 等没对应 stage，
    不应该调 close（不会有 open stage_run）。
    """
    from dataclasses import dataclass
    from datetime import UTC
    from datetime import datetime as _dt

    from orchestrator import admin as admin_mod

    @dataclass
    class _Row:
        req_id: str
        project_id: str
        state: ReqState
        context: dict
        history: list
        created_at: _dt
        updated_at: _dt

    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    row = _Row(
        req_id="REQ-X", project_id="p", state=ReqState.INIT,
        context={}, history=[],
        created_at=_dt.now(UTC), updated_at=_dt.now(UTC),
    )

    async def _get(_pool, _req_id):
        return row
    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)

    close_calls: list = []

    async def _fake_close(_pool, req_id, stage, *, outcome, fail_reason=None):
        close_calls.append((req_id, stage, outcome))

    monkeypatch.setattr(
        "orchestrator.admin.stage_runs.close_latest_stage_run", _fake_close,
    )

    class FakePool:
        async def execute(self, sql, *args):
            return "UPDATE 1"

    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: FakePool())

    async def _fake_cleanup(req_id, terminal_state):
        pass
    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    result = await force_escalate("REQ-X", authorization="Bearer x")

    assert result == {"action": "force_escalated", "from_state": "init", "kind": "admin"}
    assert close_calls == [], (
        "When current state has no corresponding stage in STATE_TO_STAGE "
        "(e.g. INIT), close_latest_stage_run must NOT be called"
    )



# /admin/req/{req_id}/escalate — kind param + BKD sync (REQ-429)
# ═══════════════════════════════════════════════════════════════════════


def test_escalate_body_default_kind():
    """EKS-S1: EscalateBody 默认 kind="admin"."""
    assert EscalateBody().kind == "admin"


def test_escalate_body_custom_kind():
    """EKS-S2: EscalateBody 接受自定义 kind。"""
    assert EscalateBody(kind="infra-flake").kind == "infra-flake"


def _bypass_auth_pool_for_escalate(monkeypatch, pool, row, bkd_calls: list | None = None):
    """共用 patch helper for force_escalate 新测试。"""
    from orchestrator import admin as admin_mod

    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    async def _get(_pool, _req_id):
        return row

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)

    async def _fake_cleanup(req_id, terminal_state):
        pass

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    recorded = bkd_calls if bkd_calls is not None else []

    class _FakeBKD:
        async def merge_tags_and_update(self, proj, issue_id, *, add=None, remove=None, status_id=None):
            recorded.append({"proj": proj, "issue_id": issue_id, "add": add, "status_id": status_id})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            ...

    monkeypatch.setattr("orchestrator.admin.BKDClient", lambda *a, **kw: _FakeBKD())
    return recorded


@pytest.mark.asyncio
async def test_force_escalate_custom_kind_in_sql_and_response(monkeypatch):
    """EKS-S3: kind="infra-flake" → SQL ctx contains escalated_reason + response has kind."""
    import json as _json

    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="p", state=ReqState.ANALYZING,
        context={"intent_issue_id": "issue-abc"},
    )
    _bypass_auth_pool_for_escalate(monkeypatch, pool, row)

    result = await force_escalate(
        "REQ-X", body=EscalateBody(kind="infra-flake"), authorization="Bearer x",
    )

    assert result == {"action": "force_escalated", "from_state": "analyzing", "kind": "infra-flake"}
    assert len(pool.executed) == 1
    _sql, args = pool.executed[0]
    ctx = _json.loads(args[1])
    assert ctx["escalated_reason"] == "infra-flake"


@pytest.mark.asyncio
async def test_force_escalate_bkd_sync_called_with_correct_args(monkeypatch):
    """EKS-S4: BKD sync 在 SQL UPDATE 后调用，传 add=[escalated, reason:<kind>] + status_id=review。"""
    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="myproj", state=ReqState.ANALYZING,
        context={"intent_issue_id": "intent-123"},
    )
    bkd_calls: list = []
    _bypass_auth_pool_for_escalate(monkeypatch, pool, row, bkd_calls)

    await force_escalate(
        "REQ-X", body=EscalateBody(kind="watchdog-stuck"), authorization="Bearer x",
    )

    assert len(bkd_calls) == 1
    call = bkd_calls[0]
    assert call["proj"] == "myproj"
    assert call["issue_id"] == "intent-123"
    assert "escalated" in call["add"]
    assert "reason:watchdog-stuck" in call["add"]
    assert call["status_id"] == "review"


@pytest.mark.asyncio
async def test_force_escalate_bkd_uses_req_id_fallback_when_no_intent_issue(monkeypatch):
    """EKS-S5: ctx 没 intent_issue_id → fallback 用 req_id。"""
    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="p", state=ReqState.ANALYZING,
        context={},  # 没 intent_issue_id
    )
    bkd_calls: list = []
    _bypass_auth_pool_for_escalate(monkeypatch, pool, row, bkd_calls)

    await force_escalate("REQ-X", authorization="Bearer x")

    assert bkd_calls[0]["issue_id"] == "REQ-X"


@pytest.mark.asyncio
async def test_force_escalate_bkd_failure_does_not_block(monkeypatch):
    """EKS-S6: BKD sync 失败 → 不影响 SQL UPDATE 结果 + cleanup task。"""
    import json as _json

    pool = _FakePool()
    row = _FakeRow(
        req_id="REQ-X", project_id="p", state=ReqState.ANALYZING,
        context={},
    )
    from orchestrator import admin as admin_mod

    monkeypatch.setattr(admin_mod, "_verify_token", lambda x: None)

    async def _get(_pool, _req_id):
        return row

    monkeypatch.setattr("orchestrator.admin.req_state.get", _get)
    monkeypatch.setattr("orchestrator.admin.db.get_pool", lambda: pool)

    cleanup_calls: list = []

    async def _fake_cleanup(req_id, terminal_state):
        cleanup_calls.append((req_id, terminal_state))

    monkeypatch.setattr(
        "orchestrator.admin.engine._cleanup_runner_on_terminal", _fake_cleanup,
    )

    class _FailBKD:
        async def merge_tags_and_update(self, *a, **kw):
            raise RuntimeError("BKD unreachable")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            ...

    monkeypatch.setattr("orchestrator.admin.BKDClient", lambda *a, **kw: _FailBKD())

    result = await force_escalate("REQ-X", authorization="Bearer x")

    # SQL UPDATE 成功
    assert result["action"] == "force_escalated"
    assert len(pool.executed) == 1
    _sql, args = pool.executed[0]
    ctx = _json.loads(args[1])
    assert ctx["escalated_reason"] == "admin"
    # cleanup task 还是起了
    await asyncio.sleep(0)
    assert cleanup_calls == [("REQ-X", ReqState.ESCALATED)]

def test_admin_route_table_runner_pause_renamed():
    """ARE-S9: /admin/req/{req_id}/runner-pause 已注册，旧 /pause 不存在."""
    from orchestrator.admin import admin as admin_router

    paths = {
        r.path
        for r in admin_router.routes
        if hasattr(r, "path") and "POST" in (getattr(r, "methods", set()) or set())
    }
    assert "/admin/req/{req_id}/runner-pause" in paths
    assert "/admin/req/{req_id}/pause" not in paths


def test_admin_route_table_runner_resume_renamed():
    """ARE-S10: /admin/req/{req_id}/runner-resume 已注册，bare /resume 现在
    指向新的 state-level resume_req（不是旧 resume_runner）."""
    from orchestrator.admin import admin as admin_router
    from orchestrator.admin import resume_req, resume_runner

    paths_to_endpoint: dict = {
        r.path: r.endpoint
        for r in admin_router.routes
        if hasattr(r, "path") and "POST" in (getattr(r, "methods", set()) or set())
    }
    # runner 路径绑 resume_runner
    assert paths_to_endpoint.get("/admin/req/{req_id}/runner-resume") is resume_runner
    # bare /resume 绑 state-level resume_req（不是 resume_runner）
    assert paths_to_endpoint.get("/admin/req/{req_id}/resume") is resume_req
    assert paths_to_endpoint.get("/admin/req/{req_id}/resume") is not resume_runner
