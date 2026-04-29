"""M14b：verifier-agent 框架 单测。

覆盖：
1. decision schema 校验 + Event 映射（router.validate_decision / decision_to_event）
2. decision 提取（tag base64 / description ```json```）
3. derive_verifier_event 整合：合规 / 非法 / 缺失 → 正确事件
4. invoke_verifier：prompt 渲染 + BKD issue 创建 + ctx 落字段
5. action handler：apply_verify_pass / start_fixer / invoke_verifier_after_fix 的行为
6. 12 个 stage_trigger prompt 模板都能渲染出来
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest

from orchestrator.router import (
    decision_to_event,
    derive_verifier_event,
    derive_verifier_event_with_retry_info,
    extract_decision_from_issue,
    validate_decision,
)
from orchestrator.state import Event

# ─── 1. validate_decision ────────────────────────────────────────────────

@pytest.mark.parametrize("decision,ok", [
    ({"action": "pass", "fixer": None, "scope": None, "reason": "ok", "confidence": "high"}, True),
    ({"action": "fix", "fixer": "dev", "scope": "src/", "reason": "bug", "confidence": "high"}, True),
    ({"action": "fix", "fixer": "spec", "scope": "openspec/changes/REQ-1/", "reason": "x", "confidence": "low"}, True),
    # M15: fixer=manifest 已删（manifest 抽象层砍掉），保留作 invalid case 验证
    ({"action": "fix", "fixer": "manifest", "scope": "manifest.pr.number", "reason": "y", "confidence": "high"}, False),
    # retry_checker 已砍 → 当 invalid action（unknown enum）
    ({"action": "retry_checker", "fixer": None, "scope": None, "reason": "flaky", "confidence": "low"}, False),
    ({"action": "escalate", "fixer": None, "scope": None, "reason": "need human", "confidence": "high"}, True),
    # REQ-428: retry action — VFR-S5: valid + invalid cases
    ({"action": "retry", "fixer": None, "scope": None, "reason": "infra flaky: kubectl", "confidence": "high"}, True),
    ({"action": "retry", "fixer": "dev", "scope": None, "reason": "x", "confidence": "high"}, False),  # fixer must be null
    # invalid action
    ({"action": "nope", "fixer": None, "scope": None, "reason": "", "confidence": "high"}, False),
    # missing fixer for fix
    ({"action": "fix", "fixer": None, "scope": "src/", "reason": "", "confidence": "high"}, False),
    # fixer set when not fix
    ({"action": "pass", "fixer": "dev", "scope": "src/", "reason": "", "confidence": "high"}, False),
    # invalid fixer
    ({"action": "fix", "fixer": "wizard", "scope": "src/", "reason": "", "confidence": "high"}, False),
    # invalid confidence
    ({"action": "pass", "fixer": None, "scope": None, "reason": "", "confidence": "medium"}, False),
    # not a dict
    ("not a dict", False),
    (None, False),
])
def test_validate_decision(decision, ok):
    got_ok, _ = validate_decision(decision)
    assert got_ok == ok


def test_decision_to_event_mapping():
    # 无 stage → 回退 VERIFY_PASS（调用方应 escalate）
    assert decision_to_event({"action": "pass"}) == Event.VERIFY_PASS
    # 有 stage → 主链 pass 事件
    assert decision_to_event({"action": "pass"}, stage="staging_test") == Event.STAGING_TEST_PASS
    assert decision_to_event({"action": "pass"}, stage="pr_ci") == Event.PR_CI_PASS
    assert decision_to_event({"action": "fix"}) == Event.VERIFY_FIX_NEEDED
    assert decision_to_event({"action": "escalate"}) == Event.VERIFY_ESCALATE
    # REQ-428 VFR-S6: retry maps to VERIFY_INFRA_RETRY
    assert decision_to_event({"action": "retry"}) == Event.VERIFY_INFRA_RETRY


def _b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")


# ─── 2. extract_decision_from_issue ──────────────────────────────────────

def test_extract_from_tag_base64():
    d = {"action": "pass", "fixer": None, "scope": None, "reason": "ok", "confidence": "high"}
    b64 = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    tags = ["verifier", "REQ-1", f"decision:{b64}"]
    got = extract_decision_from_issue(None, tags)
    assert got == d


def test_extract_from_description_json_block():
    d = {"action": "fix", "fixer": "dev", "scope": "src/", "reason": "need fix", "confidence": "high"}
    desc = f"some text\n```json\n{json.dumps(d)}\n```\nfooter"
    got = extract_decision_from_issue(desc, [])
    assert got == d


def test_extract_prefers_last_json_block():
    d1 = {"action": "pass", "fixer": None}
    d2 = {"action": "escalate", "fixer": None}
    desc = f"```json\n{json.dumps(d1)}\n```\n```json\n{json.dumps(d2)}\n```"
    got = extract_decision_from_issue(desc, [])
    assert got == d2


def test_extract_tag_beats_description_when_valid():
    d_tag = {"action": "pass", "fixer": None}
    d_desc = {"action": "escalate", "fixer": None}
    b64 = base64.urlsafe_b64encode(json.dumps(d_tag).encode()).decode().rstrip("=")
    desc = f"```json\n{json.dumps(d_desc)}\n```"
    got = extract_decision_from_issue(desc, [f"decision:{b64}"])
    assert got == d_tag


def test_extract_bad_tag_falls_back_to_description():
    d_desc = {"action": "pass", "fixer": None}
    desc = f"```json\n{json.dumps(d_desc)}\n```"
    got = extract_decision_from_issue(desc, ["decision:!!!not-base64!!!"])
    assert got == d_desc


def test_extract_none_when_empty():
    assert extract_decision_from_issue(None, []) is None
    assert extract_decision_from_issue("", []) is None
    assert extract_decision_from_issue("no json here", []) is None


# ─── 3. derive_verifier_event 整合 ───────────────────────────────────────

def test_derive_verifier_event_pass():
    d = {"action": "pass", "fixer": None, "scope": None, "reason": "ok", "confidence": "high"}
    b64 = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    # verify:staging_test tag 驱动 router 译成 STAGING_TEST_PASS
    ev, decision, why = derive_verifier_event(None, [f"decision:{b64}", "verify:staging_test"])
    assert ev == Event.STAGING_TEST_PASS
    assert decision == d
    assert why == ""


def test_derive_verifier_event_fix():
    d = {"action": "fix", "fixer": "dev", "scope": "src/", "reason": "bug", "confidence": "high"}
    b64 = base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    ev, got_decision, _ = derive_verifier_event(None, [f"decision:{b64}"])
    assert ev == Event.VERIFY_FIX_NEEDED
    assert got_decision == d


def test_derive_verifier_event_legacy_retry_checker_now_escalates():
    """retry_checker 已砍 → router 当 invalid action → escalate（避免老 prompt 卡死）。"""
    d = {"action": "retry_checker", "fixer": None, "scope": None, "reason": "flaky", "confidence": "low"}
    desc = f"```json\n{json.dumps(d)}\n```"
    ev, _decision, why = derive_verifier_event(desc, [])
    assert ev == Event.VERIFY_ESCALATE
    assert "retry_checker" in why or "invalid" in why.lower()


def test_derive_verifier_event_invalid_decision_escalates():
    d = {"action": "nope"}  # invalid
    desc = f"```json\n{json.dumps(d)}\n```"
    ev, decision, why = derive_verifier_event(desc, [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision == d
    assert "invalid" in why.lower()


def test_derive_verifier_event_no_decision_escalates():
    ev, decision, why = derive_verifier_event("no json here", [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision is None
    assert "no decision" in why.lower()


# ─── 3.5 derive_verifier_event_with_retry_info ───────────────────────────

def test_derive_with_retry_info_valid_decision():
    d = {"action": "pass", "fixer": None, "scope": None, "reason": "ok", "confidence": "high"}
    ev, decision, why, retry = derive_verifier_event_with_retry_info(
        None, [f"decision:{_b64(d)}", "verify:staging_test"],
    )
    assert ev == Event.STAGING_TEST_PASS
    assert decision == d
    assert why == ""
    assert retry is False


def test_derive_pass_without_verify_tag_escalates():
    """无 verify:<stage> tag → router 译不出 stage → escalate（不 retry，不是解析问题）。"""
    d = {"action": "pass", "fixer": None, "scope": None, "reason": "ok", "confidence": "high"}
    ev, decision, why, retry = derive_verifier_event_with_retry_info(
        None, [f"decision:{_b64(d)}"],
    )
    assert ev == Event.VERIFY_ESCALATE
    assert decision == d
    assert "unknown verifier stage" in why
    assert retry is False


def test_derive_with_retry_info_schema_invalid_is_retry_worthy():
    d = {"action": "nope"}  # invalid action
    desc = f"```json\n{json.dumps(d)}\n```"
    ev, decision, why, retry = derive_verifier_event_with_retry_info(desc, [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision == d
    assert "invalid" in why.lower()
    assert retry is True


def test_derive_with_retry_info_no_decision_not_retry_worthy():
    ev, decision, _why, retry = derive_verifier_event_with_retry_info("no json here", [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision is None
    assert retry is False


def test_derive_with_retry_info_unparseable_but_retry_worthy():
    """找到了 decision-like 文本但解析失败 → retry_worthy=True。"""
    desc = "My decision is {action: pass, fixer: None} because..."
    ev, decision, _why, retry = derive_verifier_event_with_retry_info(desc, [])
    assert ev == Event.VERIFY_ESCALATE
    assert decision is None
    assert retry is True


# ─── 4. invoke_verifier ─────────────────────────────────────────────────

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
    bkd.create_issue = AsyncMock(return_value=FakeIssue(id="vfy-1"))
    bkd.update_issue = AsyncMock(return_value=FakeIssue(id="vfy-1"))
    bkd.follow_up_issue = AsyncMock(return_value={})
    return bkd


def patch_bkd(monkeypatch, fake):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake
    monkeypatch.setattr("orchestrator.actions._verifier.BKDClient", _ctx)


def patch_db(monkeypatch):
    writes: list = []

    class P:
        async def execute(self, sql, *args):
            writes.append((sql.strip()[:40], args))
        async def fetchrow(self, sql, *args):
            return None
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: P())
    return writes


@pytest.fixture(autouse=True)
def _mock_verifier_dispatch_slugs(monkeypatch):
    """REQ-427: prevent real DB hits from slug dedup added to _verifier.py."""
    from orchestrator.actions import _verifier as v
    monkeypatch.setattr(v.dispatch_slugs, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(v.dispatch_slugs, "put", AsyncMock())


@pytest.mark.asyncio
async def test_invoke_verifier_creates_issue_with_right_tags(monkeypatch):
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_db(monkeypatch)

    out = await v.invoke_verifier(
        stage="staging_test",
        trigger="fail",
        req_id="REQ-9",
        project_id="proj-1",
        stderr_tail="oops",
        ctx={"intent_title": "加登录"},
    )
    assert out["verifier_issue_id"] == "vfy-1"
    assert out["stage"] == "staging_test"
    assert out["trigger"] == "fail"

    _args, kwargs = fake.create_issue.await_args
    assert kwargs["project_id"] == "proj-1"
    assert "verifier" in kwargs["tags"]
    assert "REQ-9" in kwargs["tags"]
    assert "verify:staging_test" in kwargs["tags"]
    assert "trigger:fail" in kwargs["tags"]
    assert kwargs["use_worktree"] is True
    assert "[VERIFY staging_test] fail" in kwargs["title"]
    assert "加登录" in kwargs["title"]

    fake.follow_up_issue.assert_awaited_once()
    # update_issue 被调 1 次（→working）
    fake.update_issue.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_verifier_renders_template(monkeypatch):
    """验 prompt 渲染结果带 stderr_tail + stage hint。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)
    patch_db(monkeypatch)

    await v.invoke_verifier(
        stage="dev_cross_check", trigger="fail",
        req_id="REQ-7", project_id="p",
        stderr_tail="TypeError: whoopsie",
    )
    _, kwargs = fake.follow_up_issue.await_args
    prompt = kwargs["prompt"]
    assert "verifier-agent" in prompt
    assert "REQ-7" in prompt
    assert "TypeError: whoopsie" in prompt
    # decision schema 指示必须在
    assert '"action":' in prompt
    assert "pass" in prompt and "fix" in prompt and "escalate" in prompt
    # retry_checker 已砍：prompt 不应再提该选项
    assert "retry_checker" not in prompt


