"""actions 烟测：mock BKDClient + db pool 验单个 handler 调对了 BKD API。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest


@dataclass
class FakeIssue:
    id: str
    project_id: str = "p"
    issue_number: int = 0
    title: str = ""
    status_id: str = "todo"
    tags: list = None
    session_status: str | None = None
    description: str | None = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


def make_fake_bkd():
    bkd = AsyncMock()
    bkd.create_issue = AsyncMock(return_value=FakeIssue(id="new-1"))
    bkd.update_issue = AsyncMock(return_value=FakeIssue(id="new-1"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    bkd.list_issues = AsyncMock(return_value=[])
    bkd.get_issue = AsyncMock(return_value=FakeIssue(id="x", tags=["foo"]))
    bkd.merge_tags_and_update = AsyncMock(return_value=FakeIssue(id="x"))
    return bkd


def patch_bkd(monkeypatch, target_module: str, fake):
    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake
    monkeypatch.setattr(f"orchestrator.actions.{target_module}.BKDClient", _ctx)


def patch_db(monkeypatch, target_module: str):
    """让 db.get_pool 返回一个 dummy pool，update_context 静默吞。"""
    pool_writes: list = []

    class P:
        async def execute(self, sql, *args):
            pool_writes.append((sql.strip()[:40], args))

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(f"orchestrator.actions.{target_module}.db.get_pool", lambda: P())
    return pool_writes


def make_body(issue_id="src-1", project_id="p", event="session.completed", title="T"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": event, "title": title, "tags": [], "issueNumber": None,
    })()


# ─── start_analyze ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_start_analyze(monkeypatch):
    from orchestrator.actions import start_analyze as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "start_analyze", fake)
    body = make_body(issue_id="intent-1", title="加个登录")
    out = await mod.start_analyze(body=body, req_id="REQ-9", tags=["intent:analyze"], ctx={})
    assert out == {"issue_id": "intent-1", "req_id": "REQ-9"}
    # 改 title + tags + 发 prompt + 推 working
    assert fake.update_issue.await_count == 2  # title/tags + working
    assert fake.follow_up_issue.await_count == 1


@pytest.mark.asyncio
async def test_start_analyze_title_format(monkeypatch):
    """验证 start_analyze 标题使用 short_title 格式（ — 分隔 + 截断）。"""
    from orchestrator.actions import start_analyze as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "start_analyze", fake)

    # 场景1：有 intent_title，长度正常
    body = make_body(issue_id="intent-1", title="加个登录端点")
    ctx = {"intent_title": "加个登录端点"}
    await mod.start_analyze(body=body, req_id="REQ-9", tags=["intent:analyze"], ctx=ctx)
    _, kwargs = fake.update_issue.call_args_list[0]
    assert kwargs["title"] == "[REQ-9] [ANALYZE] — 加个登录端点"

    # 场景2：intent_title 超过 50 字符，需要截断 + 省略号
    long_title = "a" * 60
    ctx = {"intent_title": long_title}
    await mod.start_analyze(body=body, req_id="REQ-10", tags=["intent:analyze"], ctx=ctx)
    _, kwargs = fake.update_issue.call_args_list[2]
    title = kwargs["title"]
    assert title.startswith("[REQ-10] [ANALYZE] — ")
    assert "…" in title
    assert len(title) < 100  # 合理长度

    # 场景3：ctx 为空，标题应该只有 [REQ-xx] [ANALYZE] 部分
    ctx = {}
    await mod.start_analyze(body=body, req_id="REQ-11", tags=["intent:analyze"], ctx=ctx)
    _, kwargs = fake.update_issue.call_args_list[4]
    assert kwargs["title"] == "[REQ-11] [ANALYZE]"


def test_short_title_helper():
    from orchestrator.actions import short_title
    assert short_title(None) == ""
    assert short_title({}) == ""
    assert short_title({"intent_title": ""}) == ""
    assert short_title({"intent_title": "  hi  "}) == " — hi"
    long = "x" * 60
    out = short_title({"intent_title": long}, max_len=20)
    assert out.startswith(" — ")
    assert "…" in out
    assert len(out) < 30


def test_short_title_strips_leading_brackets():
    """[REQ-x] [E2E FOO] /buildinfo → /buildinfo（剥前缀避免 verifier title 双倍 [REQ-x]）"""
    from orchestrator.actions import short_title
    # 单层
    assert short_title({"intent_title": "[REQ-x] hello"}) == " — hello"
    # 双层 (REQ + E2E label)
    assert short_title(
        {"intent_title": "[REQ-final15-1776989948] [E2E FINAL15] /buildinfo (post-#45)"}
    ) == " — /buildinfo (post-#45)"
    # 不闭合的 [ —— 无法解析，原样保留（不破坏数据）
    assert short_title({"intent_title": "[unclosed bracket"}) == " — [unclosed bracket"
    # 全是括号无内容
    assert short_title({"intent_title": "[A] [B] [C]"}) == ""
    # 嵌套不剥（只剥前缀）
    assert short_title({"intent_title": "code [in middle]"}) == " — code [in middle]"


# ─── escalate ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_escalate_auto_resume_first_attempt(monkeypatch):
    """transient session.failed + retry_count=0 → auto-resume (follow-up "continue")"""
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    body = make_body(issue_id="rvw-1", event="session.failed")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["reviewer"],
        ctx={"intent_issue_id": "intent-1"},  # auto_retry_count default 0
    )
    assert out["auto_resumed"] is True
    assert out["retry"] == 1
    assert out["reason"] == "session-failed"
    fake.follow_up_issue.assert_awaited_once()
    fake.merge_tags_and_update.assert_not_awaited()  # 没真 escalate


@pytest.mark.asyncio
async def test_escalate_real_after_retries_exhausted(monkeypatch):
    """transient session.failed + retry_count=2 → 真 escalate (final reason: session-failed-after-2-retries)"""
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    # mock req_state.get + cas_transition + k8s_runner cleanup
    from unittest.mock import AsyncMock

    from orchestrator import k8s_runner as krunner
    from orchestrator.store import req_state as rs

    class FakeRow:
        state = type("S", (), {"value": "executing"})()  # any non-ESCALATED
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=FakeRow()))
    monkeypatch.setattr(rs, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(krunner, "get_controller", lambda: type("C", (), {"cleanup_runner": AsyncMock()})())

    body = make_body(issue_id="rvw-1", event="session.failed")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["reviewer"],
        ctx={"intent_issue_id": "intent-1", "auto_retry_count": 2},
    )
    assert out["escalated"] is True
    assert out["reason"] == "session-failed-after-2-retries"
    fake.merge_tags_and_update.assert_awaited_once()
    fake.follow_up_issue.assert_not_awaited()  # 没 retry，直接真 escalate


@pytest.mark.asyncio
async def test_escalate_non_transient_immediate(monkeypatch):
    """verifier-decision-escalate (非 session.failed) → 直接真 escalate，不 retry"""
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    body = make_body(issue_id="rvw-1", event="verify.escalate")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "verifier-decision-escalate",
        },
    )
    assert out["escalated"] is True
    assert out["reason"] == "verifier-decision-escalate"
    fake.follow_up_issue.assert_not_awaited()
    fake.merge_tags_and_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_escalate_canonical_signal_overrides_stale_ctx(monkeypatch):
    """body.event=watchdog.stuck 应覆盖前一轮残留的 ctx.escalated_reason，
    避免 ctx 毒化（实证 sis #27：第一次 escalate 写了垃圾 reason，第二次
    watchdog 读 ctx 仍拿垃圾 → 不 transient → 永远不 auto-resume）。
    """
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    body = make_body(issue_id="src-1", event="watchdog.stuck")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["watchdog:intaking"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "issue-updated",  # ← 上一轮写的垃圾值
        },
    )
    # body.event canonical 优先 → reason=watchdog-stuck → transient → auto-resume
    assert out["auto_resumed"] is True
    assert out["reason"] == "watchdog-stuck"
    fake.follow_up_issue.assert_awaited_once()
    fake.merge_tags_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_escalate_intake_no_result_tag_directly_escalates(monkeypatch):
    """body.event=watchdog.intake_no_result_tag + ctx.escalated_reason='intake-no-result-tag'
    → 非 canonical → 用 ctx reason；非 transient → 直接真 escalate（不 retry）；
    body.event 在 _SESSION_END_SIGNALS → 走 cleanup CAS 推 ESCALATED + 清 runner。"""
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")

    from unittest.mock import AsyncMock

    from orchestrator import k8s_runner as krunner
    from orchestrator.store import req_state as rs

    class FakeRow:
        state = type("S", (), {"value": "intaking"})()
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=FakeRow()))
    cas = AsyncMock(return_value=True)
    monkeypatch.setattr(rs, "cas_transition", cas)
    cleanup = AsyncMock()
    monkeypatch.setattr(
        krunner, "get_controller",
        lambda: type("C", (), {"cleanup_runner": cleanup})(),
    )

    body = make_body(issue_id="intent-1", event="watchdog.intake_no_result_tag")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["watchdog:intake-no-result-tag"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "intake-no-result-tag",
        },
    )
    assert out["escalated"] is True
    # ctx.escalated_reason 优先（非 canonical）→ reason 透传
    assert out["reason"] == "intake-no-result-tag"
    # 不 retry
    fake.follow_up_issue.assert_not_awaited()
    # 真 escalate：在 intent issue 上加 escalated + reason 标签
    fake.merge_tags_and_update.assert_awaited_once()
    args, kwargs = fake.merge_tags_and_update.call_args
    assert "escalated" in kwargs.get("add", []) or "escalated" in (args[2] if len(args) > 2 else [])
    # CAS 推 ESCALATED + cleanup 跑（_SESSION_END_SIGNALS 包含本 event）
    cas.assert_awaited_once()
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_escalate_action_error_is_transient(monkeypatch):
    """engine _emit_escalate 注的 ctx.escalated_reason='action-error:...' 应被识别为
    transient（基础设施 flaky）→ auto-resume 一次，给环境恢复机会。
    """
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    body = make_body(issue_id="src-1", event="issue.updated")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["intent:intake"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "action-error:RuntimeError: pod not ready in 120s after 3 attempts",
        },
    )
    assert out["auto_resumed"] is True
    assert "action-error" in out["reason"]
    fake.follow_up_issue.assert_awaited_once()


# ─── done_archive ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_done_archive(monkeypatch):
    from orchestrator.actions import done_archive as mod
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="done-1")
    patch_bkd(monkeypatch, "done_archive", fake)
    patch_db(monkeypatch, "done_archive")
    out = await mod.done_archive(
        body=make_body(issue_id="acc-1"), req_id="REQ-9", tags=["accept", "result:pass"],
        ctx={"accept_issue_id": "acc-1"},
    )
    assert out == {"archive_issue_id": "done-1"}


@pytest.mark.asyncio
async def test_create_accept(monkeypatch):
    """v0.2：create_accept 先跑 env-up（k8s_runner.exec_in_runner）拿 endpoint，
    再 dispatch BKD accept-agent。
    """
    from orchestrator.actions import create_accept as mod
    from orchestrator.k8s_runner import ExecResult

    fake_bkd = make_fake_bkd()
    fake_bkd.create_issue.return_value = FakeIssue(id="acc-1")
    patch_bkd(monkeypatch, "create_accept", fake_bkd)
    patch_db(monkeypatch, "create_accept")
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)

    # mock k8s runner controller：env-up 返 exit_code=0 + stdout 末行 JSON
    class FakeRC:
        async def exec_in_runner(self, req_id, command, env=None, timeout_sec=600):
            return ExecResult(
                exit_code=0,
                stdout='some helm output\n{"endpoint":"http://svc.accept-req-9.svc:8080"}\n',
                stderr="",
                duration_sec=5.0,
            )

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: FakeRC(),
    )

    out = await mod.create_accept(
        body=make_body(issue_id="pr-ci-1"), req_id="REQ-9",
        tags=["pr-ci"], ctx={},
    )
    assert out["accept_issue_id"] == "acc-1"
    assert out["endpoint"] == "http://svc.accept-req-9.svc:8080"
    assert out["namespace"] == "accept-req-9"


@pytest.mark.asyncio
async def test_create_accept_env_up_fail(monkeypatch):
    """env-up exit_code != 0 → emit accept-env-up.fail，不 dispatch agent。"""
    from orchestrator.actions import create_accept as mod
    from orchestrator.k8s_runner import ExecResult

    fake_bkd = make_fake_bkd()
    patch_bkd(monkeypatch, "create_accept", fake_bkd)
    patch_db(monkeypatch, "create_accept")
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)

    class FakeRC:
        async def exec_in_runner(self, req_id, command, env=None, timeout_sec=600):
            return ExecResult(
                exit_code=1, stdout="", stderr="helm install failed",
                duration_sec=3.0,
            )

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: FakeRC(),
    )

    out = await mod.create_accept(
        body=make_body(issue_id="x"), req_id="REQ-9", tags=["pr-ci"], ctx={},
    )
    assert out["emit"] == "accept-env-up.fail"
    assert out["exit_code"] == 1
    # 不应 dispatch agent
    fake_bkd.create_issue.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_accept_skipped(monkeypatch):
    """skip_accept=True 直接 emit accept.pass，不调 BKD"""
    from orchestrator.actions import create_accept as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "create_accept", fake)
    patch_db(monkeypatch, "create_accept")
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", True)
    out = await mod.create_accept(
        body=make_body(issue_id="ci-int-1"), req_id="REQ-9", tags=["ci"], ctx={},
    )
    assert out["skipped"] is True
    assert out["emit"] == "accept.pass"
    fake.create_issue.assert_not_called()


# ─── create_staging_test ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_staging_test_checker_pass(monkeypatch):
    """checker_staging_test_enabled=True + checker pass → emit staging-test.pass。"""
    from orchestrator.actions import create_staging_test as mod
    from orchestrator.checkers._types import CheckResult

    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.checker_staging_test_enabled", True)
    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.skip_staging_test", False)
    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.test_mode", False)

    fake_result = CheckResult(passed=True, exit_code=0, stdout_tail="ok\n", stderr_tail="", duration_sec=4.2, cmd="cd /workspace/source/foo && make ci-unit-test")

    async def fake_run(req_id):
        return fake_result

    monkeypatch.setattr("orchestrator.actions.create_staging_test.checker.run_staging_test", fake_run)

    insert_calls: list = []

    async def fake_insert(pool, req_id, stage, result):
        insert_calls.append((req_id, stage, result))

    monkeypatch.setattr("orchestrator.actions.create_staging_test.artifact_checks.insert_check", fake_insert)
    patch_db(monkeypatch, "create_staging_test")

    out = await mod.create_staging_test(body=make_body(), req_id="REQ-9", tags=[], ctx={})

    assert out["emit"] == "staging-test.pass"
    assert out["passed"] is True
    assert out["exit_code"] == 0
    assert len(insert_calls) == 1
    assert insert_calls[0] == ("REQ-9", "staging-test", fake_result)


@pytest.mark.asyncio
async def test_create_staging_test_checker_fail(monkeypatch):
    """checker_staging_test_enabled=True + checker fail → emit staging-test.fail。"""
    from orchestrator.actions import create_staging_test as mod
    from orchestrator.checkers._types import CheckResult

    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.checker_staging_test_enabled", True)
    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.skip_staging_test", False)
    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.test_mode", False)

    fake_result = CheckResult(passed=False, exit_code=1, stdout_tail="FAIL\n", stderr_tail="panic\n", duration_sec=2.0, cmd="cd /workspace/source/foo && make ci-unit-test")

    async def fake_run(req_id):
        return fake_result

    monkeypatch.setattr("orchestrator.actions.create_staging_test.checker.run_staging_test", fake_run)

    async def fake_insert(pool, req_id, stage, result):
        pass

    monkeypatch.setattr("orchestrator.actions.create_staging_test.artifact_checks.insert_check", fake_insert)
    patch_db(monkeypatch, "create_staging_test")

    out = await mod.create_staging_test(body=make_body(), req_id="REQ-9", tags=[], ctx={})

    assert out["emit"] == "staging-test.fail"
    assert out["passed"] is False
    assert out["exit_code"] == 1


@pytest.mark.asyncio
async def test_create_staging_test_checker_timeout(monkeypatch):
    """M14c：checker timeout → emit staging-test.fail（让 verifier 判）。"""
    from orchestrator.actions import create_staging_test as mod

    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.checker_staging_test_enabled", True)
    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.skip_staging_test", False)
    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.test_mode", False)

    async def fake_run_check(req_id):
        raise TimeoutError()

    monkeypatch.setattr("orchestrator.actions.create_staging_test.checker.run_staging_test", fake_run_check)
    patch_db(monkeypatch, "create_staging_test")

    out = await mod.create_staging_test(
        body=make_body(), req_id="REQ-9", tags=[], ctx={},
    )

    assert out["emit"] == "staging-test.fail"
    assert out["passed"] is False
    assert out["reason"] == "timeout"


@pytest.mark.asyncio
async def test_create_staging_test_bkd_path(monkeypatch):
    """checker_staging_test_enabled=False → 走老路创建 BKD agent issue。"""
    from orchestrator.actions import create_staging_test as mod

    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.checker_staging_test_enabled", False)
    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.skip_staging_test", False)
    monkeypatch.setattr("orchestrator.actions.create_staging_test.settings.test_mode", False)

    fake_bkd = make_fake_bkd()
    fake_bkd.create_issue.return_value = FakeIssue(id="st-1")
    patch_bkd(monkeypatch, "create_staging_test", fake_bkd)
    patch_db(monkeypatch, "create_staging_test")

    out = await mod.create_staging_test(body=make_body(issue_id="dev-1"), req_id="REQ-9", tags=[], ctx={})

    assert out == {"staging_test_issue_id": "st-1"}
    fake_bkd.create_issue.assert_awaited_once()
    fake_bkd.follow_up_issue.assert_awaited_once()
    fake_bkd.update_issue.assert_awaited_once()


# ─── teardown_accept_env skip 路径 ────────────────────────────────────────
@pytest.mark.asyncio
async def test_teardown_skipped_when_accept_skipped(monkeypatch):
    """skip_accept=True 时 teardown 必 skip，emit teardown-done.pass（不能误推 fail）。"""
    from orchestrator.actions import teardown_accept_env as mod
    from orchestrator.config import settings

    monkeypatch.setattr(settings, "skip_accept", True)
    monkeypatch.setattr(settings, "test_mode", False)

    out = await mod.teardown_accept_env(body=make_body(), req_id="REQ-9", tags=[], ctx={})

    assert out.get("skipped") is True
    assert out["emit"] == "teardown-done.pass"


@pytest.mark.asyncio
async def test_teardown_runs_normally_when_accept_not_skipped(monkeypatch):
    """skip_accept=False + tags 含 result:pass → 正常跑 env-down + emit teardown-done.pass。"""
    from orchestrator.actions import teardown_accept_env as mod
    from orchestrator.config import settings

    monkeypatch.setattr(settings, "skip_accept", False)
    monkeypatch.setattr(settings, "test_mode", False)

    # k8s_runner.get_controller 抛 → teardown 走 best-effort 路径（test 跳真 exec）
    def fake_controller():
        raise RuntimeError("no k8s in test")
    monkeypatch.setattr(mod.k8s_runner, "get_controller", fake_controller)
    patch_db(monkeypatch, "teardown_accept_env")

    out = await mod.teardown_accept_env(
        body=make_body(), req_id="REQ-9", tags=["accept", "REQ-9", "result:pass"], ctx={},
    )

    assert out.get("skipped") is None  # 没走 skip 路径
    assert out["emit"] == "teardown-done.pass"
    assert out["accept_result"] == "pass"
    assert out["env_down_ok"] is False  # controller 抛了，best-effort 失败但不阻塞


# ─── 重要：BKD merge_tags_and_update 的 tag merge 逻辑 ─────────────────────
@pytest.mark.asyncio
async def test_bkd_merge_tags_preserves(monkeypatch):
    # 直接拿具体实现 class — merge_tags_and_update 在 REST/MCP 两侧逻辑一致，任挑一个测
    from orchestrator.bkd_rest import BKDRestClient

    captured = {}

    async def fake_get_issue(self, project_id, issue_id):
        return FakeIssue(id=issue_id, tags=["existing", "REQ-9"])

    async def fake_update_issue(self, project_id, issue_id, **kw):
        captured.update(kw)
        return FakeIssue(id=issue_id, tags=kw.get("tags", []))

    monkeypatch.setattr(BKDRestClient, "get_issue", fake_get_issue)
    monkeypatch.setattr(BKDRestClient, "update_issue", fake_update_issue)
    bkd = BKDRestClient.__new__(BKDRestClient)
    await bkd.merge_tags_and_update(
        "p", "i1", add=["ci-passed"], remove=["existing"], status_id="done",
    )
    assert set(captured["tags"]) == {"REQ-9", "ci-passed"}
    assert captured["status_id"] == "done"
