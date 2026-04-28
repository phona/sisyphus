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
    patch_db(monkeypatch, "start_analyze")  # admission gate reads pool
    body = make_body(issue_id="intent-1", title="加个登录")
    out = await mod.start_analyze(body=body, req_id="REQ-9", tags=["intent:analyze"], ctx={})
    # cloned_repos=None: 直接 analyze 路径无 involved_repos，跳过 server-side clone
    # （REQ-clone-and-pr-ci-fallback-1777115925）
    assert out == {"issue_id": "intent-1", "req_id": "REQ-9", "cloned_repos": None}
    # 改 title + tags + 发 prompt + 推 working
    assert fake.update_issue.await_count == 2  # title/tags + working
    assert fake.follow_up_issue.await_count == 1


@pytest.mark.asyncio
async def test_start_analyze_title_format(monkeypatch):
    """验证 start_analyze 标题使用 short_title 格式（ — 分隔 + 截断）。"""
    from orchestrator.actions import start_analyze as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "start_analyze", fake)
    patch_db(monkeypatch, "start_analyze")  # admission gate reads pool

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
    """verifier-decision (非 session.failed) → 直接真 escalate，不 retry"""
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
async def test_escalate_archive_failed_from_watchdog(monkeypatch):
    """REQ-archive-failure-watchdog: watchdog 贴 body.event='archive.failed' →
    reason='archive-failed'（不是 generic 'watchdog-stuck'），auto-resume 一次。"""
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    body = make_body(issue_id="arch-1", event="archive.failed")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["done-archive"],
        ctx={"intent_issue_id": "intent-1", "archive_issue_id": "arch-1"},
    )
    # archive.failed 是 canonical → reason 直接 slug 化得 archive-failed
    assert out["auto_resumed"] is True
    assert out["reason"] == "archive-failed"
    fake.follow_up_issue.assert_awaited_once()
    fake.merge_tags_and_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_escalate_archive_failed_from_bkd_session_failed_webhook(monkeypatch):
    """REQ-archive-failure-watchdog: BKD 真发的 session.failed webhook + body.issueId
    匹配 ctx.archive_issue_id → reason 也覆盖为 'archive-failed'（不是默认 'session-failed'），
    让 dashboard M7 能统一统计 archive 阶段失败。"""
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    body = make_body(issue_id="arch-2", event="session.failed")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["done-archive"],
        ctx={"intent_issue_id": "intent-1", "archive_issue_id": "arch-2"},
    )
    assert out["auto_resumed"] is True
    assert out["reason"] == "archive-failed"


@pytest.mark.asyncio
async def test_escalate_session_failed_unrelated_issue_unchanged(monkeypatch):
    """body.issueId 不是 archive_issue_id（比如 dev / accept 阶段崩溃）→ reason 不变 'session-failed'。
    确保 archive override 只在 issue 真匹配 archive_issue_id 时生效。"""
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    body = make_body(issue_id="dev-1", event="session.failed")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["dev"],
        ctx={"intent_issue_id": "intent-1", "archive_issue_id": "arch-other"},
    )
    assert out["reason"] == "session-failed"


@pytest.mark.asyncio
async def test_escalate_archive_failed_real_after_retries_exhausted(monkeypatch):
    """archive.failed + retry_count=2 → 真 escalate，final_reason='archive-failed-after-2-retries'
    （区别于通用 'session-failed-after-2-retries'）。"""
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    from unittest.mock import AsyncMock

    from orchestrator import k8s_runner as krunner
    from orchestrator.store import req_state as rs

    class FakeRow:
        state = type("S", (), {"value": "archiving"})()
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=FakeRow()))
    monkeypatch.setattr(rs, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(krunner, "get_controller", lambda: type("C", (), {"cleanup_runner": AsyncMock()})())

    body = make_body(issue_id="arch-1", event="archive.failed")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["done-archive"],
        ctx={
            "intent_issue_id": "intent-1",
            "archive_issue_id": "arch-1",
            "auto_retry_count": 2,
        },
    )
    assert out["escalated"] is True
    assert out["reason"] == "archive-failed-after-2-retries"
    fake.merge_tags_and_update.assert_awaited_once()
    fake.follow_up_issue.assert_not_awaited()
    # 验证 tag merge 用的是 intent_issue_id（不是 archive issue），且 reason tag 正确
    _, kwargs = fake.merge_tags_and_update.call_args
    assert "escalated" in kwargs["add"]
    assert "reason:archive-failed-after-2-retries" in kwargs["add"]


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


@pytest.mark.asyncio
async def test_escalate_fixer_round_cap_is_hard_reason(monkeypatch):
    """ctx.escalated_reason='fixer-round-cap' 即使 body.event 是 canonical 信号
    （watchdog.stuck / session.failed）也不能被覆盖、不能 auto-resume。

    保护场景：watchdog 检到孤儿 FIXER_RUNNING（start_fixer 写完 ctx 但 emit 失败），
    若 escalate 把 reason 重写成 watchdog-stuck → 误判 transient → auto-resume →
    BKD 续上去 → fixer 继续跑 → 死循环回归。
    """
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    from unittest.mock import AsyncMock

    from orchestrator import k8s_runner as krunner
    from orchestrator.store import req_state as rs

    class FakeRow:
        state = type("S", (), {"value": "fixer-running"})()
    monkeypatch.setattr(rs, "get", AsyncMock(return_value=FakeRow()))
    monkeypatch.setattr(rs, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(krunner, "get_controller", lambda: type("C", (), {"cleanup_runner": AsyncMock()})())

    body = make_body(issue_id="src-1", event="watchdog.stuck")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["watchdog:fixer-running"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "fixer-round-cap",
            "fixer_round": 5,
        },
    )
    # ctx hard reason 压过 canonical → reason 保留 fixer-round-cap，不 auto-resume
    assert out["escalated"] is True
    assert out["reason"] == "fixer-round-cap"
    fake.follow_up_issue.assert_not_awaited()
    fake.merge_tags_and_update.assert_awaited_once()
    # 加的 tag 含 reason:fixer-round-cap
    _, mtu_kwargs = fake.merge_tags_and_update.call_args
    add_tags = mtu_kwargs.get("add") or []
    assert "escalated" in add_tags
    assert "reason:fixer-round-cap" in add_tags


