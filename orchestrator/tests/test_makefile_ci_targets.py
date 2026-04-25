"""REQ-makefile-ci-targets-1777110320: 顶层 Makefile self-dogfood ttpos-ci 契约。

Assertions on file content (Makefile + pyproject.toml) — no `make` executable
required. The runtime semantics (BASE_REV scoping, exit-code 5 mapping) are
covered by manual recipe inspection during the change; what we lock down here
is the structural contract:

- repo-root Makefile declares ci-lint / ci-unit-test / ci-integration-test
- repo-root Makefile no longer declares dev-cross-check / ci-test
- orchestrator/pyproject.toml registers the `integration` pytest marker
- the ci-lint recipe references BASE_REV and falls back to a full ruff scan
- the ci-integration-test recipe maps pytest exit code 5 to pass
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
PYPROJECT = REPO_ROOT / "orchestrator" / "pyproject.toml"


def _makefile_text() -> str:
    return MAKEFILE.read_text(encoding="utf-8")


def _pyproject_text() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


def _has_target(makefile: str, target: str) -> bool:
    """grep '^<target>:' — same heuristic sisyphus checkers use."""
    needle = f"\n{target}:"
    return needle in ("\n" + makefile)


def test_ci_lint_target_declared() -> None:
    assert _has_target(_makefile_text(), "ci-lint"), (
        "顶层 Makefile 必须声明 ci-lint target — sisyphus dev_cross_check checker 按 "
        "`grep -q '^ci-lint:'` 决定是否对该仓跑"
    )


def test_ci_unit_test_target_declared() -> None:
    assert _has_target(_makefile_text(), "ci-unit-test"), (
        "顶层 Makefile 必须声明 ci-unit-test target — sisyphus staging_test checker 缺 "
        "ci-unit-test 或 ci-integration-test 任一就跳过整仓"
    )


def test_ci_integration_test_target_declared() -> None:
    assert _has_target(_makefile_text(), "ci-integration-test"), (
        "顶层 Makefile 必须声明 ci-integration-test target — staging_test checker 必查"
    )


def test_dev_cross_check_target_removed() -> None:
    assert not _has_target(_makefile_text(), "dev-cross-check"), (
        "dev-cross-check 已被 ci-lint 取代，应已删除"
    )


def test_ci_test_target_removed() -> None:
    assert not _has_target(_makefile_text(), "ci-test"), (
        "ci-test 已被 ci-unit-test 取代，应已删除"
    )


def test_ci_lint_recipe_honors_base_rev() -> None:
    text = _makefile_text()
    assert "BASE_REV" in text, "ci-lint recipe 必须读 BASE_REV env"
    assert "uv run ruff check" in text, "ci-lint 实现必须调 ruff（项目唯一 Python lint）"


def test_ci_lint_recipe_falls_back_to_full_scan() -> None:
    """BASE_REV 空 → 全量；BASE_REV 非空但 diff 无 .py → exit 0。"""
    text = _makefile_text()
    # 全量分支：检查 src/ tests/ 同时出现
    assert "ruff check src/ tests/" in text, "BASE_REV 空时必须 cd orchestrator && ruff check src/ tests/"


def test_ci_unit_test_excludes_integration_marker() -> None:
    text = _makefile_text()
    assert 'pytest -m "not integration"' in text, (
        "ci-unit-test 必须用 -m 'not integration' 排除 integration 测试"
    )


def test_ci_integration_test_uses_integration_marker() -> None:
    text = _makefile_text()
    assert "pytest -m integration" in text, (
        "ci-integration-test 必须用 -m integration 选 integration 测试"
    )


def test_ci_integration_test_treats_exit_5_as_pass() -> None:
    """pytest 退码 5 = no tests collected。bootstrap 期 sisyphus 0 个 integration 测试，
    必须把 5 映射成 pass，否则 staging_test 永远红。
    """
    text = _makefile_text()
    assert "rc -eq 5" in text, (
        "ci-integration-test recipe 必须把 pytest exit code 5（no tests collected）"
        "视为 pass — bootstrap 期 sisyphus 没有任何 @pytest.mark.integration 测试"
    )


def test_integration_marker_is_registered() -> None:
    text = _pyproject_text()
    assert "[tool.pytest.ini_options]" in text
    # marker 注册避免 PytestUnknownMarkWarning + 文档化 ci-unit-test/ci-integration-test split
    assert '"integration:' in text or "'integration:" in text, (
        "pyproject.toml [tool.pytest.ini_options].markers 必须注册 'integration' marker"
    )


def test_phony_lists_new_targets() -> None:
    """ci-lint / ci-unit-test / ci-integration-test 都是无产物 target，必须进 .PHONY。"""
    text = _makefile_text()
    # 第一行 .PHONY 声明
    phony_lines = [line for line in text.splitlines() if line.startswith(".PHONY:")]
    assert phony_lines, ".PHONY 必须存在"
    phony_blob = " ".join(phony_lines)
    for tgt in ("ci-lint", "ci-unit-test", "ci-integration-test"):
        assert tgt in phony_blob, f"{tgt} 必须列进 .PHONY"


@pytest.mark.skipif(shutil.which("make") is None, reason="make not available in this env")
def test_make_dry_run_resolves_ci_targets() -> None:
    """如果 make 在环境里可用，`make -n` 必须能解析三个 target（不报 No rule to make target）。"""
    for tgt in ("ci-lint", "ci-unit-test", "ci-integration-test"):
        result = subprocess.run(
            ["make", "-n", tgt],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"make -n {tgt} failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "No rule to make target" not in result.stderr


@pytest.mark.skipif(shutil.which("make") is None, reason="make not available in this env")
def test_make_dry_run_legacy_targets_gone() -> None:
    """make -n dev-cross-check / make -n ci-test 必须报 No rule。"""
    for tgt in ("dev-cross-check", "ci-test"):
        result = subprocess.run(
            ["make", "-n", tgt],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0, f"make -n {tgt} unexpectedly succeeded — target should be removed"
