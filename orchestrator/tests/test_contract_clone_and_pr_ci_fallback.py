"""contract regression for REQ-clone-and-pr-ci-fallback-1777115925:

死锁 SISYPHUS_BUSINESS_REPO env fallback 不被 reintroduced —— grep 整个
production source tree 应当 0 命中。test fixture 里允许（regression guard
本身需要 setenv 来验证 ignore 行为）。
"""
from __future__ import annotations

from pathlib import Path

_PRODUCTION_SOURCE = Path(__file__).resolve().parent.parent / "src" / "orchestrator"


def test_no_sisyphus_business_repo_env_in_production_source():
    """grep 'SISYPHUS_BUSINESS_REPO' under orchestrator/src/orchestrator/ → 0 命中。

    任何重新引入这个 env 的 commit 必须先把这个 case 改回来 —— 让回归显式。
    """
    matches: list[str] = []
    for py in _PRODUCTION_SOURCE.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "SISYPHUS_BUSINESS_REPO" in line:
                matches.append(f"{py.relative_to(_PRODUCTION_SOURCE.parent.parent)}:{lineno}: {line.strip()}")
    assert matches == [], (
        "SISYPHUS_BUSINESS_REPO must not be referenced in production source "
        "(REQ-clone-and-pr-ci-fallback-1777115925). Found:\n"
        + "\n".join(matches)
    )


def test_no_os_getenv_repo_fallback_in_pr_ci_watch():
    """checker pr_ci_watch.py 不应 import os —— 已经不读任何 env。"""
    pr_ci_watch_path = _PRODUCTION_SOURCE / "checkers" / "pr_ci_watch.py"
    text = pr_ci_watch_path.read_text(encoding="utf-8")
    # `import os` 整行 / `os.getenv(` 都不该出现
    bad_lines = [
        f"line {i}: {line.rstrip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if line.strip() in ("import os",) or "os.getenv(" in line
    ]
    assert bad_lines == [], (
        "pr_ci_watch.py must not consult os.environ (REQ-clone-and-pr-ci-fallback-1777115925). "
        "Found:\n" + "\n".join(bad_lines)
    )


def test_clone_helper_module_exists():
    """server-side clone helper module 必须存在（合约层兜底）。"""
    helper = _PRODUCTION_SOURCE / "actions" / "_clone.py"
    assert helper.exists(), (
        "actions/_clone.py is the canonical server-side clone helper "
        "(REQ-clone-and-pr-ci-fallback-1777115925) and must remain importable."
    )
    text = helper.read_text(encoding="utf-8")
    assert "clone_involved_repos_into_runner" in text
    assert "/opt/sisyphus/scripts/sisyphus-clone-repos.sh" in text


def test_start_analyze_actions_invoke_clone_helper():
    """start_analyze + start_analyze_with_finalized_intent 必须 import + call _clone helper。"""
    for action_filename in ("start_analyze.py", "start_analyze_with_finalized_intent.py"):
        path = _PRODUCTION_SOURCE / "actions" / action_filename
        text = path.read_text(encoding="utf-8")
        assert "clone_involved_repos_into_runner" in text, (
            f"{action_filename} must import + invoke clone_involved_repos_into_runner "
            "(REQ-clone-and-pr-ci-fallback-1777115925)."
        )