@pytest.mark.asyncio
async def test_invoke_verifier_injects_checker_context_on_fail(monkeypatch):
    """trigger=fail 时从 artifact_checks 读最新记录并注入 prompt。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)

    class FakePool:
        async def fetchrow(self, sql, *args):
            assert args[0] == "REQ-8"
            assert args[1] == "dev-cross-check"
            return {
                "exit_code": 2,
                "stdout_tail": "unit pass\nlint fail",
                "stderr_tail": "ERROR: type mismatch",
                "cmd": "make ci-lint",
                "duration_sec": 45.0,
                "attempts": 1,
                "flake_reason": None,
                "checked_at": None,
            }

        async def execute(self, sql, *args):
            pass

    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: FakePool())
    monkeypatch.setattr(
        "orchestrator.actions._verifier.req_state.update_context", AsyncMock()
    )

    await v.invoke_verifier(
        stage="dev_cross_check", trigger="fail",
        req_id="REQ-8", project_id="p",
    )
    _, kwargs = fake.follow_up_issue.await_args
    prompt = kwargs["prompt"]
    assert "exit_code: `2`" in prompt
    assert "unit pass" in prompt
    assert "ERROR: type mismatch" in prompt
    assert "机械 checker 输出" in prompt


@pytest.mark.asyncio
async def test_invoke_verifier_skips_checker_context_on_success(monkeypatch):
    """trigger=success 时不查 artifact_checks。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)

    queries: list = []

    class FakePool:
        async def fetchrow(self, sql, *args):
            queries.append((sql, args))
            return None

        async def execute(self, sql, *args):
            pass

    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: FakePool())
    monkeypatch.setattr(
        "orchestrator.actions._verifier.req_state.update_context", AsyncMock()
    )

    await v.invoke_verifier(
        stage="staging_test", trigger="success",
        req_id="REQ-9", project_id="p",
    )
    assert queries == [], "success trigger should not query artifact_checks"
    fake.follow_up_issue.assert_awaited_once()


