"""checkers/manifest_validate.py 单测：

- schema 合法 + admission flag off → passed
- schema 合法 + admission flag on + open_questions 非空 → pending_human
- schema 合法 + admission flag on + open_questions 空 → passed
- schema 缺 open_questions/assumptions/out_of_scope → 被拒（fail with exit 1）
- 读 PVC 挂 / yaml 坏 → 按原语义 fail（reason 不设）
"""
from __future__ import annotations

import textwrap

import pytest

from orchestrator.checkers import manifest_validate
from orchestrator.checkers._types import CheckResult
from orchestrator.k8s_runner import ExecResult


def _yaml_min(open_questions_line: str = "open_questions: []") -> str:
    # 注意：open_questions_line 必须是顶层 YAML（可多行），不能依赖 textwrap.dedent
    base = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        sources:
          - repo: phona/foo
            path: source/foo
            role: leader
            branch: stage/REQ-9-dev
        """
    )
    tail = textwrap.dedent(
        """\
        assumptions: []
        out_of_scope: []
        """
    )
    return base + open_questions_line.rstrip() + "\n" + tail


def _fake_controller(exit_code: int, stdout: str, stderr: str = "", duration: float = 0.1):
    class FakeRC:
        async def exec_in_runner(self, req_id, command, **kw):
            return ExecResult(
                exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration,
            )
    return FakeRC()


@pytest.fixture(autouse=True)
def _reset_admission_flag(monkeypatch):
    """测试默认关 flag；单测自己按需开。"""
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.settings.admission_analyze_pending_questions",
        False,
    )


# ── happy path ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_passes_when_schema_valid_and_flag_off(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, _yaml_min()),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.reason is None


@pytest.mark.asyncio
async def test_passes_when_open_questions_empty_and_flag_on(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.settings.admission_analyze_pending_questions",
        True,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, _yaml_min()),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is True
    assert result.reason is None


# ── pending_human ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pending_human_when_open_questions_not_empty(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.settings.admission_analyze_pending_questions",
        True,
    )
    yaml_body = _yaml_min(
        "open_questions:\n  - 登录支持手机号还是邮箱？\n  - 失败重试几次？"
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason == manifest_validate.REASON_OPEN_QUESTIONS_PENDING
    assert result.exit_code == 3
    # stderr_tail 必须能让 oncall 看到具体歧义项（不能裸退出码）
    assert "登录支持手机号还是邮箱" in result.stderr_tail
    assert "失败重试几次" in result.stderr_tail


@pytest.mark.asyncio
async def test_flag_off_ignores_open_questions(monkeypatch):
    """admission flag off 时，即使 open_questions 非空也不 pending（给老路径兜底）。"""
    yaml_body = _yaml_min("open_questions:\n  - 还没决定\n")
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is True
    assert result.reason is None


# ── schema fail（缺 M6 新字段） ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_open_questions_is_schema_fail(monkeypatch):
    """少了 open_questions/assumptions/out_of_scope 任一都应被 jsonschema 拒。
    这是 schema fail（exit 1），不是 pending_human（exit 3）；reason 也不该设。
    """
    yaml_body = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        sources:
          - repo: phona/foo
            path: source/foo
            role: leader
            branch: stage/REQ-9-dev
        assumptions: []
        out_of_scope: []
        """
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.settings.admission_analyze_pending_questions",
        True,  # 开 flag 也应该是 schema fail 而不是 pending_human
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason is None
    assert result.exit_code == 1
    assert "open_questions" in result.stderr_tail


@pytest.mark.asyncio
async def test_missing_assumptions_is_schema_fail(monkeypatch):
    yaml_body = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        sources:
          - repo: phona/foo
            path: source/foo
            role: leader
            branch: stage/REQ-9-dev
        open_questions: []
        out_of_scope: []
        """
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason is None
    assert "assumptions" in result.stderr_tail


@pytest.mark.asyncio
async def test_missing_out_of_scope_is_schema_fail(monkeypatch):
    yaml_body = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        sources:
          - repo: phona/foo
            path: source/foo
            role: leader
            branch: stage/REQ-9-dev
        open_questions: []
        assumptions: []
        """
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason is None
    assert "out_of_scope" in result.stderr_tail


# ── infra fail：读 PVC 挂（reason 保持 None，走老语义） ─────────────────

@pytest.mark.asyncio
async def test_read_failure_keeps_reason_none(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.settings.admission_analyze_pending_questions",
        True,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(2, "", stderr="cat: No such file"),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason is None  # 不是 pending_human
    assert result.exit_code == 2


@pytest.mark.asyncio
async def test_yaml_parse_failure_keeps_reason_none(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.settings.admission_analyze_pending_questions",
        True,
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, "::: not valid yaml :::\n  - ["),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason is None
    assert result.exit_code == 2
