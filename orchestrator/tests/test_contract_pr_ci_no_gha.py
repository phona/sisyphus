"""Contract tests: pr_ci_watch no-GHA verdict (PRCINOGHA-S1 ~ S5).

Black-box challenger. Mock boundaries:
  • orchestrator.checkers.pr_ci_watch._get_pr_info  ← spec-named boundary
  • httpx check-runs responses via pytest-httpx      ← external GitHub API boundary

REQ-pr-ci-no-gha-1777105576
"""
from __future__ import annotations

import re
from unittest.mock import patch

from orchestrator.checkers import pr_ci_watch as checker
from orchestrator.checkers._types import CheckResult

_MODULE = "orchestrator.checkers.pr_ci_watch"
REPO = "phona/sisyphus"
BRANCH = "feat/REQ-prcinogha-contract"
REQ = "REQ-prcinogha-contract-test"

SHA_A = "aaa111bbb222"

FAST_POLL = 0.01
FAST_TIMEOUT = 30.0


# ── helpers ──────────────────────────────────────────────────────────────

def _open(sha: str):
    return (1, sha, "open")


def _merged(sha: str):
    return (1, sha, "merged")


def _cr_url(sha: str) -> re.Pattern:
    return re.compile(
        rf"https://api\.github\.com/repos/{re.escape(REPO)}/commits/{re.escape(sha)}/check-runs.*"
    )


def _runs(sha: str, runs: list[dict]) -> dict:
    return {"total_count": len(runs), "check_runs": runs}


def _call_seq(*states):
    """Produce a _get_pr_info side_effect list; append 100 copies of last state."""
    last = states[-1]
    return list(states) + [last] * 100


async def _watch(**kw):
    return await checker.watch_pr_ci(
        REQ,
        branch=BRANCH,
        poll_interval_sec=FAST_POLL,
        timeout_sec=FAST_TIMEOUT,
        repos=[REPO],
        **kw,
    )


# ── S1: all-green but only review-only check-run → no-gha fail ──────────

async def test_prcinogha_s1_review_only_all_green_returns_no_gha_fail(httpx_mock):
    """S1: All check-runs completed+success but none have app.slug='github-actions'.
    watch_pr_ci MUST return passed=False, exit_code=1, and stdout_tail containing
    'no-gha-checks-ran' plus the actual check-run name."""
    review_runs = _runs(SHA_A, [
        {
            "id": 1,
            "name": "claude-review",
            "head_sha": SHA_A,
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "anthropic-claude"},
        }
    ])
    httpx_mock.add_response(url=_cr_url(SHA_A), json=review_runs, is_reusable=True)

    call_iter = iter(_call_seq(_open(SHA_A)))

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    assert isinstance(result, CheckResult)
    assert result.passed is False
    assert result.exit_code == 1
    assert "no-gha-checks-ran" in (result.stdout_tail or "")
    assert "claude-review" in (result.stdout_tail or "")


# ── S2: GHA + review-only, all green → pass ─────────────────────────────

async def test_prcinogha_s2_gha_plus_review_all_green_returns_pass(httpx_mock):
    """S2: At least one check-run has app.slug='github-actions' and all are
    completed+success. watch_pr_ci MUST return passed=True, exit_code=0.
    The review-only bot must not pollute a real CI green."""
    mixed_runs = _runs(SHA_A, [
        {
            "id": 1,
            "name": "lint",
            "head_sha": SHA_A,
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "github-actions"},
        },
        {
            "id": 2,
            "name": "claude-review",
            "head_sha": SHA_A,
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "anthropic-claude"},
        },
    ])
    httpx_mock.add_response(url=_cr_url(SHA_A), json=mixed_runs, is_reusable=True)

    call_iter = iter(_call_seq(_open(SHA_A)))

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0
    assert "no-gha-checks-ran" not in (result.stdout_tail or "")


# ── S3: check-run missing app field → conservative non-GHA → no-gha fail ─

async def test_prcinogha_s3_missing_app_field_treated_as_non_gha(httpx_mock):
    """S3: A check-run with no 'app' field MUST be treated as non-GHA (conservative).
    All such runs completed+success → no-gha verdict → passed=False, exit_code=1.
    Unknown origin cannot underwrite a CI-green."""
    no_app_runs = _runs(SHA_A, [
        {
            "id": 1,
            "name": "unknown-bot",
            "head_sha": SHA_A,
            "status": "completed",
            "conclusion": "success",
            # deliberately omitting "app" field
        }
    ])
    httpx_mock.add_response(url=_cr_url(SHA_A), json=no_app_runs, is_reusable=True)

    call_iter = iter(_call_seq(_open(SHA_A)))

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    assert isinstance(result, CheckResult)
    assert result.passed is False
    assert result.exit_code == 1
    assert "no-gha-checks-ran" in (result.stdout_tail or "")


# ── S4: pending check-run overrides no-gha ───────────────────────────────

async def test_prcinogha_s4_pending_check_run_prevents_no_gha(httpx_mock):
    """S4: One review-only completed+success AND another review-only in_progress.
    _classify MUST return 'pending' (not 'no-gha') — the GHA workflow may still
    arrive while any run is in_progress. Polling continues; once PR merges the
    function returns pass without ever emitting no-gha-checks-ran."""
    pending_runs = _runs(SHA_A, [
        {
            "id": 1,
            "name": "claude-review",
            "head_sha": SHA_A,
            "status": "completed",
            "conclusion": "success",
            "app": {"slug": "anthropic-claude"},
        },
        {
            "id": 2,
            "name": "another-review",
            "head_sha": SHA_A,
            "status": "in_progress",
            "conclusion": None,
            "app": {"slug": "anthropic-claude"},
        },
    ])
    httpx_mock.add_response(url=_cr_url(SHA_A), json=pending_runs, is_reusable=True)

    # pre-loop: open → loop tick 1: open (check-runs fetched, pending) → tick 2: merged
    call_iter = iter(_call_seq(_open(SHA_A), _open(SHA_A), _merged(SHA_A)))

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    # MUST resolve via merge, not fail with no-gha
    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert "no-gha-checks-ran" not in (result.stdout_tail or "")


# ── S5: empty check-runs → pending (unchanged behavior) ──────────────────

async def test_prcinogha_s5_empty_runs_stays_pending_not_no_gha(httpx_mock):
    """S5: Zero check-runs MUST still return 'pending', not 'no-gha'.
    The empty-runs case is explicitly NOT promoted to no-gha verdict; the existing
    polling loop continues and exit_code=124 timeout remains the safety net for
    'GHA never showed up at all'. Here the PR merges to provide a clean exit."""
    empty_runs = _runs(SHA_A, [])
    httpx_mock.add_response(url=_cr_url(SHA_A), json=empty_runs, is_reusable=True)

    # pre-loop: open → loop tick 1: open (empty runs → pending) → tick 2: merged
    call_iter = iter(_call_seq(_open(SHA_A), _open(SHA_A), _merged(SHA_A)))

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    # MUST resolve via merge, not fail with no-gha
    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert "no-gha-checks-ran" not in (result.stdout_tail or "")
