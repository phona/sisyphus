"""端到端 shell-level 测试：spec_lint / dev_cross_check / staging_test 的 cmd 模板
在 /workspace/source 不存在或为空时**必须** exit 非 0，不能 silent-pass。

REQ-checker-empty-source-1777113775：previously 三个 checker 的 `for repo in /workspace/source/*/` 在
源目录不存在或为空时会因为 bash 默认 glob fallback / 0 次循环走到 `[ $fail -eq 0 ]` 直接 PASS。
"""
from __future__ import annotations

import subprocess

import pytest

from orchestrator.checkers.dev_cross_check import _build_cmd as build_dev_cross_check_cmd
from orchestrator.checkers.spec_lint import _build_cmd as build_spec_lint_cmd
from orchestrator.checkers.staging_test import _build_cmd as build_staging_test_cmd

_BUILDERS = [
    pytest.param(build_spec_lint_cmd, "spec_lint", id="spec_lint"),
    pytest.param(build_dev_cross_check_cmd, "dev_cross_check", id="dev_cross_check"),
    pytest.param(build_staging_test_cmd, "staging_test", id="staging_test"),
]


def _patched_cmd(builder, fake_root: str) -> str:
    """把 cmd 里所有 /workspace/source 替换成 tmp_path 下的假 root，方便本地跑。"""
    return builder("REQ-X").replace("/workspace/source", fake_root)


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_cmd_exits_nonzero_when_source_dir_missing(builder, name, tmp_path):
    """fake_root 不存在 → 第一个 guard 命中，stderr 含 missing。"""
    fake_root = str(tmp_path / "source")  # 故意不 mkdir
    r = subprocess.run(
        ["bash", "-c", _patched_cmd(builder, fake_root)],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode != 0, f"{name} should fail when source dir missing, got rc={r.returncode}"
    assert f"FAIL {name}" in r.stderr
    assert "missing" in r.stderr


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_cmd_exits_nonzero_when_source_dir_empty(builder, name, tmp_path):
    """fake_root 存在但没有任何子目录 → 第二个 guard 命中，stderr 含 empty。"""
    fake_root = tmp_path / "source"
    fake_root.mkdir()
    r = subprocess.run(
        ["bash", "-c", _patched_cmd(builder, str(fake_root))],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode != 0, f"{name} should fail when source dir empty, got rc={r.returncode}"
    assert f"FAIL {name}" in r.stderr
    assert "empty" in r.stderr


@pytest.mark.parametrize("builder,name", _BUILDERS)
def test_cmd_exits_nonzero_when_no_repo_eligible(builder, name, tmp_path):
    """fake_root 下有 1 个目录，但不是 git 仓 → fetch 失败 skip → ran=0 → 第三个 guard 命中。"""
    fake_root = tmp_path / "source"
    (fake_root / "repo-a").mkdir(parents=True)
    r = subprocess.run(
        ["bash", "-c", _patched_cmd(builder, str(fake_root))],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode != 0, f"{name} should fail when 0 repos eligible, got rc={r.returncode}"
    assert "0 source repos eligible" in r.stderr
