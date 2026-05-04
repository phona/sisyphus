"""config_version startup hook 单测（P0-2）。

覆盖：
- _is_watched 白名单过滤（prompts/.j2 ✓、checkers/ ✓、config.py ✓、README.md ✗、tests/ ✗）
- git 不可用（FileNotFoundError / CalledProcessError）→ 优雅降级，不抛，不调 DB
- 首次记录路径：DB 无 last_commit → 直接 INSERT watched=["<initial-record>"]
- 同 SHA 路径：last_commit == current_sha → skip，不调 execute
- diff 有白名单文件 → INSERT，changed_files 只含白名单文件
- diff 无白名单文件 → skip
- kind 推断：只 .j2 → prompt，只 config.py → config，混合 → checker/mixed
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from orchestrator.config_version import _is_watched, maybe_record_config_change

# ── _is_watched 白名单 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("path,expected", [
    # 白名单内
    ("orchestrator/src/orchestrator/prompts/intake.md.j2",                True),
    ("orchestrator/src/orchestrator/prompts/verifier/staging_test_fail.md.j2", True),
    ("orchestrator/src/orchestrator/checkers/spec_lint.py",               True),
    ("orchestrator/src/orchestrator/checkers/staging_test.py",            True),
    ("orchestrator/src/orchestrator/config.py",                           True),
    # 白名单外
    ("README.md",                                                          False),
    ("docs/architecture.md",                                               False),
    ("tests/foo.py",                                                       False),
    ("orchestrator/tests/test_table_ttl.py",                              False),
    ("orchestrator/src/orchestrator/engine.py",                           False),
    ("orchestrator/src/orchestrator/main.py",                             False),
])
def test_is_watched(path: str, expected: bool):
    assert _is_watched(path) is expected


# ── FakePool ────────────────────────────────────────────────────────────────

class _FakePool:
    """asyncpg pool stub：fetchval 返回 last_commit，记录 execute 调用。"""

    def __init__(self, last_commit: str | None = None):
        self.last_commit = last_commit
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args):
        return self.last_commit

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))


# ── git 不可用路径 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_git_file_not_found_does_not_raise_and_skips_db():
    """git 二进制不存在（FileNotFoundError）→ 优雅返回，不调 DB。"""
    pool = _FakePool()
    with patch("orchestrator.config_version._git",
               side_effect=FileNotFoundError("git not found")):
        await maybe_record_config_change(pool)
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_git_called_process_error_does_not_raise_and_skips_db():
    """git 非零退码（CalledProcessError）→ 优雅返回，不调 DB。"""
    pool = _FakePool()
    with patch("orchestrator.config_version._git",
               side_effect=subprocess.CalledProcessError(128, "git")):
        await maybe_record_config_change(pool)
    assert pool.execute_calls == []


# ── 首次记录路径（DB 空） ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_record_inserts_with_initial_marker():
    """DB 无历史记录时，不 diff，直接 INSERT，changed_files=["<initial-record>"]。"""
    pool = _FakePool(last_commit=None)
    current_sha = "a" * 40

    with patch("orchestrator.config_version._git", return_value=current_sha):
        await maybe_record_config_change(pool)

    assert len(pool.execute_calls) == 1
    sql, args = pool.execute_calls[0]
    assert "INSERT INTO config_version" in sql
    assert "ON CONFLICT" in sql
    assert args[0] == current_sha        # version_hash
    assert args[4] == current_sha        # git_commit
    assert json.loads(args[6]) == ["<initial-record>"]   # changed_files


# ── 同 SHA → skip ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_sha_skips_insert():
    """current_sha == last_commit → 直接返回，不调 execute。"""
    sha = "b" * 40
    pool = _FakePool(last_commit=sha)

    with patch("orchestrator.config_version._git", return_value=sha):
        await maybe_record_config_change(pool)

    assert pool.execute_calls == []


# ── diff 路径：有白名单文件 → INSERT ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_diff_with_watched_files_inserts_only_watched():
    """diff 含 prompts/.j2 → INSERT；changed_files 仅含白名单文件，README.md 不含。"""
    last_sha = "c" * 40
    current_sha = "d" * 40
    pool = _FakePool(last_commit=last_sha)

    def _fake_git(*args):
        if args[0] == "rev-parse":
            return current_sha
        # diff call
        return (
            "orchestrator/src/orchestrator/prompts/intake.md.j2\n"
            "README.md\n"
            "docs/architecture.md"
        )

    with patch("orchestrator.config_version._git", side_effect=_fake_git):
        await maybe_record_config_change(pool)

    assert len(pool.execute_calls) == 1
    _, args = pool.execute_calls[0]
    changed = json.loads(args[6])
    assert "orchestrator/src/orchestrator/prompts/intake.md.j2" in changed
    assert "README.md" not in changed
    assert "docs/architecture.md" not in changed


# ── diff 路径：无白名单文件 → skip ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_diff_without_watched_files_skips_insert():
    """diff 只含 README.md / docs / tests 等非白名单文件 → 不 INSERT。"""
    last_sha = "e" * 40
    current_sha = "f" * 40
    pool = _FakePool(last_commit=last_sha)

    def _fake_git(*args):
        if args[0] == "rev-parse":
            return current_sha
        return "README.md\ndocs/architecture.md\norchestrator/tests/test_foo.py"

    with patch("orchestrator.config_version._git", side_effect=_fake_git):
        await maybe_record_config_change(pool)

    assert pool.execute_calls == []


# ── kind 推断 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kind_prompt_for_only_jinja_changes():
    """.j2 only → kind='prompt'。"""
    last_sha = "1" * 40
    current_sha = "2" * 40
    pool = _FakePool(last_commit=last_sha)

    def _fake_git(*args):
        if args[0] == "rev-parse":
            return current_sha
        return "orchestrator/src/orchestrator/prompts/execute.md.j2"

    with patch("orchestrator.config_version._git", side_effect=_fake_git):
        await maybe_record_config_change(pool)

    _, args = pool.execute_calls[0]
    assert args[1] == "prompt"


@pytest.mark.asyncio
async def test_kind_config_for_only_config_py_change():
    """config.py only → kind='config'。"""
    last_sha = "3" * 40
    current_sha = "4" * 40
    pool = _FakePool(last_commit=last_sha)

    def _fake_git(*args):
        if args[0] == "rev-parse":
            return current_sha
        return "orchestrator/src/orchestrator/config.py"

    with patch("orchestrator.config_version._git", side_effect=_fake_git):
        await maybe_record_config_change(pool)

    _, args = pool.execute_calls[0]
    assert args[1] == "config"


@pytest.mark.asyncio
async def test_kind_checker_for_only_checker_change():
    """checkers/ only → kind='checker'。"""
    last_sha = "5" * 40
    current_sha = "6" * 40
    pool = _FakePool(last_commit=last_sha)

    def _fake_git(*args):
        if args[0] == "rev-parse":
            return current_sha
        return "orchestrator/src/orchestrator/checkers/spec_lint.py"

    with patch("orchestrator.config_version._git", side_effect=_fake_git):
        await maybe_record_config_change(pool)

    _, args = pool.execute_calls[0]
    assert args[1] == "checker"


@pytest.mark.asyncio
async def test_kind_mixed_for_prompt_and_config_changes():
    """prompt + config 混合 → kind 包含两者（不是单一类型）。"""
    last_sha = "7" * 40
    current_sha = "8" * 40
    pool = _FakePool(last_commit=last_sha)

    def _fake_git(*args):
        if args[0] == "rev-parse":
            return current_sha
        return (
            "orchestrator/src/orchestrator/prompts/intake.md.j2\n"
            "orchestrator/src/orchestrator/config.py"
        )

    with patch("orchestrator.config_version._git", side_effect=_fake_git):
        await maybe_record_config_change(pool)

    _, args = pool.execute_calls[0]
    kind: str = args[1]
    assert kind not in ("prompt", "config")   # must be compound
    assert "prompt" in kind
    assert "config" in kind


# ── upsert 幂等：ON CONFLICT DO NOTHING ─────────────────────────────────────

@pytest.mark.asyncio
async def test_insert_sql_has_on_conflict_do_nothing():
    """INSERT 必须携带 ON CONFLICT (version_hash) DO NOTHING，保证幂等。"""
    pool = _FakePool(last_commit=None)
    with patch("orchestrator.config_version._git", return_value="a" * 40):
        await maybe_record_config_change(pool)

    sql, _ = pool.execute_calls[0]
    assert "ON CONFLICT" in sql
    assert "DO NOTHING" in sql