@pytest.mark.asyncio
async def test_invoke_verifier_no_checker_context_when_db_empty(monkeypatch):
    """artifact_checks 无记录时 prompt 正常渲染，不抛异常。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)

    class FakePool:
        async def fetchrow(self, sql, *args):
            return None

        async def execute(self, sql, *args):
            pass

    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: FakePool())
    monkeypatch.setattr(
        "orchestrator.actions._verifier.req_state.update_context", AsyncMock()
    )

    await v.invoke_verifier(
        stage="spec_lint", trigger="fail",
        req_id="REQ-10", project_id="p",
    )
    _, kwargs = fake.follow_up_issue.await_args
    prompt = kwargs["prompt"]
    assert "verifier-agent" in prompt
    assert "REQ-10" in prompt


@pytest.mark.asyncio
async def test_invoke_verifier_rejects_unknown_stage():
    from orchestrator.actions import _verifier as v
    with pytest.raises(ValueError):
        await v.invoke_verifier(
            stage="nonsense", trigger="success",
            req_id="R", project_id="p",
        )


@pytest.mark.asyncio
async def test_invoke_verifier_rejects_unknown_trigger():
    from orchestrator.actions import _verifier as v
    with pytest.raises(ValueError):
        await v.invoke_verifier(
            stage="dev_cross_check", trigger="maybe",
            req_id="R", project_id="p",
        )


# ─── 5. 所有 12 个 prompt 模板都能渲染 ─────────────────────────────────────

@pytest.mark.parametrize("stage", [
    "analyze", "spec_lint", "dev_cross_check", "staging_test", "pr_ci", "accept",
    "challenger", "analyze_artifact_check",
])
@pytest.mark.parametrize("trigger", ["success", "fail"])
def test_all_verifier_prompts_render(stage, trigger):
    from orchestrator.prompts import render
    out = render(
        f"verifier/{stage}_{trigger}.md.j2",
        req_id="REQ-42",
        stage=stage,
        trigger=trigger,
        stderr_tail="boom",
        history=[],
        artifact_paths=[],
        checker_stdout="out",
        checker_stderr="err",
        checker_exit_code=1,
    )
    assert "REQ-42" in out
    assert "verifier-agent" in out
    # decision schema 文本必须在 —— 强校验 agent 输出格式
    assert '"action":' in out
    assert "pass" in out and "escalate" in out


def test_tail_lines_truncation():
    from orchestrator.actions._verifier import _tail_lines
    long = "\n".join(f"line{i}" for i in range(100))
    got = _tail_lines(long, 50)
    assert got.count("\n") == 49
    assert "line49" not in got
    assert "line50" in got
    assert "line99" in got


def test_tail_lines_short_text_preserved():
    from orchestrator.actions._verifier import _tail_lines
    short = "a\nb\nc"
    assert _tail_lines(short, 50) == short


def test_tail_lines_none_and_empty():
    from orchestrator.actions._verifier import _tail_lines
    assert _tail_lines(None, 50) == ""
    assert _tail_lines("", 50) == ""


# ─── 6. action handlers ─────────────────────────────────────────────────

def make_body(issue_id="src-1", project_id="p", event="session.completed"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": event, "title": "", "tags": [], "issueNumber": None,
    })()


@pytest.mark.asyncio
async def test_start_fixer_creates_issue_with_fixer_tags(monkeypatch):
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fix-1")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.start_fixer(
        body=make_body(project_id="proj-x"), req_id="REQ-9", tags=[],
        ctx={
            "verifier_stage": "staging_test",
            "verifier_fixer": "spec",
            "verifier_scope": "openspec/changes/REQ-9/",
            "verifier_reason": "spec contract 漏字段",
            "verifier_issue_id": "vfy-old",
        },
    )
    assert out["fixer_issue_id"] == "fix-1"
    assert out["fixer"] == "spec"
    assert out["stage"] == "staging_test"

    _, kwargs = fake.create_issue.await_args
    assert "fixer" in kwargs["tags"]
    assert "REQ-9" in kwargs["tags"]
    assert "fixer:spec" in kwargs["tags"]
    assert "parent-stage:staging_test" in kwargs["tags"]
    assert kwargs["use_worktree"] is True

    # follow-up prompt 里 scope + reason 带入
    _, fu = fake.follow_up_issue.await_args
    assert "openspec/changes/REQ-9/" in fu["prompt"]
    assert "spec contract 漏字段" in fu["prompt"]


@pytest.mark.asyncio
async def test_start_fixer_dev_uses_dedicated_prompt(monkeypatch):
    """fixer=dev 时用 verifier-fix-dev.md.j2，不是通用 bugfix。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fix-dev")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.start_fixer(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "dev_cross_check", "verifier_fixer": "dev"},
    )
    assert out["fixer"] == "dev"
    _, fu = fake.follow_up_issue.await_args
    assert "DEV FIXER" in fu["prompt"]
    assert "LOCKED：只改业务代码" in fu["prompt"]
    assert "LOCKED：不改 spec" in fu["prompt"]