@pytest.mark.asyncio
async def test_escalate_fixer_round_cap_session_completed_path(monkeypatch):
    """start_fixer 主链路径：body.event=session.completed（verifier 完成事件触发）。
    ctx.escalated_reason=fixer-round-cap → 真 escalate（非 transient），且
    is_session_failed_path=False，依赖 engine 已 CAS 推 ESCALATED（这里只验 action 行为）。
    """
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")

    body = make_body(issue_id="vfy-1", event="session.completed")
    out = await mod.escalate(
        body=body, req_id="REQ-9", tags=["verifier"],
        ctx={
            "intent_issue_id": "intent-1",
            "escalated_reason": "fixer-round-cap",
        },
    )
    assert out["escalated"] is True
    assert out["reason"] == "fixer-round-cap"
    fake.follow_up_issue.assert_not_awaited()
    fake.merge_tags_and_update.assert_awaited_once()


def test_is_transient_treats_fixer_round_cap_as_hard():
    """单测：_is_transient 对 fixer-round-cap 永远返 False，不论 body.event。"""
    from orchestrator.actions.escalate import _is_transient
    assert _is_transient("session.failed", "fixer-round-cap") is False
    assert _is_transient("watchdog.stuck", "fixer-round-cap") is False
    assert _is_transient(None, "fixer-round-cap") is False
    # sanity: 老的 transient 仍 transient
    assert _is_transient("session.failed", "session-failed") is True


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
    """v0.3-lite: per-repo shell script exits 0 → emit accept.pass (no BKD agent)."""
    from orchestrator.actions import create_accept as mod
    from orchestrator.k8s_runner import ExecResult

    patch_db(monkeypatch, "create_accept")
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.accept_smoke_delay_sec", 0)

    class FakeRC:
        async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
            return ExecResult(exit_code=0, stdout="PASS\n", stderr="", duration_sec=1.0)

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: FakeRC(),
    )

    ctx_updates: list[dict] = []
    async def fake_update_ctx(p, req_id, updates):
        ctx_updates.append(updates)
    monkeypatch.setattr("orchestrator.actions.create_accept.req_state.update_context", fake_update_ctx)

    out = await mod.create_accept(
        body=make_body(issue_id="pr-ci-1"), req_id="REQ-9",
        tags=["pr-ci"], ctx={"cloned_repos": ["phona/sisyphus"]},
    )
    assert out["emit"] == "accept.pass"
    assert any(u.get("accept_result") == "pass" for u in ctx_updates)


@pytest.mark.asyncio
async def test_create_accept_env_up_fail(monkeypatch):
    """Shell script exits 1, stdout ends FAIL:repo → emit accept.fail."""
    from orchestrator.actions import create_accept as mod
    from orchestrator.k8s_runner import ExecResult

    patch_db(monkeypatch, "create_accept")
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.accept_smoke_delay_sec", 0)

    class FakeRC:
        async def exec_in_runner(self, req_id, command, env=None, timeout_sec=None):
            return ExecResult(
                exit_code=1, stdout="FAIL:repo-a\n",
                stderr="make: Error 1", duration_sec=3.0,
            )

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: FakeRC(),
    )

    ctx_updates: list[dict] = []
    async def fake_update_ctx(p, req_id, updates):
        ctx_updates.append(updates)
    monkeypatch.setattr("orchestrator.actions.create_accept.req_state.update_context", fake_update_ctx)

    out = await mod.create_accept(
        body=make_body(issue_id="x"), req_id="REQ-9",
        tags=["pr-ci"], ctx={"cloned_repos": ["org/repo-a"]},
    )
    assert out["emit"] == "accept.fail"
    assert out["exit_code"] == 1
    assert "repo-a" in out.get("fail_repos", [])
    assert any(u.get("accept_result") == "fail" for u in ctx_updates)


@pytest.mark.asyncio
async def test_create_accept_skipped(monkeypatch):
    """skip_accept=True → emit accept.pass, exec_in_runner never called."""
    from orchestrator.actions import create_accept as mod

    patch_db(monkeypatch, "create_accept")
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.skip_accept", True)
    monkeypatch.setattr("orchestrator.actions.create_accept.settings.test_mode", False)

    class FakeRC:
        async def exec_in_runner(self, *a, **kw):
            raise AssertionError("exec_in_runner must NOT be called when skipped")

    monkeypatch.setattr(
        "orchestrator.actions.create_accept.k8s_runner.get_controller",
        lambda: FakeRC(),
    )

    ctx_updates: list[dict] = []
    async def fake_update_ctx(p, req_id, updates):
        ctx_updates.append(updates)
    monkeypatch.setattr("orchestrator.actions.create_accept.req_state.update_context", fake_update_ctx)

    out = await mod.create_accept(
        body=make_body(issue_id="ci-int-1"), req_id="REQ-9", tags=["ci"], ctx={},
    )
    assert out["skipped"] is True
    assert out["emit"] == "accept.pass"


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
