"""
Contract tests for REQ-fixer-cap-5-to-2-1777420814:
lower fixer round cap default from 5 to 2

Black-box behavioral contracts derived from:
  openspec/changes/REQ-fixer-cap-5-to-2-1777420814/specs/fixer-round-cap/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  FRC-S1  Settings default fixer_round_cap MUST be 2
  FRC-S2  Cap escalation triggers at round 3 with default cap=2
  FRC-S3  Round-counter behavior is isolated from default changes via configurable cap
  FRC-S4  Default cap test reflects new value (round=2 → escalate)
  FRC-S5  Q19 SQL returns fixer round distribution with escalate rates
  FRC-S6  Documentation references match the default cap of 2
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Helpers (mirroring existing contract test conventions)
# ─────────────────────────────────────────────────────────────────────────────


def _make_settings(cap: int = 2) -> Any:
    s = MagicMock()
    s.fixer_round_cap = cap
    s.watchdog_timeout_secs = 1800
    s.watchdog_interval_secs = 30
    return s


def _make_body(project_id: str = "test-project") -> Any:
    b = MagicMock()
    b.projectId = project_id
    return b


def _make_mock_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    pool.execute.return_value = None
    return pool


def _start_fixer_patches(settings, mock_req_state, mock_bkd_cls):
    """Return a list of patch contexts for start_fixer tests."""
    return [
        patch("orchestrator.actions._verifier.settings", settings),
        patch("orchestrator.actions._verifier.req_state", mock_req_state),
        patch("orchestrator.actions._verifier.BKDClient", mock_bkd_cls),
        patch("orchestrator.store.db.get_pool", return_value=_make_mock_pool()),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: Default cap value  FRC-S1
# ─────────────────────────────────────────────────────────────────────────────


class TestDefaultCapValueFRCS1:
    """Spec: Settings default fixer_round_cap MUST be 2."""

    def test_frc_s1_default_cap_is_2(self):
        """
        FRC-S1: A fresh Settings() instance with no environment overrides
        MUST have fixer_round_cap == 2.
        """
        from orchestrator.config import Settings

        settings = Settings()
        assert settings.fixer_round_cap == 2, (
            f"Settings.fixer_round_cap default must be 2; got {settings.fixer_round_cap!r}. "
            f"This is the core behavioral change of REQ-fixer-cap-5-to-2-1777420814."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: Escalation at cap with default value  FRC-S2, FRC-S4
# ─────────────────────────────────────────────────────────────────────────────


class TestEscalationAtDefaultCapFRCS2S4:
    """
    Spec: start_fixer MUST escalate when fixer_round reaches the default cap.
    FRC-S2 and FRC-S4 describe the same behavior: default cap=2 + round=2 → escalate.
    """

    async def test_frc_s2_s4_default_cap_2_round_2_escalates(self):
        """
        FRC-S2 / FRC-S4: ctx.fixer_round=2, default cap=2 →
        start_fixer MUST return escalate emit and MUST NOT create a BKD issue.
        ctx.fixer_round_cap_hit MUST equal 2.
        """
        from orchestrator.actions._verifier import start_fixer

        ctx = {"fixer_round": 2}
        settings = _make_settings(2)
        created_issues: list = []

        mock_bkd_inst = AsyncMock()
        mock_bkd_inst.create_issue.side_effect = lambda *a, **kw: (
            created_issues.append(True) or {"data": {"id": "x"}}
        )
        mock_bkd_cls = MagicMock(return_value=mock_bkd_inst)
        mock_req_state = AsyncMock()

        written_ctx: dict = {}

        async def _capture_ctx(*args, **updates):
            written_ctx.update(updates)
            for a in args:
                if isinstance(a, dict):
                    written_ctx.update(a)

        mock_req_state.update_context.side_effect = _capture_ctx

        patches = _start_fixer_patches(settings, mock_req_state, mock_bkd_cls)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await start_fixer(
                body=_make_body(),
                req_id="REQ-test",
                tags=[],
                ctx=ctx,
            )

        assert isinstance(result, dict), (
            f"start_fixer must return dict when cap hit; got {type(result)}"
        )
        assert result.get("emit") == "verify.escalate", (
            f"Must escalate when cap=2 and round=2; got emit={result.get('emit')!r}"
        )
        assert result.get("reason") == "fixer-round-cap", (
            f"reason must be 'fixer-round-cap'; got {result.get('reason')!r}"
        )
        assert len(created_issues) == 0, (
            "BKD create_issue must not be called when default cap is hit"
        )
        assert written_ctx.get("escalated_reason") == "fixer-round-cap", (
            f"ctx.escalated_reason must be 'fixer-round-cap'; got: {written_ctx!r}"
        )
        assert written_ctx.get("fixer_round_cap_hit") == 2, (
            f"ctx.fixer_round_cap_hit must be 2; got: {written_ctx!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: Cap is configurable (isolation from default changes)  FRC-S3
# ─────────────────────────────────────────────────────────────────────────────


class TestCapConfigurableFRCS3:
    """
    Spec: The fixer round cap MUST be configurable via settings,
    so that tests (or operators) can override the default without
    changing the default value itself.
    """

    async def test_frc_s3_explicit_cap_5_allows_round_5(self):
        """
        FRC-S3: When cap is explicitly set to 5 (e.g. via monkeypatch),
        round=4 MUST NOT trigger escalation — the cap value is read from
        settings, not hard-coded to the default.
        """
        from orchestrator.actions._verifier import start_fixer

        ctx = {"fixer_round": 4}
        settings = _make_settings(5)
        created_issues: list = []

        class _FakeIssue:
            id = "fixer-issue"

        mock_bkd_inner = AsyncMock()
        async def _fake_create(*a, **kw):
            created_issues.append(True)
            return _FakeIssue()
        mock_bkd_inner.create_issue.side_effect = _fake_create
        mock_bkd_inst = AsyncMock()
        mock_bkd_inst.__aenter__ = AsyncMock(return_value=mock_bkd_inner)
        mock_bkd_cls = MagicMock(return_value=mock_bkd_inst)
        mock_req_state = AsyncMock()

        patches = _start_fixer_patches(settings, mock_req_state, mock_bkd_cls)
        with patches[0], patches[1], patches[2], patches[3]:
            await start_fixer(
                body=_make_body(),
                req_id="REQ-test",
                tags=[],
                ctx=ctx,
            )

        # With cap=5 and round=4, a fixer issue SHOULD be created
        # (not escalate yet). The exact return shape may vary, but
        # the key contract is: create_issue MUST be called.
        assert len(created_issues) == 1, (
            f"FRC-S3: With cap=5 and round=4, start_fixer MUST create a fixer issue "
            f"(cap is configurable, not hard-coded to default 2); "
            f"create_issue called {len(created_issues)} time(s)"
        )

    async def test_frc_s3_explicit_cap_5_round_5_escalates(self):
        """
        FRC-S3: When cap is explicitly set to 5, round=5 MUST trigger escalation.
        This validates that the configurable cap is enforced at its overridden value.
        """
        from orchestrator.actions._verifier import start_fixer

        ctx = {"fixer_round": 5}
        settings = _make_settings(5)
        created_issues: list = []

        mock_bkd_inst = AsyncMock()
        mock_bkd_inst.create_issue.side_effect = lambda *a, **kw: (
            created_issues.append(True) or {"data": {"id": "x"}}
        )
        mock_bkd_cls = MagicMock(return_value=mock_bkd_inst)
        mock_req_state = AsyncMock()

        patches = _start_fixer_patches(settings, mock_req_state, mock_bkd_cls)
        with patches[0], patches[1], patches[2], patches[3]:
            result = await start_fixer(
                body=_make_body(),
                req_id="REQ-test",
                tags=[],
                ctx=ctx,
            )

        assert isinstance(result, dict), (
            f"start_fixer must return dict when cap hit; got {type(result)}"
        )
        assert result.get("emit") == "verify.escalate", (
            f"Must escalate when cap=5 and round=5; got emit={result.get('emit')!r}"
        )
        assert len(created_issues) == 0, (
            "BKD create_issue must not be called when overridden cap=5 is hit"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: Observability SQL contracts  FRC-S5
# ─────────────────────────────────────────────────────────────────────────────


class TestObservabilitySQLFRCS5:
    """Spec: Q19 SQL MUST expose fixer round distribution with escalate rates."""

    def test_frc_s5_q19_sql_exists(self):
        """FRC-S5: Q19 SQL file must exist."""
        repo_root = Path(__file__).resolve().parents[2]
        q19_path = repo_root / "observability" / "queries" / "sisyphus" / "19-fixer-round-distribution.sql"
        assert q19_path.exists(), (
            f"Q19 SQL must exist at {q19_path}; "
            f"observability query is required for data-driven cap evaluation"
        )

    def test_frc_s5_q19_sql_contains_required_columns(self):
        """
        FRC-S5: Q19 SQL must contain the required output columns:
        fixer_round, n_reqs, pct, n_escalated, escalate_rate.
        """
        repo_root = Path(__file__).resolve().parents[2]
        q19_path = repo_root / "observability" / "queries" / "sisyphus" / "19-fixer-round-distribution.sql"
        if not q19_path.exists():
            pytest.skip("Q19 SQL file missing")

        sql = q19_path.read_text()
        required = ["fixer_round", "n_reqs", "pct", "n_escalated", "escalate_rate"]
        for col in required:
            assert col in sql, (
                f"Q19 SQL must contain column '{col}'; missing from {q19_path}"
            )

    def test_frc_s5_q20_sql_exists(self):
        """FRC-S5 (complementary): Q20 SQL file must exist."""
        repo_root = Path(__file__).resolve().parents[2]
        q20_path = repo_root / "observability" / "queries" / "sisyphus" / "20-fixer-decision-distribution-by-cap.sql"
        assert q20_path.exists(), (
            f"Q20 SQL must exist at {q20_path}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 5: Documentation contracts  FRC-S6
# ─────────────────────────────────────────────────────────────────────────────


class TestDocumentationFRCS6:
    """
    Spec: docs/IMPACT-REPORT.md and docs/user-feedback-loop.md MUST
    describe a cap of 2 rounds, not 5.
    """

    def test_frc_s6_impact_report_cap_is_2(self):
        """
        FRC-S6: IMPACT-REPORT.md must describe the fixer round cap as 2.
        We check that the file contains a reference to '2 轮' or '2轮'
        in the fixer cap context.
        """
        repo_root = Path(__file__).resolve().parents[2]
        doc_path = repo_root / "docs" / "IMPACT-REPORT.md"
        if not doc_path.exists():
            pytest.skip("IMPACT-REPORT.md not found")

        text = doc_path.read_text()
        # The document should reference the cap in the context of fixer rounds.
        assert "硬上限 2 轮" in text or "硬上限2轮" in text or "cap 2 轮" in text or "上限 2 轮" in text, (
            f"FRC-S6: {doc_path} must describe the fixer round cap as 2 rounds; "
            f"expected a phrase like '硬上限 2 轮'"
        )

    def test_frc_s6_user_feedback_loop_cap_is_2(self):
        """
        FRC-S6: user-feedback-loop.md must reference the fixer cap as 2.
        """
        repo_root = Path(__file__).resolve().parents[2]
        doc_path = repo_root / "docs" / "user-feedback-loop.md"
        if not doc_path.exists():
            pytest.skip("user-feedback-loop.md not found")

        text = doc_path.read_text()
        assert "fixer cap 2 轮" in text or "cap 2 轮" in text or "上限 2" in text, (
            f"FRC-S6: {doc_path} must reference the fixer cap as 2 rounds; "
            f"expected a phrase like 'fixer cap 2 轮'"
        )

    def test_frc_s6_no_stale_cap_5_reference(self):
        """
        FRC-S6: Neither IMPACT-REPORT.md nor user-feedback-loop.md should
        contain a stale reference to '5 轮' as the fixer cap default.
        """
        repo_root = Path(__file__).resolve().parents[2]
        stale_patterns = ["硬上限 5 轮", "cap 5 轮", "上限 5 轮"]

        for doc_name in ("IMPACT-REPORT.md", "user-feedback-loop.md"):
            doc_path = repo_root / "docs" / doc_name
            if not doc_path.exists():
                continue
            text = doc_path.read_text()
            for pattern in stale_patterns:
                assert pattern not in text, (
                    f"FRC-S6: {doc_path} contains stale reference '{pattern}'; "
                    f"default cap is 2, not 5"
                )
