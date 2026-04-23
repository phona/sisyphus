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


# ─── fanout_specs ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fanout_specs_single_mode(monkeypatch):
    """M16：fanout_specs 退化成单 spec 模式（不再硬分 contract / acceptance）。

    并行 spec 任务的决策权交给 analyze-agent —— 它直接创多个 tag=spec issue，
    本 action 只起一个默认 spec agent。
    """
    from orchestrator.actions import fanout_specs as mod

    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="spec-1")
    patch_bkd(monkeypatch, "fanout_specs", fake)
    patch_db(monkeypatch, "fanout_specs")
    body = make_body(issue_id="anz-1")
    out = await mod.fanout_specs(body=body, req_id="REQ-9", tags=["analyze"], ctx={})
    assert out == {"spec_issue_id": "spec-1"}
    fake.create_issue.assert_awaited_once()
    _, kwargs = fake.create_issue.await_args
    assert "spec" in kwargs["tags"] and "REQ-9" in kwargs["tags"]
    assert "[REQ-9] [SPEC]" in kwargs["title"]
    # update-issue 调用：1 (analyze→done) + 1 (spec→working) = 2
    assert fake.update_issue.await_count == 2


def test_fanout_specs_no_manifest_validate_import():
    """M12：fanout_specs 不再跑 manifest_validate admission。M16：也不再 hardcode SPEC_STAGES。

    确认模块不 import checkers.manifest_validate / 不构造 ANALYZE_PENDING_HUMAN /
    不 hardcode 多个 spec stage（双 fanout 已砍）。
    """
    import inspect

    from orchestrator.actions import fanout_specs as mod
    src = inspect.getsource(mod)
    assert "manifest_validate" not in src
    assert "ANALYZE_PENDING_HUMAN" not in src
    assert "pending_human" not in src
    # M16：不再硬编码 contract-spec / acceptance-spec 两个 stage
    assert "contract-spec" not in src
    assert "acceptance-spec" not in src
    assert "SPEC_STAGES" not in src


# ─── mark_spec_reviewed_and_check ────────────────────────────────────────
@pytest.mark.asyncio
async def test_mark_spec_gate_open_single_mode(monkeypatch):
    """M16：单 spec issue ci-passed → emit spec.all-passed。"""
    from orchestrator.actions import mark_spec_reviewed_and_check as mod
    fake = make_fake_bkd()
    fake.list_issues.return_value = [
        FakeIssue(id="spec-1", tags=["spec", "REQ-9", "ci-passed"]),
    ]
    patch_bkd(monkeypatch, "mark_spec_reviewed_and_check", fake)
    patch_db(monkeypatch, "mark_spec_reviewed_and_check")
    body = make_body(issue_id="spec-1")
    out = await mod.mark_spec_reviewed_and_check(
        body=body, req_id="REQ-9", tags=["spec", "REQ-9"], ctx={},
    )
    assert out["emit"] == "spec.all-passed"
    assert out["passed_count"] == 1
    assert out["expected"] == 1
    fake.merge_tags_and_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_spec_gate_open_parallel(monkeypatch):
    """analyze-agent 自开多 spec 并行：2/2 ci-passed → emit spec.all-passed。"""
    from orchestrator.actions import mark_spec_reviewed_and_check as mod
    fake = make_fake_bkd()
    fake.list_issues.return_value = [
        FakeIssue(id="spec-1", tags=["spec", "REQ-9", "ci-passed"]),
        FakeIssue(id="spec-2", tags=["spec", "REQ-9", "ci-passed"]),
    ]
    patch_bkd(monkeypatch, "mark_spec_reviewed_and_check", fake)
    patch_db(monkeypatch, "mark_spec_reviewed_and_check")
    out = await mod.mark_spec_reviewed_and_check(
        body=make_body(issue_id="spec-2"), req_id="REQ-9",
        tags=["spec", "REQ-9"], ctx={},
    )
    assert out["emit"] == "spec.all-passed"
    assert out["passed_count"] == 2
    assert out["expected"] == 2


@pytest.mark.asyncio
async def test_mark_spec_gate_wait_parallel(monkeypatch):
    """2 个并行 spec，只有 1 个 ci-passed → 不 emit。"""
    from orchestrator.actions import mark_spec_reviewed_and_check as mod
    fake = make_fake_bkd()
    fake.list_issues.return_value = [
        FakeIssue(id="spec-1", tags=["spec", "REQ-9", "ci-passed"]),
        FakeIssue(id="spec-2", tags=["spec", "REQ-9"]),  # not yet
    ]
    patch_bkd(monkeypatch, "mark_spec_reviewed_and_check", fake)
    patch_db(monkeypatch, "mark_spec_reviewed_and_check")
    body = make_body(issue_id="spec-1")
    out = await mod.mark_spec_reviewed_and_check(
        body=body, req_id="REQ-9", tags=["spec"], ctx={},
    )
    assert "emit" not in out
    assert out["gate"] == "wait"
    assert out["passed_count"] == 1
    assert out["expected"] == 2


