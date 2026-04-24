"""alerts 模块测试（store + TG + escalate 集成）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Fake pool ──────────────────────────────────────────────────────────────
@dataclass
class FakePool:
    inserted: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    _next_id: int = 1

    async def fetchrow(self, sql, *args):
        self.inserted.append({"sql": sql, "args": args})
        result = {"id": self._next_id}
        self._next_id += 1
        return result

    async def execute(self, sql, *args):
        self.updated.append({"sql": sql, "args": args})
        return None

    async def fetch(self, sql, *args):
        return []


# ─── test_alerts_insert_and_query ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_insert_and_query():
    """store.alerts.insert_alert 写入 + 返回 id。"""
    from orchestrator.store import alerts as store_alerts

    pool = FakePool()
    alert_id = await store_alerts.insert_alert(
        pool,
        severity="critical",
        reason="runner-pod-not-ready",
        hint="image pull failed",
        suggested_action="kubectl describe pod",
        req_id="REQ-1",
        stage="runner-startup",
    )
    assert alert_id == 1
    assert len(pool.inserted) == 1
    args = pool.inserted[0]["args"]
    # severity, req_id, stage, reason, hint, suggested_action
    assert args[0] == "critical"
    assert args[1] == "REQ-1"
    assert args[2] == "runner-startup"
    assert args[3] == "runner-pod-not-ready"
    assert args[4] == "image pull failed"
    assert args[5] == "kubectl describe pod"


@pytest.mark.asyncio
async def test_alerts_mark_sent_to_tg():
    """store.alerts.mark_sent_to_tg 更新 sent_to_tg=TRUE。"""
    from orchestrator.store import alerts as store_alerts

    pool = FakePool()
    await store_alerts.mark_sent_to_tg(pool, 42)
    assert len(pool.updated) == 1
    assert "42" in str(pool.updated[0]["args"])


# ─── test_alerts_severity_check_constraint ──────────────────────────────────

@pytest.mark.asyncio
async def test_alerts_severity_check_constraint():
    """非法 severity 值应被 DB CHECK 约束拒绝（模拟 asyncpg 抛 IntegrityError）。"""
    import asyncpg
    from orchestrator.store import alerts as store_alerts

    class _ConstraintPool:
        async def fetchrow(self, sql, *args):
            raise asyncpg.IntegrityConstraintViolationError(
                "ERROR: new row violates check constraint"
            )

    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await store_alerts.insert_alert(
            _ConstraintPool(),
            severity="invalid",
            reason="test",
        )


# ─── test_tg_send_critical_no_config ────────────────────────────────────────

@pytest.mark.asyncio
async def test_tg_send_critical_no_config(monkeypatch):
    """settings 没有 token → return False，不抛异常。"""
    from orchestrator.alerts import tg

    monkeypatch.setattr("orchestrator.alerts.tg.settings.tg_bot_token", None)
    monkeypatch.setattr("orchestrator.alerts.tg.settings.tg_chat_id", None)

    result = await tg.send_critical("test message")
    assert result is False


# ─── test_tg_send_critical_mock_success ─────────────────────────────────────

@pytest.mark.asyncio
async def test_tg_send_critical_mock_success(monkeypatch):
    """mock httpx → 200 OK → return True。"""
    from orchestrator.alerts import tg

    monkeypatch.setattr("orchestrator.alerts.tg.settings.tg_bot_token", "fake-token")
    monkeypatch.setattr("orchestrator.alerts.tg.settings.tg_chat_id", "-123456789")

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("orchestrator.alerts.tg.httpx.AsyncClient", return_value=mock_client):
        result = await tg.send_critical("🚨 test alert")

    assert result is True
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    # verify payload
    payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
    assert payload["parse_mode"] == "Markdown"


@pytest.mark.asyncio
async def test_tg_send_critical_network_error_returns_false(monkeypatch):
    """httpx 抛网络异常 → return False，不向上传播。"""
    from orchestrator.alerts import tg

    monkeypatch.setattr("orchestrator.alerts.tg.settings.tg_bot_token", "fake-token")
    monkeypatch.setattr("orchestrator.alerts.tg.settings.tg_chat_id", "-123456789")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

    with patch("orchestrator.alerts.tg.httpx.AsyncClient", return_value=mock_client):
        result = await tg.send_critical("test")

    assert result is False


# ─── test_escalate_with_ctx_reason ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_escalate_with_ctx_reason(monkeypatch):
    """ctx.escalated_reason 优先于 body.event。"""
    from orchestrator.actions import escalate as escalate_mod

    @dataclass
    class _Body:
        projectId: str = "proj-1"
        issueId: str = "issue-1"
        event: str = "session.failed"

    reasons_used: list = []

    # mock BKD merge_tags_and_update
    class _FakeBKD:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            pass
        async def merge_tags_and_update(self, proj, iid, add):
            reasons_used.extend([t for t in add if t.startswith("reason:")])

    monkeypatch.setattr("orchestrator.actions.escalate.BKDClient", _FakeBKD)

    updates: list = []
    async def fake_update_context(pool, req_id, patch):
        updates.append(patch)
    monkeypatch.setattr("orchestrator.actions.escalate.req_state.update_context", fake_update_context)
    monkeypatch.setattr("orchestrator.actions.escalate.db.get_pool", lambda: object())

    alert_inserts: list = []
    async def fake_alert_insert(**kw):
        alert_inserts.append(kw)
        return 1
    monkeypatch.setattr("orchestrator.actions.escalate.alerts.insert", fake_alert_insert)
    monkeypatch.setattr("orchestrator.actions.escalate.tg.send_critical", AsyncMock(return_value=False))

    result = await escalate_mod.escalate(
        body=_Body(),
        req_id="REQ-1",
        tags=[],
        ctx={"escalated_reason": "runner-pod-not-ready", "intent_issue_id": "intent-1"},
    )

    assert result["reason"] == "runner-pod-not-ready"
    assert any("reason:runner-pod-not-ready" in r for r in reasons_used)


# ─── test_escalate_writes_alert_and_tg ──────────────────────────────────────

@pytest.mark.asyncio
async def test_escalate_writes_alert_and_tg(monkeypatch):
    """escalate action: alerts.insert 和 tg.send_critical 都被调用。"""
    from orchestrator.actions import escalate as escalate_mod

    @dataclass
    class _Body:
        projectId: str = "proj-1"
        issueId: str = "issue-1"
        event: str = "session.failed"

    class _FakeBKD:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def merge_tags_and_update(self, *a, **kw): pass

    monkeypatch.setattr("orchestrator.actions.escalate.BKDClient", _FakeBKD)
    monkeypatch.setattr("orchestrator.actions.escalate.req_state.update_context", AsyncMock())
    monkeypatch.setattr("orchestrator.actions.escalate.db.get_pool", lambda: object())

    alert_calls: list = []
    async def fake_insert(**kw):
        alert_calls.append(kw)
        return 1
    monkeypatch.setattr("orchestrator.actions.escalate.alerts.insert", fake_insert)

    tg_calls: list = []
    async def fake_tg(text):
        tg_calls.append(text)
        return True
    monkeypatch.setattr("orchestrator.actions.escalate.tg.send_critical", fake_tg)

    await escalate_mod.escalate(
        body=_Body(),
        req_id="REQ-2",
        tags=[],
        ctx={"escalated_reason": "watchdog-stuck-30min"},
    )

    assert len(alert_calls) == 1
    assert alert_calls[0]["severity"] == "critical"
    assert alert_calls[0]["reason"] == "watchdog-stuck-30min"
    assert len(tg_calls) == 1
    assert "REQ-2" in tg_calls[0]


# ─── test_diagnose_pod_pull_failed ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_diagnose_pod_pull_failed():
    """events 含 ImagePullBackOff → 返回 'image pull failed'。"""
    from orchestrator.k8s_runner import RunnerController

    mock_core_v1 = MagicMock()

    # 构造 fake event
    fake_event = MagicMock()
    fake_event.reason = "BackOff"
    fake_event.message = "Back-off pulling image: ImagePullBackOff"
    fake_event.last_timestamp = None

    mock_event_list = MagicMock()
    mock_event_list.items = [fake_event]
    mock_core_v1.list_namespaced_event.return_value = mock_event_list

    rc = RunnerController(
        namespace="sisyphus-runners",
        runner_image="test-image",
        runner_sa="sa",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="secret",
        core_v1=mock_core_v1,
    )

    result = await rc._diagnose_pod("test-pod")
    assert result == "image pull failed"


@pytest.mark.asyncio
async def test_diagnose_pod_pvc_pending():
    """events 含 WaitForFirstConsumer → 返回 PVC pending 字符串。"""
    from orchestrator.k8s_runner import RunnerController

    mock_core_v1 = MagicMock()

    fake_event = MagicMock()
    fake_event.reason = "FailedScheduling"
    fake_event.message = "WaitForFirstConsumer: no consumer yet"
    fake_event.last_timestamp = None

    mock_event_list = MagicMock()
    mock_event_list.items = [fake_event]
    mock_core_v1.list_namespaced_event.return_value = mock_event_list

    rc = RunnerController(
        namespace="sisyphus-runners",
        runner_image="test-image",
        runner_sa="sa",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="secret",
        core_v1=mock_core_v1,
    )

    result = await rc._diagnose_pod("test-pod")
    assert "PVC pending" in result


@pytest.mark.asyncio
async def test_diagnose_pod_api_failure_returns_diagnostic_failed():
    """K8s API 抛异常 → 返回 'diagnostic failed'（不向上传播）。"""
    from orchestrator.k8s_runner import RunnerController

    mock_core_v1 = MagicMock()
    mock_core_v1.list_namespaced_event.side_effect = Exception("API error")

    rc = RunnerController(
        namespace="sisyphus-runners",
        runner_image="test-image",
        runner_sa="sa",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="secret",
        core_v1=mock_core_v1,
    )

    result = await rc._diagnose_pod("test-pod")
    assert result == "diagnostic failed"


# ─── test_migrate_0008 ──────────────────────────────────────────────────────

def test_migrate_0008_forward_creates_alerts_table():
    """migration 0008 forward SQL 含 CREATE TABLE alerts。"""
    from orchestrator.migrate import _DEFAULT_MIGRATIONS_DIR

    fwd = Path(_DEFAULT_MIGRATIONS_DIR) / "0008_create_alerts.sql"
    assert fwd.is_file(), "migration 0008 not found"
    body = fwd.read_text()
    assert "CREATE TABLE" in body.upper()
    assert "alerts" in body.lower()
    assert "severity" in body
    assert "reason" in body
    assert "sent_to_tg" in body


def test_migrate_0008_rollback_drops_alerts():
    """migration 0008 rollback SQL 含 DROP TABLE alerts。"""
    from orchestrator.migrate import _DEFAULT_MIGRATIONS_DIR

    rb = Path(_DEFAULT_MIGRATIONS_DIR) / "0008_create_alerts.rollback.sql"
    assert rb.is_file(), "migration 0008 rollback not found"
    body = rb.read_text()
    assert "DROP TABLE" in body.upper()
    assert "alerts" in body.lower()


# ─── test_invoke_verifier_after_fix_loop_detect ─────────────────────────────

@pytest.mark.asyncio
async def test_invoke_verifier_after_fix_loop_detect(monkeypatch):
    """verifier_history 已有 3 条 → 直接 emit VERIFY_ESCALATE，不起新 verifier。"""
    from orchestrator.actions._verifier import invoke_verifier_after_fix
    from orchestrator.state import Event

    # mock db
    ctx_patches: list = []
    async def fake_update_context(pool, req_id, patch):
        ctx_patches.append(patch)

    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update_context)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: object())

    invoke_calls: list = []
    async def fake_invoke_verifier(**kw):
        invoke_calls.append(kw)
        return {"verifier_issue_id": "v-1"}

    monkeypatch.setattr("orchestrator.actions._verifier.invoke_verifier", fake_invoke_verifier)

    alert_calls: list = []
    async def fake_alert_insert(**kw):
        alert_calls.append(kw)
        return 1
    monkeypatch.setattr("orchestrator.actions._verifier.alerts.insert", fake_alert_insert)

    @dataclass
    class _Body:
        projectId: str = "proj-1"

    result = await invoke_verifier_after_fix(
        body=_Body(),
        req_id="REQ-X",
        tags=["parent-stage:dev_cross_check"],
        ctx={
            "verifier_history": [
                {"fixer": "dev", "fixer_issue_id": "f-1"},
                {"fixer": "dev", "fixer_issue_id": "f-2"},
                {"fixer": "dev", "fixer_issue_id": "f-3"},
            ],
            "fixer_role": "dev",
            "fixer_issue_id": "f-4",
        },
    )

    # 4 rounds total (3 existing + 1 current) → loop detected
    assert result["emit"] == Event.VERIFY_ESCALATE.value
    assert result["reason"] == "fixer loop"
    # invoke_verifier NOT called
    assert invoke_calls == []
    # alert written
    assert len(alert_calls) == 1
    assert alert_calls[0]["reason"] == "fixer-loop-3rounds"
    # ctx patched with escalated_reason
    merged_patch = {}
    for p in ctx_patches:
        merged_patch.update(p)
    assert merged_patch["escalated_reason"] == "fixer-loop-3rounds"


@pytest.mark.asyncio
async def test_invoke_verifier_after_fix_no_loop(monkeypatch):
    """verifier_history 有 2 条（+本次=3）→ 正常起 verifier，不触发 loop detect。"""
    from orchestrator.actions._verifier import invoke_verifier_after_fix

    async def fake_update_context(pool, req_id, patch):
        pass

    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update_context)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: object())

    invoke_calls: list = []
    async def fake_invoke_verifier(**kw):
        invoke_calls.append(kw)
        return {"verifier_issue_id": "v-1", "stage": "dev_cross_check", "trigger": "success"}

    monkeypatch.setattr("orchestrator.actions._verifier.invoke_verifier", fake_invoke_verifier)
    monkeypatch.setattr("orchestrator.actions._verifier.alerts.insert", AsyncMock(return_value=1))

    @dataclass
    class _Body:
        projectId: str = "proj-1"

    result = await invoke_verifier_after_fix(
        body=_Body(),
        req_id="REQ-Y",
        tags=["parent-stage:staging_test"],
        ctx={
            "verifier_history": [
                {"fixer": "dev", "fixer_issue_id": "f-1"},
                {"fixer": "dev", "fixer_issue_id": "f-2"},
            ],
            "fixer_role": "dev",
            "fixer_issue_id": "f-3",
        },
    )

    # 3 rounds total → boundary, NOT loop (loop triggers at > 3)
    assert "emit" not in result or result.get("emit") != "verify.escalate"
    assert len(invoke_calls) == 1