@pytest.mark.asyncio
async def test_start_fixer_spec_uses_dedicated_prompt(monkeypatch):
    """fixer=spec 时用 verifier-fix-spec.md.j2，不是通用 bugfix。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fix-spec")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.start_fixer(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "spec_lint", "verifier_fixer": "spec"},
    )
    assert out["fixer"] == "spec"
    _, fu = fake.follow_up_issue.await_args
    assert "SPEC FIXER" in fu["prompt"]
    assert "LOCKED：只改 spec 相关文件" in fu["prompt"]
    assert "LOCKED：不改业务代码" in fu["prompt"]


@pytest.mark.asyncio
async def test_start_fixer_target_repo_passed_to_prompt(monkeypatch):
    """ctx.verifier_target_repo 透传给 prompt 模板。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fix-tr")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    await v.start_fixer(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={
            "verifier_stage": "staging_test",
            "verifier_fixer": "dev",
            "verifier_target_repo": "owner/repo-a",
        },
    )
    _, fu = fake.follow_up_issue.await_args
    assert "TARGET_REPO=owner/repo-a" in fu["prompt"]
    assert "只改 `owner/repo-a` 这一个仓的业务代码" in fu["prompt"]


@pytest.mark.asyncio
async def test_start_fixer_defaults_to_dev(monkeypatch):
    """ctx 里没 verifier_fixer 时兜底 dev。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fix-2")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.start_fixer(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "dev"},
    )
    assert out["fixer"] == "dev"


# ─── 7. fixer round cap ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_fixer_persists_round_counter(monkeypatch):
    """每次 start_fixer 都把下一轮号写进 ctx.fixer_round。

    第一次：ctx.fixer_round 不存在 → 写 1。第 N 次：写 N。
    bugfix prompt 的 round_n 同时升到 next_round（上一版恒为 1，看不出轮次）。
    """
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fix-3")
    patch_bkd(monkeypatch, fake)

    patches: list = []

    async def fake_update(pool, req_id, patch):
        patches.append(patch)
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.start_fixer(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "dev_cross_check", "fixer_round": 2},
    )
    assert out["fixer_round"] == 3
    # ctx 写了 fixer_round=3
    rounds = [p for p in patches if "fixer_round" in p]
    assert rounds and rounds[-1]["fixer_round"] == 3
    # 创 issue 时带 round:3 tag
    _, kwargs = fake.create_issue.await_args
    assert "round:3" in kwargs["tags"]
    # bugfix prompt 渲染里出现 ROUND=3
    _, fu = fake.follow_up_issue.await_args
    assert "ROUND=3" in fu["prompt"]


@pytest.mark.asyncio
async def test_start_fixer_caps_at_default_5(monkeypatch):
    """ctx.fixer_round=5（已起 5 轮）+ 第 6 次调 start_fixer → escalate（不开 fixer）。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    patch_bkd(monkeypatch, fake)

    patches: list = []

    async def fake_update(pool, req_id, patch):
        patches.append(patch)
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.start_fixer(
        body=make_body(), req_id="REQ-9", tags=["verify:dev_cross_check"],
        ctx={"verifier_stage": "dev_cross_check", "fixer_round": 5},
    )
    # 不创 fixer issue
    fake.create_issue.assert_not_called()
    fake.follow_up_issue.assert_not_called()
    # emit verify.escalate
    assert out["emit"] == Event.VERIFY_ESCALATE.value
    assert out["reason"] == "fixer-round-cap"
    # ctx 标了 escalated_reason
    reasons = [p for p in patches if "escalated_reason" in p]
    assert reasons
    assert reasons[-1]["escalated_reason"] == "fixer-round-cap"
    assert reasons[-1]["fixer_round_cap_hit"] == 5


