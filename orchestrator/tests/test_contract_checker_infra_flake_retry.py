"""Contract tests for REQ-checker-infra-flake-retry-1777247423.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-checker-infra-flake-retry-1777247423/specs/checker-infra-flake-retry/spec.md

Scenarios covered:
  CIFR-S1   classify_failure → "dns" for stderr "Could not resolve host"
  CIFR-S2   classify_failure → None for generic make failure
  CIFR-S3   classify_failure → None when exit_code is zero
  CIFR-S4   run_with_flake_retry single pass → (result, 1, None), no sleep
  CIFR-S5   run_with_flake_retry non-flake fail → (result, 1, None), no sleep
  CIFR-S6   run_with_flake_retry flake fail + recover → (result, 2, "flake-retry-recovered:dns")
  CIFR-S7   run_with_flake_retry both flake fail → (result, 2, "flake-retry-exhausted:dns")
  CIFR-S8   max_retries=0 → (result, 1, None), no retry
  CIFR-S9   CheckResult defaults: attempts=1, reason=None
  CIFR-S10  dev_cross_check recovers from one DNS flake (attempts=2, passed=True)
  CIFR-S11  staging_test does not retry real test failure (attempts=1, reason=None)
  CIFR-S12  pr_ci_watch does not import _flake or call run_with_flake_retry
  CIFR-S13  migration 0009 adds IF NOT EXISTS columns and index

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _exec(exit_code: int, stdout: str = "", stderr: str = "") -> types.SimpleNamespace:
    """Minimal fake ExecResult with required fields."""
    return types.SimpleNamespace(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=0.1)


# ── CIFR-S1 ──────────────────────────────────────────────────────────────────


def test_cifr_s1_classify_failure_dns_tag_for_could_not_resolve_host() -> None:
    """CIFR-S1: stderr containing 'Could not resolve host' → reason_tag 'dns'."""
    from orchestrator.checkers._flake import classify_failure

    tag = classify_failure(
        stdout_tail="",
        stderr_tail="fatal: repository not accessible\nCould not resolve host github.com",
        exit_code=128,
    )
    assert tag == "dns", (
        f"CIFR-S1: classify_failure MUST return 'dns' for 'Could not resolve host' in stderr; "
        f"got {tag!r}"
    )


# ── CIFR-S2 ──────────────────────────────────────────────────────────────────


def test_cifr_s2_classify_failure_none_for_generic_make_error() -> None:
    """CIFR-S2: generic make/test failure → None (real business failure, not infra flake)."""
    from orchestrator.checkers._flake import classify_failure

    tag = classify_failure(
        stdout_tail="FAIL TestFoo",
        stderr_tail="make: *** [Makefile:42] Error 1",
        exit_code=2,
    )
    assert tag is None, (
        f"CIFR-S2: classify_failure MUST return None for generic make failure; got {tag!r}"
    )


# ── CIFR-S3 ──────────────────────────────────────────────────────────────────


def test_cifr_s3_classify_failure_none_when_exit_code_zero() -> None:
    """CIFR-S3: exit_code=0 → None regardless of stderr content."""
    from orchestrator.checkers._flake import classify_failure

    tag = classify_failure(
        stdout_tail="",
        stderr_tail="Could not resolve host github.com",
        exit_code=0,
    )
    assert tag is None, (
        f"CIFR-S3: classify_failure MUST return None when exit_code==0 even if "
        f"stderr matches an infra-flake pattern; got {tag!r}"
    )


# ── CIFR-S4 ──────────────────────────────────────────────────────────────────


async def test_cifr_s4_single_pass_returns_attempts_1_reason_none() -> None:
    """CIFR-S4: single passing attempt → (result, 1, None), coro_factory called once, no sleep."""
    from orchestrator.checkers._flake import run_with_flake_retry

    call_count = 0
    ok_result = _exec(exit_code=0, stdout="ok")

    async def coro_factory():
        nonlocal call_count
        call_count += 1
        return ok_result

    with patch("asyncio.sleep") as mock_sleep:
        result, attempts, reason = await run_with_flake_retry(
            coro_factory=coro_factory,
            stage="spec_lint",
            req_id="REQ-test",
            max_retries=2,
            backoff_sec=5,
        )

    assert attempts == 1, f"CIFR-S4: attempts MUST be 1 for single pass; got {attempts}"
    assert reason is None, f"CIFR-S4: reason MUST be None for single pass; got {reason!r}"
    assert call_count == 1, (
        f"CIFR-S4: coro_factory MUST be called exactly once; called {call_count} times"
    )
    mock_sleep.assert_not_called()


# ── CIFR-S5 ──────────────────────────────────────────────────────────────────


async def test_cifr_s5_non_flake_fail_no_retry_no_sleep() -> None:
    """CIFR-S5: non-flake failure → (result, 1, None), called once, sleep NOT invoked."""
    from orchestrator.checkers._flake import run_with_flake_retry

    call_count = 0
    fail_result = _exec(exit_code=2, stdout="", stderr="make: *** Error 1")

    async def coro_factory():
        nonlocal call_count
        call_count += 1
        return fail_result

    with patch("asyncio.sleep") as mock_sleep:
        result, attempts, reason = await run_with_flake_retry(
            coro_factory=coro_factory,
            stage="dev_cross_check",
            req_id="REQ-test",
            max_retries=2,
            backoff_sec=5,
        )

    assert attempts == 1, f"CIFR-S5: attempts MUST be 1 for non-flake fail; got {attempts}"
    assert reason is None, f"CIFR-S5: reason MUST be None for non-flake fail; got {reason!r}"
    assert call_count == 1, (
        f"CIFR-S5: coro_factory MUST be called exactly once (no retry); called {call_count} times"
    )
    mock_sleep.assert_not_called()


# ── CIFR-S6 ──────────────────────────────────────────────────────────────────


async def test_cifr_s6_flake_fail_recovers_on_retry() -> None:
    """CIFR-S6: first call=dns flake, second call=pass → (result2, 2, 'flake-retry-recovered:dns')."""
    from orchestrator.checkers._flake import run_with_flake_retry

    call_count = 0
    flake_result = _exec(exit_code=128, stderr="Could not resolve host github.com")
    ok_result = _exec(exit_code=0, stdout="ok")

    async def coro_factory():
        nonlocal call_count
        call_count += 1
        return flake_result if call_count == 1 else ok_result

    result, attempts, reason = await run_with_flake_retry(
        coro_factory=coro_factory,
        stage="spec_lint",
        req_id="REQ-test",
        max_retries=1,
        backoff_sec=0,
    )

    assert attempts == 2, f"CIFR-S6: attempts MUST be 2; got {attempts}"
    assert reason == "flake-retry-recovered:dns", (
        f"CIFR-S6: reason MUST be 'flake-retry-recovered:dns'; got {reason!r}"
    )
    assert call_count == 2, (
        f"CIFR-S6: coro_factory MUST be called exactly twice; called {call_count} times"
    )
    assert result.exit_code == 0, (
        f"CIFR-S6: final result MUST be the second (passing) ExecResult; exit_code={result.exit_code}"
    )


# ── CIFR-S7 ──────────────────────────────────────────────────────────────────


async def test_cifr_s7_flake_fail_both_attempts_exhausted() -> None:
    """CIFR-S7: both attempts are dns flake → (result2, 2, 'flake-retry-exhausted:dns')."""
    from orchestrator.checkers._flake import run_with_flake_retry

    call_count = 0
    flake_result = _exec(exit_code=128, stderr="Could not resolve host github.com")

    async def coro_factory():
        nonlocal call_count
        call_count += 1
        return flake_result

    result, attempts, reason = await run_with_flake_retry(
        coro_factory=coro_factory,
        stage="staging_test",
        req_id="REQ-test",
        max_retries=1,
        backoff_sec=0,
    )

    assert attempts == 2, f"CIFR-S7: attempts MUST be 2; got {attempts}"
    assert reason == "flake-retry-exhausted:dns", (
        f"CIFR-S7: reason MUST be 'flake-retry-exhausted:dns'; got {reason!r}"
    )
    assert call_count == 2, (
        f"CIFR-S7: coro_factory MUST be called exactly twice; called {call_count} times"
    )


# ── CIFR-S8 ──────────────────────────────────────────────────────────────────


async def test_cifr_s8_max_retries_zero_disables_retry() -> None:
    """CIFR-S8: max_retries=0 → no retry even if flake pattern matches, (result, 1, None)."""
    from orchestrator.checkers._flake import run_with_flake_retry

    call_count = 0
    flake_result = _exec(exit_code=128, stderr="Could not resolve host github.com")

    async def coro_factory():
        nonlocal call_count
        call_count += 1
        return flake_result

    result, attempts, reason = await run_with_flake_retry(
        coro_factory=coro_factory,
        stage="spec_lint",
        req_id="REQ-test",
        max_retries=0,
        backoff_sec=5,
    )

    assert attempts == 1, f"CIFR-S8: attempts MUST be 1 when max_retries=0; got {attempts}"
    assert reason is None, f"CIFR-S8: reason MUST be None when max_retries=0; got {reason!r}"
    assert call_count == 1, (
        f"CIFR-S8: coro_factory MUST be called exactly once when max_retries=0; called {call_count} times"
    )


# ── CIFR-S9 ──────────────────────────────────────────────────────────────────


def test_cifr_s9_checkresult_defaults_attempts_1_reason_none() -> None:
    """CIFR-S9: CheckResult constructed without attempts/reason → attempts=1, reason=None."""
    from orchestrator.checkers._types import CheckResult

    r = CheckResult(
        passed=True,
        exit_code=0,
        stdout_tail="",
        stderr_tail="",
        duration_sec=1.0,
        cmd="make ci-lint",
    )
    assert r.attempts == 1, (
        f"CIFR-S9: CheckResult.attempts default MUST be 1; got {r.attempts!r}"
    )
    assert r.reason is None, (
        f"CIFR-S9: CheckResult.reason default MUST be None; got {r.reason!r}"
    )


# ── CIFR-S10 ─────────────────────────────────────────────────────────────────


async def test_cifr_s10_dev_cross_check_recovers_from_one_dns_flake(monkeypatch) -> None:
    """CIFR-S10: dev_cross_check wires flake retry — DNS flake on attempt 1, pass on attempt 2."""
    import orchestrator.config as config_mod
    from orchestrator import k8s_runner
    from orchestrator.checkers import dev_cross_check as dcc

    call_count = 0
    flake = _exec(exit_code=128, stderr="Could not resolve host github.com")
    ok = _exec(exit_code=0, stdout="lint ok")

    class _FakeRC:
        async def exec_in_runner(self, req_id: str, cmd: str, **kwargs):
            nonlocal call_count
            call_count += 1
            return flake if call_count == 1 else ok

    monkeypatch.setattr(k8s_runner, "get_controller", lambda: _FakeRC())
    monkeypatch.setattr(config_mod.settings, "checker_infra_flake_retry_enabled", True)
    monkeypatch.setattr(config_mod.settings, "checker_infra_flake_retry_max", 1)
    monkeypatch.setattr(config_mod.settings, "checker_infra_flake_retry_backoff_sec", 0)

    result = await dcc.run_dev_cross_check("REQ-X")

    assert result.passed is True, (
        f"CIFR-S10: CheckResult.passed MUST be True after DNS flake recovery; got {result.passed}"
    )
    assert result.exit_code == 0, (
        f"CIFR-S10: CheckResult.exit_code MUST be 0 after recovery; got {result.exit_code}"
    )
    assert result.attempts == 2, (
        f"CIFR-S10: CheckResult.attempts MUST be 2 (original + 1 retry); got {result.attempts}"
    )
    assert result.reason is not None and "flake-retry-recovered" in result.reason, (
        f"CIFR-S10: CheckResult.reason MUST contain 'flake-retry-recovered'; got {result.reason!r}"
    )
    assert call_count == 2, (
        f"CIFR-S10: exec_in_runner MUST be called exactly twice; called {call_count} times"
    )


# ── CIFR-S11 ─────────────────────────────────────────────────────────────────


async def test_cifr_s11_staging_test_no_retry_on_real_failure(monkeypatch) -> None:
    """CIFR-S11: staging_test does NOT retry a real test failure (non-flake), attempts=1."""
    import orchestrator.config as config_mod
    from orchestrator import k8s_runner
    from orchestrator.checkers import staging_test as st

    call_count = 0
    real_fail = _exec(exit_code=1, stdout="FAIL TestFoo", stderr="make: *** Error 1")

    class _FakeRC:
        async def exec_in_runner(self, req_id: str, cmd: str, **kwargs):
            nonlocal call_count
            call_count += 1
            return real_fail

    monkeypatch.setattr(k8s_runner, "get_controller", lambda: _FakeRC())
    monkeypatch.setattr(config_mod.settings, "checker_infra_flake_retry_enabled", True)
    monkeypatch.setattr(config_mod.settings, "checker_infra_flake_retry_max", 2)
    monkeypatch.setattr(config_mod.settings, "checker_infra_flake_retry_backoff_sec", 0)

    result = await st.run_staging_test("REQ-X")

    assert result.passed is False, (
        f"CIFR-S11: CheckResult.passed MUST be False for real test failure; got {result.passed}"
    )
    assert result.attempts == 1, (
        f"CIFR-S11: CheckResult.attempts MUST be 1 (no retry on real failure); got {result.attempts}"
    )
    assert result.reason is None, (
        f"CIFR-S11: CheckResult.reason MUST be None for non-flake fail; got {result.reason!r}"
    )
    assert call_count == 1, (
        f"CIFR-S11: exec_in_runner MUST be called exactly once (no retry); called {call_count} times"
    )


# ── CIFR-S12 ─────────────────────────────────────────────────────────────────


def test_cifr_s12_pr_ci_watch_does_not_import_flake() -> None:
    """CIFR-S12: pr_ci_watch.py MUST NOT import _flake or call run_with_flake_retry."""
    pr_ci_watch_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "orchestrator" / "checkers" / "pr_ci_watch.py"
    )
    assert pr_ci_watch_path.exists(), (
        f"CIFR-S12: pr_ci_watch.py not found at {pr_ci_watch_path}"
    )
    source = pr_ci_watch_path.read_text(encoding="utf-8")
    assert "_flake" not in source, (
        "CIFR-S12: pr_ci_watch.py MUST NOT contain any reference to '_flake' "
        "(pr_ci_watch retains its own HTTP retry semantics, separate from kubectl-exec flake retry)"
    )
    assert "run_with_flake_retry" not in source, (
        "CIFR-S12: pr_ci_watch.py MUST NOT call run_with_flake_retry"
    )


# ── CIFR-S13 ─────────────────────────────────────────────────────────────────


def test_cifr_s13_migration_idempotent_columns_and_index() -> None:
    """CIFR-S13: 0009_artifact_checks_flake.sql uses IF NOT EXISTS for both columns and index."""
    migration_path = (
        REPO_ROOT / "orchestrator" / "migrations" / "0009_artifact_checks_flake.sql"
    )
    assert migration_path.exists(), (
        f"CIFR-S13: migration file not found at {migration_path}. "
        "The migration MUST be named '0009_artifact_checks_flake.sql'."
    )
    sql = migration_path.read_text(encoding="utf-8").lower()

    assert "add column if not exists" in sql, (
        "CIFR-S13: migration MUST use 'ADD COLUMN IF NOT EXISTS' for idempotency"
    )
    assert "attempts" in sql, (
        "CIFR-S13: migration MUST define column 'attempts'"
    )
    assert "flake_reason" in sql, (
        "CIFR-S13: migration MUST define column 'flake_reason'"
    )
    assert "create index if not exists" in sql, (
        "CIFR-S13: migration MUST use 'CREATE INDEX IF NOT EXISTS' for idempotency"
    )
    assert "idx_artifact_checks_flake_reason" in sql, (
        "CIFR-S13: migration MUST create index 'idx_artifact_checks_flake_reason'"
    )
    assert "default 1" in sql, (
        "CIFR-S13: 'attempts' column MUST have DEFAULT 1 so existing rows are not null"
    )