# ─── fanout_dev / create_accept ────────────────────────
@pytest.mark.asyncio
async def test_fanout_dev_single_mode(monkeypatch):
    """M15：fanout_dev 退化成纯单 dev 模式（manifest.parallelism 已删）。

    并行 dev 任务的决策权交给 analyze-agent —— 它直接创多个 tag=dev issue，
    本 action 只起一个。
    """
    from orchestrator.actions import fanout_dev as mod

    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="dev-1")
    patch_bkd(monkeypatch, "fanout_dev", fake)
    patch_db(monkeypatch, "fanout_dev")
    monkeypatch.setattr(mod.settings, "test_mode", False)
    monkeypatch.setattr(mod.settings, "skip_dev", False)

    out = await mod.fanout_dev(body=make_body(), req_id="REQ-9", tags=[], ctx={})
    assert out == {"dev_issue_id": "dev-1"}
    fake.create_issue.assert_awaited_once()
    _, kwargs = fake.create_issue.await_args
    assert "dev" in kwargs["tags"] and "REQ-9" in kwargs["tags"]
    assert "[REQ-9] [DEV]" in kwargs["title"]


def test_fanout_dev_no_manifest_import():
    """M15：fanout_dev 不该再 import manifest_io / 解析 parallelism 字段。"""
    import inspect

    from orchestrator.actions import fanout_dev as mod
    src = inspect.getsource(mod)
    # 不 import manifest_io
    assert "import manifest_io" not in src
    assert "from ..checkers" not in src
    # 不读 parallelism / scope / depends_on（dict key 形式访问）
    for token in ['"parallelism"', "'parallelism'", '"scope"', '"depends_on"']:
        assert token not in src, f"unexpected manifest token in src: {token}"


# ─── mark_dev_reviewed_and_check ────────────────────────
@pytest.mark.asyncio
async def test_mark_dev_gate_open_single_mode(monkeypatch):
    """expected=1 + 当前 issue 打 ci-passed → emit dev.all-passed。"""
    from orchestrator.actions import mark_dev_reviewed_and_check as mod

    fake = make_fake_bkd()
    fake.list_issues.return_value = [
        FakeIssue(id="dev-1", tags=["dev", "REQ-9", "ci-passed"]),
    ]
    patch_bkd(monkeypatch, "mark_dev_reviewed_and_check", fake)
    patch_db(monkeypatch, "mark_dev_reviewed_and_check")
    out = await mod.mark_dev_reviewed_and_check(
        body=make_body(issue_id="dev-1"), req_id="REQ-9",
        tags=["dev", "REQ-9"], ctx={"expected_dev_count": 1},
    )
    assert out["emit"] == "dev.all-passed"
    fake.merge_tags_and_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_dev_gate_wait_parallel(monkeypatch):
    """3 个并行 dev，只有 1 个 ci-passed → 不 emit。"""
    from orchestrator.actions import mark_dev_reviewed_and_check as mod

    fake = make_fake_bkd()
    fake.list_issues.return_value = [
        FakeIssue(id="d-a", tags=["dev", "REQ-9", "ci-passed", "dev-task:a"]),
        FakeIssue(id="d-b", tags=["dev", "REQ-9", "dev-task:b"]),
        FakeIssue(id="d-c", tags=["dev", "REQ-9", "dev-task:c"]),
    ]
    patch_bkd(monkeypatch, "mark_dev_reviewed_and_check", fake)
    patch_db(monkeypatch, "mark_dev_reviewed_and_check")
    out = await mod.mark_dev_reviewed_and_check(
        body=make_body(issue_id="d-a"), req_id="REQ-9",
        tags=["dev", "REQ-9"], ctx={"expected_dev_count": 3},
    )
    assert "emit" not in out
    assert out["gate"] == "wait"
    assert out["passed_count"] == 1
    assert out["expected"] == 3


@pytest.mark.asyncio
async def test_mark_dev_gate_all_passed_parallel(monkeypatch):
    """3/3 ci-passed → emit dev.all-passed。"""
    from orchestrator.actions import mark_dev_reviewed_and_check as mod

    fake = make_fake_bkd()
    fake.list_issues.return_value = [
        FakeIssue(id=f"d-{t}", tags=["dev", "REQ-9", "ci-passed", f"dev-task:{t}"])
        for t in "abc"
    ]
    patch_bkd(monkeypatch, "mark_dev_reviewed_and_check", fake)
    patch_db(monkeypatch, "mark_dev_reviewed_and_check")
    out = await mod.mark_dev_reviewed_and_check(
        body=make_body(issue_id="d-c"), req_id="REQ-9",
        tags=["dev", "REQ-9"], ctx={"expected_dev_count": 3},
    )
    assert out["emit"] == "dev.all-passed"
    assert out["passed_count"] == 3


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


# ─── escalate ────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_escalate(monkeypatch):
    from orchestrator.actions import escalate as mod
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, "escalate", fake)
    patch_db(monkeypatch, "escalate")
    body = make_body(issue_id="rvw-1", event="session.failed")
    out = await mod.escalate(body=body, req_id="REQ-9", tags=["reviewer"], ctx={"intent_issue_id": "intent-1"})
    assert out == {"escalated": True, "reason": "session-failed"}
    fake.merge_tags_and_update.assert_awaited_once()
    args, _ = fake.merge_tags_and_update.call_args
    assert "intent-1" in args  # 标在 intent issue 上


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
