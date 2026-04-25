"""Contract tests: pr_ci_watch SHA re-fetch per polling tick (PRCISHAR-S1 ~ S7).

Black-box challenger. Mock boundaries:
  • orchestrator.checkers.pr_ci_watch._get_pr_info  ← spec-named boundary
  • httpx check-runs responses via pytest-httpx      ← external GitHub API boundary

REQ-pr-ci-watch-sha-refresh-1777076876
"""
from __future__ import annotations

import re
from unittest.mock import patch

import httpx
import pytest

from orchestrator.checkers._types import CheckResult
from orchestrator.checkers import pr_ci_watch as checker

_MODULE = "orchestrator.checkers.pr_ci_watch"
REPO = "phona/sisyphus"
BRANCH = "feat/REQ-prcishar-test"
REQ = "REQ-prcishar-contract-test"

SHA_A = "aaabbb111222"
SHA_B = "cccddd333444"

FAST_POLL = 0.01
FAST_TIMEOUT = 30.0


# ── helpers ──────────────────────────────────────────────────────────────

def _open(sha: str):
    return (1, sha, "open")

def _merged(sha: str):
    return (1, sha, "merged")

def _closed(sha: str):
    return (1, sha, "closed")

def _cr_url(sha: str) -> re.Pattern:
    return re.compile(rf"https://api\.github\.com/repos/{re.escape(REPO)}/commits/{re.escape(sha)}/check-runs.*")

def _pending_cr(sha: str) -> dict:
    return {
        "total_count": 1,
        "check_runs": [{"id": 1, "name": "CI", "head_sha": sha, "status": "in_progress", "conclusion": None}],
    }

def _success_cr(sha: str) -> dict:
    return {
        "total_count": 1,
        "check_runs": [{"id": 1, "name": "CI", "head_sha": sha, "status": "completed", "conclusion": "success"}],
    }

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


# ── S1 + S2: force-push → switch to new SHA, new SHA passes ──────────────

async def test_prcishar_s1_s2_force_push_switches_sha_then_passes(httpx_mock):
    """S1: SHA flip clears check-runs cache and restarts polling against new SHA.
    S2: new SHA's check-runs complete with success → passed=True.
    """
    # pre-loop: SHA_A  |  tick-0: SHA_A (no flip yet)  |  tick-1+: SHA_B (flip!)
    call_iter = iter(_call_seq(_open(SHA_A), _open(SHA_A), _open(SHA_B)))

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    httpx_mock.add_response(url=_cr_url(SHA_A), json=_pending_cr(SHA_A), is_reusable=True)
    httpx_mock.add_response(url=_cr_url(SHA_B), json=_success_cr(SHA_B), is_reusable=True)

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0


# ── S3: 5 flips → continue polling, eventually pass ──────────────────────

async def test_prcishar_s3_five_flips_does_not_trigger_failure(httpx_mock):
    """S3: flip_count=5 is within the allowed limit; polling continues normally."""
    # pre-loop + 5 alternating flips, then stable at SHA_B → pass
    sha_seq = [SHA_A, SHA_B, SHA_A, SHA_B, SHA_A, SHA_B, SHA_A, SHA_B]
    call_iter = iter([_open(s) for s in sha_seq] + [_open(SHA_B)] * 100)

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    httpx_mock.add_response(url=_cr_url(SHA_A), json=_pending_cr(SHA_A), is_reusable=True)
    # SHA_B eventually succeeds
    httpx_mock.add_response(url=_cr_url(SHA_B), json=_pending_cr(SHA_B), is_reusable=True)
    httpx_mock.add_response(url=_cr_url(SHA_B), json=_success_cr(SHA_B))

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    assert result.passed is True
    assert "too-many-sha-flips" not in (result.stdout_tail or "")


# ── S4: 6th flip → fail too-many-sha-flips ───────────────────────────────

async def test_prcishar_s4_sixth_flip_fails_too_many_sha_flips(httpx_mock):
    """S4: flip_count reaches 6 → terminal fail with reason too-many-sha-flips."""
    # pre-loop: SHA_A; 6 alternating flips (A→B→A→B→A→B→A)
    sha_seq = [SHA_A, SHA_B, SHA_A, SHA_B, SHA_A, SHA_B, SHA_A, SHA_B]
    call_iter = iter([_open(s) for s in sha_seq] + [_open(SHA_B)] * 100)

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    httpx_mock.add_response(url=_cr_url(SHA_A), json=_pending_cr(SHA_A), is_reusable=True)
    httpx_mock.add_response(url=_cr_url(SHA_B), json=_pending_cr(SHA_B), is_reusable=True)

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    assert result.passed is False
    assert result.exit_code == 1
    assert "too-many-sha-flips" in (result.stdout_tail or "")


# ── S5: PR merged → pass immediately (no check-runs wait) ────────────────

async def test_prcishar_s5_pr_merged_returns_pass_immediately(httpx_mock):
    """S5: PR transitions to merged during loop → watch_pr_ci returns passed=True
    without waiting for check-runs completion."""
    # pre-loop: open  |  first loop tick: merged
    call_iter = iter(_call_seq(_open(SHA_A), _merged(SHA_A)))

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    # optional: function might or might not read check-runs before seeing merged
    httpx_mock.add_response(
        url=_cr_url(SHA_A), json=_pending_cr(SHA_A),
        is_reusable=True, is_optional=True,
    )

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    assert result.passed is True
    assert result.exit_code == 0
    assert "merged" in (result.stdout_tail or "").lower()


# ── S6: PR closed (no merge) → fail pr-closed-without-merge ──────────────

async def test_prcishar_s6_pr_closed_returns_fail(httpx_mock):
    """S6: PR closed without merge → passed=False, stdout_tail contains
    pr-closed-without-merge."""
    call_iter = iter(_call_seq(_open(SHA_A), _closed(SHA_A)))

    async def fake_get_pr_info(*args, **kwargs):
        return next(call_iter)

    httpx_mock.add_response(
        url=_cr_url(SHA_A), json=_pending_cr(SHA_A),
        is_reusable=True, is_optional=True,
    )

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    assert result.passed is False
    assert result.exit_code == 1
    assert "pr-closed-without-merge" in (result.stdout_tail or "")


# ── S7: in-loop refetch HTTP error → warning + retry, no immediate fail ──

async def test_prcishar_s7_refetch_http_error_retries_not_fail(httpx_mock):
    """S7: httpx.HTTPError from _get_pr_info inside the loop is treated as transient.
    System logs a warning, uses cached SHA for check-runs, and continues.
    Next successful tick resumes normal operation."""
    call_count = 0

    async def fake_get_pr_info(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            # First in-loop tick: simulates a transient network error
            raise httpx.HTTPError("transient connection reset")
        if call_count >= 3:
            return _merged(SHA_A)
        return _open(SHA_A)

    # check-runs for cached SHA_A on the error tick
    httpx_mock.add_response(url=_cr_url(SHA_A), json=_pending_cr(SHA_A), is_reusable=True)

    with patch(f"{_MODULE}._get_pr_info", side_effect=fake_get_pr_info):
        result = await _watch()

    # must not fail immediately on the error tick
    assert result.passed is True
    # _get_pr_info must have been called ≥3 times: initial + error tick + recovery tick
    assert call_count >= 3
