"""Challenger contract tests for REQ-ruff-fix-dispatch-idempotency-1777342033.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-ruff-fix-dispatch-idempotency-1777342033/specs/ruff-lint-fix/spec.md

Written by: challenger-agent (M18 — independent of dev implementation)

Scenarios covered:
  RUFF-S1  ruff check on the challenger test file exits 0 with zero I001/F841 findings
  RUFF-S2  import block: from __future__ immediately followed by blank line then
           from unittest.mock import ... with no extra blank lines between groups
  RUFF-S3  no result= assignment on invoke_verifier call in test_DISP_S2
  RUFF-S4  no result= assignment on invoke_verifier call in test_DISP_S5

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).parent.parent
_TARGET = Path(__file__).parent / "test_contract_dispatch_idempotency_challenger.py"


# ─── RUFF-S1 ──────────────────────────────────────────────────────────────────


def test_ruff_s1_ruff_check_exits_0() -> None:
    """RUFF-S1: ruff check on the challenger test file exits 0 with no I001/F841."""
    result = subprocess.run(
        [
            "uv",
            "run",
            "ruff",
            "check",
            "tests/test_contract_dispatch_idempotency_challenger.py",
        ],
        capture_output=True,
        text=True,
        cwd=_ORCHESTRATOR_DIR,
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, (
        "RUFF-S1: `uv run ruff check tests/test_contract_dispatch_idempotency_challenger.py`"
        f" must exit 0.\nGot returncode={result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "I001" not in combined, (
        f"RUFF-S1: I001 (unsorted import) must not appear in ruff output.\n{combined}"
    )
    assert "F841" not in combined, (
        f"RUFF-S1: F841 (unused variable) must not appear in ruff output.\n{combined}"
    )


# ─── RUFF-S2 ──────────────────────────────────────────────────────────────────


def test_ruff_s2_import_block_is_sorted() -> None:
    """RUFF-S2: 'from __future__ import annotations' is followed by exactly one
    blank line and then 'from unittest.mock import ...' with no additional blank
    lines between the two import groups.
    """
    lines = _TARGET.read_text().splitlines()

    future_idx = next(
        (i for i, ln in enumerate(lines) if ln.strip() == "from __future__ import annotations"),
        None,
    )
    assert future_idx is not None, (
        "RUFF-S2: 'from __future__ import annotations' must be present in the file"
    )
    assert future_idx + 1 < len(lines) and lines[future_idx + 1] == "", (
        "RUFF-S2: the line immediately after 'from __future__ import annotations' "
        f"must be blank. Got: {lines[future_idx + 1]!r}"
    )
    assert future_idx + 2 < len(lines) and lines[future_idx + 2].startswith(
        "from unittest.mock import"
    ), (
        "RUFF-S2: after the blank line, 'from unittest.mock import ...' must follow "
        f"immediately. Got: {lines[future_idx + 2]!r}"
    )


# ─── helpers ──────────────────────────────────────────────────────────────────


def _has_result_assign_on_invoke_verifier(func_node: ast.AsyncFunctionDef) -> list[int]:
    """Return line numbers of any `result = await invoke_verifier(...)` assignment."""
    bad_lines: list[int] = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == "result"):
                continue
            val = node.value
            if not isinstance(val, ast.Await):
                continue
            call = val.value
            if not isinstance(call, ast.Call):
                continue
            fn = call.func
            fn_name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if fn_name == "invoke_verifier":
                bad_lines.append(node.lineno)
    return bad_lines


def _parse_func(func_name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(_TARGET.read_text(), filename=str(_TARGET))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == func_name:
            return node
    raise AssertionError(f"Function '{func_name}' not found in {_TARGET.name}")


# ─── RUFF-S3 ──────────────────────────────────────────────────────────────────


def test_ruff_s3_no_unused_variable_in_disp_s2() -> None:
    """RUFF-S3: In test_DISP_S2_no_slug_hit_calls_create_issue_and_stores_slug,
    'await invoke_verifier(...)' must be a bare expression — no 'result =' LHS.
    """
    func_name = "test_DISP_S2_no_slug_hit_calls_create_issue_and_stores_slug"
    func_node = _parse_func(func_name)
    bad = _has_result_assign_on_invoke_verifier(func_node)
    assert not bad, (
        f"RUFF-S3: found 'result = await invoke_verifier(...)' assignment(s) at "
        f"line(s) {bad} in '{func_name}'. "
        "The call must be a bare expression with no left-hand-side assignment."
    )


# ─── RUFF-S4 ──────────────────────────────────────────────────────────────────


def test_ruff_s4_no_unused_variable_in_disp_s5() -> None:
    """RUFF-S4: In test_DISP_S5_round_aware_slug_distinguishes_fixer_rounds,
    'await invoke_verifier(...)' must be a bare expression — no 'result =' LHS.
    """
    func_name = "test_DISP_S5_round_aware_slug_distinguishes_fixer_rounds"
    func_node = _parse_func(func_name)
    bad = _has_result_assign_on_invoke_verifier(func_node)
    assert not bad, (
        f"RUFF-S4: found 'result = await invoke_verifier(...)' assignment(s) at "
        f"line(s) {bad} in '{func_name}'. "
        "The call must be a bare expression with no left-hand-side assignment."
    )
