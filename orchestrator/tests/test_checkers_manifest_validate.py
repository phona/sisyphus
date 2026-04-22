"""checkers/manifest_validate.py 单测：

- schema 合法（含 open_questions 非空）→ passed（M12 不再卡歧义）
- schema 合法但无 open_questions / assumptions / out_of_scope 字段 → passed（M12 这些字段改选填）
- test / pr 段缺失或子字段缺失 → schema fail（M11 保留）
- 读 PVC 挂 / yaml 坏 → fail with reason None
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
        test:
          cmd: "make ci-unit-test"
          cwd: "source/foo"
        pr:
          repo: "phona/foo"
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


# ── happy path ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_passes_when_schema_valid(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, _yaml_min()),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.reason is None


@pytest.mark.asyncio
async def test_passes_with_nonempty_open_questions(monkeypatch):
    """M12：open_questions 非空不再卡 admission，agent 自己跟 user 谈完再 move review。"""
    yaml_body = _yaml_min(
        "open_questions:\n  - 登录支持手机号还是邮箱？\n  - 失败重试几次？"
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is True
    assert result.reason is None


@pytest.mark.asyncio
async def test_passes_without_ambiguity_fields(monkeypatch):
    """M12：open_questions / assumptions / out_of_scope 改选填，缺了不该拒。"""
    yaml_body = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        sources:
          - repo: phona/foo
            path: source/foo
            role: leader
            branch: stage/REQ-9-dev
        test:
          cmd: "make ci-unit-test"
          cwd: "source/foo"
        pr:
          repo: "phona/foo"
        """
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is True
    assert result.reason is None


# ── M12：REASON_OPEN_QUESTIONS_PENDING 常量已删 ─────────────────────────

def test_m12_no_reason_open_questions_pending():
    """M12 砍 M6 admission → 常量 / exit=3 语义都没了。"""
    assert not hasattr(manifest_validate, "REASON_OPEN_QUESTIONS_PENDING")


# ── infra fail：读 PVC 挂（reason 保持 None，走老语义） ─────────────────

@pytest.mark.asyncio
async def test_read_failure_keeps_reason_none(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(2, "", stderr="cat: No such file"),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason is None
    assert result.exit_code == 2


@pytest.mark.asyncio
async def test_yaml_parse_failure_keeps_reason_none(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, "::: not valid yaml :::\n  - ["),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason is None
    assert result.exit_code == 2


# ── M11：test / pr 必填字段 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_test_section_is_schema_fail(monkeypatch):
    """M11：manifest 缺 test 段 → admission 拒（exit 1）。"""
    yaml_body = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        sources:
          - repo: phona/foo
            path: source/foo
            role: leader
            branch: stage/REQ-9-dev
        pr:
          repo: "phona/foo"
        """
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason is None
    assert result.exit_code == 1
    assert "test" in result.stderr_tail


@pytest.mark.asyncio
async def test_missing_pr_section_is_schema_fail(monkeypatch):
    yaml_body = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        sources:
          - repo: phona/foo
            path: source/foo
            role: leader
            branch: stage/REQ-9-dev
        test:
          cmd: "make ci-unit-test"
          cwd: "source/foo"
        """
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert result.reason is None
    assert "pr" in result.stderr_tail


@pytest.mark.asyncio
async def test_test_section_missing_cmd_is_schema_fail(monkeypatch):
    """test 段在但 cmd 缺失也拒。"""
    yaml_body = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        sources:
          - repo: phona/foo
            path: source/foo
            role: leader
            branch: stage/REQ-9-dev
        test:
          cwd: "source/foo"
        pr:
          repo: "phona/foo"
        """
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert "cmd" in result.stderr_tail


@pytest.mark.asyncio
async def test_pr_section_missing_repo_is_schema_fail(monkeypatch):
    yaml_body = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        sources:
          - repo: phona/foo
            path: source/foo
            role: leader
            branch: stage/REQ-9-dev
        test:
          cmd: "make ci-unit-test"
          cwd: "source/foo"
        pr:
          number: 42
        """
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is False
    assert "repo" in result.stderr_tail


@pytest.mark.asyncio
async def test_pr_number_optional_at_analyze_time(monkeypatch):
    """pr.number 在 analyze 阶段可空（dev agent 开 PR 后回写）。"""
    # 使用 _yaml_min 的 pr: 只带 repo，正是 analyze 阶段的常态
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_validate.k8s_runner.get_controller",
        lambda: _fake_controller(0, _yaml_min()),
    )
    result = await manifest_validate.run_manifest_validate("REQ-9")
    assert result.passed is True
    assert result.reason is None