@pytest.mark.asyncio
async def test_start_fixer_cap_respects_setting_override(monkeypatch):
    """settings.fixer_round_cap 可被运维覆盖。设 cap=2 + 已跑 2 轮 → 第 3 次 escalate。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fix-cap")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)
    monkeypatch.setattr("orchestrator.actions._verifier.settings.fixer_round_cap", 2)

    # 跑第 2 轮（next=2，恰好等于 cap，allowed）
    out = await v.start_fixer(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "dev_cross_check", "fixer_round": 1},
    )
    assert out["fixer_round"] == 2

    # 跑第 3 轮（next=3 > cap=2 → escalate）
    out2 = await v.start_fixer(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "dev_cross_check", "fixer_round": 2},
    )
    assert out2["emit"] == Event.VERIFY_ESCALATE.value
    assert out2["reason"] == "fixer-round-cap"


@pytest.mark.asyncio
async def test_start_fixer_first_round_with_no_ctx_field(monkeypatch):
    """ctx 里完全没 fixer_round 字段 → 视为 0，next_round=1，正常起 fixer。"""
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="fix-first")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.start_fixer(
        body=make_body(), req_id="REQ-9", tags=[],
        ctx={"verifier_stage": "dev_cross_check"},
    )
    assert out["fixer_round"] == 1
    fake.create_issue.assert_called_once()


@pytest.mark.asyncio
async def test_invoke_verifier_for_staging_test_fail(monkeypatch):
    """B2：专门 handler 写死 stage=staging_test，不再从 tags sniff。

    用上游 dev issue 的 tags 调（机械 checker 没自己 issue，webhook tags
    就是 dev）—— 旧实现会按 tag 误路成 dev，新实现应稳定落 staging_test。
    """
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="vfy-3")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)

    class FakePool:
        async def fetchrow(self, sql, *args):
            return None
        async def execute(self, sql, *args):
            pass
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: FakePool())

    out = await v.invoke_verifier_for_staging_test_fail(
        body=make_body(project_id="proj-x"),
        req_id="REQ-9",
        tags=["dev", "REQ-9"],
        ctx={},
    )
    assert out["stage"] == "staging_test"
    assert out["trigger"] == "fail"

    _, kwargs = fake.create_issue.await_args
    assert "verify:staging_test" in kwargs["tags"]
    assert "trigger:fail" in kwargs["tags"]


@pytest.mark.asyncio
async def test_invoke_verifier_for_pr_ci_fail(monkeypatch):
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="vfy-pr")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)

    class FakePool:
        async def fetchrow(self, sql, *args):
            return None
        async def execute(self, sql, *args):
            pass
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: FakePool())

    out = await v.invoke_verifier_for_pr_ci_fail(
        body=make_body(project_id="proj-x"),
        req_id="REQ-9",
        tags=["dev", "REQ-9"],
        ctx={},
    )
    assert out["stage"] == "pr_ci"
    assert out["trigger"] == "fail"

    _, kwargs = fake.create_issue.await_args
    assert "verify:pr_ci" in kwargs["tags"]


@pytest.mark.asyncio
async def test_invoke_verifier_for_accept_fail(monkeypatch):
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="vfy-ac")
    patch_bkd(monkeypatch, fake)

    async def fake_update(pool, req_id, patch):
        pass
    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.invoke_verifier_for_accept_fail(
        body=make_body(project_id="proj-x"),
        req_id="REQ-9",
        tags=["watchdog:accept-tearing-down"],
        ctx={},
    )
    assert out["stage"] == "accept"
    assert out["trigger"] == "fail"

    _, kwargs = fake.create_issue.await_args
    assert "verify:accept" in kwargs["tags"]


@pytest.mark.asyncio
async def test_invoke_verifier_after_fix_passes_history(monkeypatch):
    from orchestrator.actions import _verifier as v
    fake = make_fake_bkd()
    fake.create_issue.return_value = FakeIssue(id="vfy-2")
    patch_bkd(monkeypatch, fake)

    history_updates: list = []

    async def fake_update(pool, req_id, patch):
        history_updates.append(patch)

    monkeypatch.setattr("orchestrator.actions._verifier.req_state.update_context", fake_update)
    monkeypatch.setattr("orchestrator.actions._verifier.db.get_pool", lambda: None)

    out = await v.invoke_verifier_after_fix(
        body=make_body(project_id="p"), req_id="REQ-9", tags=[],
        ctx={
            "verifier_stage": "staging_test",
            "fixer_role": "dev",
            "fixer_issue_id": "fix-1",
            "verifier_history": [{"round": 1}],
        },
    )
    assert out["verifier_issue_id"] == "vfy-2"
    # 至少一次 update 带有累积的 history
    hist_writes = [p for p in history_updates if "verifier_history" in p]
    assert hist_writes, "should persist verifier_history"
    hist = hist_writes[-1]["verifier_history"]
    assert len(hist) == 2
    assert hist[-1] == {"fixer": "dev", "fixer_issue_id": "fix-1"}
