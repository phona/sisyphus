"""
Contract tests for REQ-fixer-audit-v2-1777009056: fixer audit observability.

Black-box behavioral contract verification written by challenger-agent.
Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

_ORCHESTRATOR_ROOT = Path(__file__).parent.parent
_PROMPTS_DIR = _ORCHESTRATOR_ROOT / "src" / "orchestrator" / "prompts"
_MIGRATIONS_DIR = _ORCHESTRATOR_ROOT / "migrations"
_OBSERVABILITY_DIR = _ORCHESTRATOR_ROOT.parent / "observability"
_SISYPHUS_Q = _OBSERVABILITY_DIR / "queries" / "sisyphus"

LEGAL_VERDICTS = ["legitimate", "test-hack", "code-lobotomy", "spec-drift", "unclear"]

SUCCESS_TEMPLATES = [
    "accept_success.md.j2",
    "analyze_success.md.j2",
    "challenger_success.md.j2",
    "dev_cross_check_success.md.j2",
    "pr_ci_success.md.j2",
    "spec_lint_success.md.j2",
    "staging_test_success.md.j2",
]


def _valid_audit(verdict: str = "legitimate") -> dict:
    return {
        "diff_summary": "src=+12/-3 tests=+8/-0",
        "verdict": verdict,
        "red_flags": [],
        "files_by_category": {"src": 12, "tests": 8, "spec": 0, "config": 0},
    }


# ─── Contract 1: validate_audit_soft ─────────────────────────────────────────


class TestValidateAuditSoftContract:
    """Spec: router.py expose validate_audit_soft(audit: dict | None) -> str | None.
    Soft = log.warning only, never raise, never alter pass/fix/escalate routing."""

    def test_function_exists_in_router(self):
        from orchestrator.router import validate_audit_soft

        assert callable(validate_audit_soft)

    def test_none_input_returns_none(self):
        """Spec: first-time verifier (no history) → audit=None → returns None."""
        from orchestrator.router import validate_audit_soft

        assert validate_audit_soft(None) is None

    @pytest.mark.parametrize("verdict", LEGAL_VERDICTS)
    def test_all_five_verdicts_are_valid(self, verdict):
        """Spec: five legal verdict values must all pass soft validation."""
        from orchestrator.router import validate_audit_soft

        result = validate_audit_soft(_valid_audit(verdict))
        assert result is None, f"verdict={verdict!r} must be valid; got: {result!r}"

    def test_invalid_verdict_returns_str_not_none(self):
        """Spec: illegal verdict → log.warning + return error str, not None, not raise."""
        from orchestrator.router import validate_audit_soft

        result = validate_audit_soft(_valid_audit("not-a-real-verdict-xyz"))
        assert isinstance(result, str), (
            "invalid verdict must return str error message, not None"
        )

    def test_non_dict_files_by_category_returns_str(self):
        """Spec: files_by_category non-dict → log.warning + return str."""
        from orchestrator.router import validate_audit_soft

        audit = _valid_audit()
        audit["files_by_category"] = ["src", "tests"]  # list instead of dict
        result = validate_audit_soft(audit)
        assert isinstance(result, str), "non-dict files_by_category must return error str"

    def test_missing_required_fields_returns_str(self):
        """Spec: audit with missing fields → log.warning + return str."""
        from orchestrator.router import validate_audit_soft

        result = validate_audit_soft({"verdict": "legitimate"})  # missing 3 fields
        assert isinstance(result, str), "incomplete audit must return error str"

    def test_never_raises_for_any_input(self):
        """Spec: soft validation must never raise — only return str or None."""
        from orchestrator.router import validate_audit_soft

        bad_inputs = [
            None,
            {},
            {"verdict": "bad"},
            {"files_by_category": 99},
            {"verdict": None, "files_by_category": None, "red_flags": None},
        ]
        for bad in bad_inputs:
            try:
                validate_audit_soft(bad)
            except Exception as exc:
                pytest.fail(
                    f"validate_audit_soft must not raise; raised {exc!r} for input {bad!r}"
                )

    def test_validate_decision_accepts_5field_decision_without_audit(self):
        """Spec: validate_decision must not be affected by audit presence/absence."""
        from orchestrator.router import validate_decision

        # Original 5-field decision shape — no audit
        decision = {
            "action": "pass",
            "fixer": None,
            "scope": None,
            "reason": "all tests pass",
            "confidence": "high",
        }
        ok, reason = validate_decision(decision)
        assert ok, f"validate_decision must accept 5-field decision without audit; reason={reason!r}"


# ─── Contract 2: Template structure ──────────────────────────────────────────


class TestAuditTemplateContract:
    """Spec: _audit.md.j2 exists; all 7 *_success.md.j2 include it guarded by history."""

    def test_audit_partial_exists(self):
        assert (_PROMPTS_DIR / "verifier" / "_audit.md.j2").exists(), (
            "prompts/verifier/_audit.md.j2 must exist"
        )

    def test_audit_partial_references_all_five_fields(self):
        """Spec: audit JSON has 5 named fields; template must reference each."""
        content = (_PROMPTS_DIR / "verifier" / "_audit.md.j2").read_text()
        for field in ["diff_summary", "verdict", "red_flags", "files_by_category"]:
            assert field in content, f"_audit.md.j2 must reference field: {field}"

    def test_audit_partial_documents_all_five_verdict_values(self):
        """Spec: verdict enum has exactly 5 legal values; template must list them all."""
        content = (_PROMPTS_DIR / "verifier" / "_audit.md.j2").read_text()
        for v in LEGAL_VERDICTS:
            assert v in content, f"_audit.md.j2 must document verdict value: {v}"

    @pytest.mark.parametrize("tpl", SUCCESS_TEMPLATES)
    def test_success_template_includes_audit_partial(self, tpl):
        """Spec: all 7 *_success.md.j2 must include _audit.md.j2."""
        path = _PROMPTS_DIR / "verifier" / tpl
        assert path.exists(), f"Template not found: {tpl}"
        content = path.read_text()
        assert "_audit.md.j2" in content, (
            f"{tpl} must include _audit.md.j2 per spec"
        )

    @pytest.mark.parametrize("tpl", SUCCESS_TEMPLATES)
    def test_success_template_guards_audit_with_history_check(self, tpl):
        """Spec: inclusion wrapped in {% if history %}...{% endif %}."""
        content = (_PROMPTS_DIR / "verifier" / tpl).read_text()
        assert "history" in content, (
            f"{tpl} must reference 'history' variable to guard the audit include"
        )

    def test_audit_absent_in_rendered_output_when_history_empty(self):
        """Spec: history=[] → audit section MUST NOT appear in rendered template."""
        from orchestrator.prompts import render

        rendered = render(
            "verifier/analyze_success.md.j2",
            req_id="REQ-test-contract",
            stage="analyze",
            history=[],
            result="",
            stage_output="",
        )
        assert "diff_summary" not in rendered, (
            "audit field 'diff_summary' must not appear when history=[]"
        )
        assert "files_by_category" not in rendered, (
            "audit field 'files_by_category' must not appear when history=[]"
        )

    def test_audit_present_in_rendered_output_when_history_nonempty(self):
        """Spec: history=[...] → audit section MUST appear in rendered template."""
        from orchestrator.prompts import render

        rendered = render(
            "verifier/analyze_success.md.j2",
            req_id="REQ-test-contract",
            stage="analyze",
            history=["prior fix attempt"],
            result="",
            stage_output="",
        )
        assert "diff_summary" in rendered, (
            "audit field 'diff_summary' must appear when history is non-empty"
        )


# ─── Contract 3: Migration 0006 ───────────────────────────────────────────────


class TestMigration0006Contract:
    """Spec: 0006_add_verifier_audit adds JSONB audit column; rollback drops it."""

    def test_forward_migration_file_exists(self):
        assert (_MIGRATIONS_DIR / "0006_add_verifier_audit.sql").exists()

    def test_rollback_migration_file_exists(self):
        assert (_MIGRATIONS_DIR / "0006_add_verifier_audit.rollback.sql").exists()

    def test_forward_adds_audit_jsonb_to_verifier_decisions(self):
        sql = (_MIGRATIONS_DIR / "0006_add_verifier_audit.sql").read_text().upper()
        assert "ADD COLUMN" in sql, "forward migration must ADD COLUMN"
        assert "AUDIT" in sql, "forward migration must add column named audit"
        assert "JSONB" in sql, "forward migration must specify JSONB type"
        assert "VERIFIER_DECISIONS" in sql, "forward migration must target verifier_decisions"

    def test_rollback_drops_audit_column(self):
        sql = (_MIGRATIONS_DIR / "0006_add_verifier_audit.rollback.sql").read_text().upper()
        assert "DROP COLUMN" in sql, "rollback must DROP COLUMN"
        assert "AUDIT" in sql, "rollback must reference column named audit"

    def test_migration_is_numerically_after_0005(self):
        """Migration ordering must be monotonically increasing."""
        assert (_MIGRATIONS_DIR / "0005_verifier_decisions.sql").exists(), (
            "0005 must exist before 0006 can be applied"
        )
        assert (_MIGRATIONS_DIR / "0006_add_verifier_audit.sql").exists()


# ─── Contract 4: Observability SQL (Q14/Q15/Q16) ─────────────────────────────


class TestObservabilitySQLContract:
    """Spec: 3 new Metabase SQL files for fixer audit observability."""

    def test_q14_file_exists(self):
        assert (_SISYPHUS_Q / "14-fixer-audit-verdict-trend.sql").exists()

    def test_q15_file_exists(self):
        assert (_SISYPHUS_Q / "15-suspicious-pass-decisions.sql").exists()

    def test_q16_file_exists(self):
        assert (_SISYPHUS_Q / "16-fixer-file-category-breakdown.sql").exists()

    def test_q14_queries_audit_verdict_from_verifier_decisions(self):
        """Q14 answers: how does fixer change quality trend over time?"""
        sql = (_SISYPHUS_Q / "14-fixer-audit-verdict-trend.sql").read_text().lower()
        assert "verifier_decisions" in sql
        assert "audit" in sql
        assert "verdict" in sql
        assert "audit is not null" in sql, "Q14 must filter WHERE audit IS NOT NULL"

    def test_q15_surfaces_suspicious_pass_decisions(self):
        """Q15 answers: which passes have suspicious audit verdict?"""
        sql = (_SISYPHUS_Q / "15-suspicious-pass-decisions.sql").read_text().lower()
        assert "decision_action" in sql
        assert "pass" in sql
        assert "verdict" in sql
        assert "legitimate" in sql, "Q15 must exclude legitimate verdicts"

    def test_q16_aggregates_files_by_category_over_time(self):
        """Q16 answers: what file categories are fixer changing per week?"""
        sql = (_SISYPHUS_Q / "16-fixer-file-category-breakdown.sql").read_text().lower()
        assert "files_by_category" in sql, "Q16 must reference files_by_category JSONB field"
        assert "verifier_decisions" in sql


# ─── Contract 5: insert_decision audit parameter ─────────────────────────────


class TestInsertDecisionAuditContract:
    """Spec: insert_decision must accept audit: dict | None = None keyword arg."""

    def test_insert_decision_has_audit_keyword_param(self):
        from orchestrator.store.verifier_decisions import insert_decision

        sig = inspect.signature(insert_decision)
        assert "audit" in sig.parameters, (
            "insert_decision must accept 'audit' as a keyword parameter"
        )

    def test_audit_param_defaults_to_none(self):
        """Spec: audit=None default ensures backward compat with old callers."""
        from orchestrator.store.verifier_decisions import insert_decision

        sig = inspect.signature(insert_decision)
        p = sig.parameters["audit"]
        assert p.default is None, "audit must default to None for backward compat"


# ─── Contract 6: Prompt cleanup — no M15 remnants ────────────────────────────


class TestPromptCleanupContract:
    """Spec: bugfix.md.j2 and done_archive.md.j2 cleaned of M15 remnants."""

    def test_bugfix_no_deprecated_mcp_bkd_get_issue(self):
        """Spec: mcp__bkd__get-issue removed, replaced by curl BKD REST."""
        content = (_PROMPTS_DIR / "bugfix.md.j2").read_text()
        assert "mcp__bkd__get-issue" not in content, (
            "bugfix.md.j2 must not reference deprecated mcp__bkd__get-issue"
        )

    def test_bugfix_no_deprecated_stage_bugfix_branch_convention(self):
        """Spec: BRANCH_WORK=stage/bugfix-... old convention removed."""
        content = (_PROMPTS_DIR / "bugfix.md.j2").read_text()
        assert "stage/bugfix" not in content, (
            "bugfix.md.j2 must not reference deprecated stage/bugfix branch convention"
        )

    def test_bugfix_has_audit_warning_for_dev(self):
        """Spec: bugfix.md.j2 must warn dev that fix will be verifier diff-audited."""
        content = (_PROMPTS_DIR / "bugfix.md.j2").read_text()
        assert "audit" in content.lower(), (
            "bugfix.md.j2 must contain audit notification section for dev"
        )

    def test_done_archive_no_mcp_bkd_tools(self):
        """Spec: mcp__bkd__* → curl BKD REST in done_archive."""
        content = (_PROMPTS_DIR / "done_archive.md.j2").read_text()
        assert "mcp__bkd__" not in content, (
            "done_archive.md.j2 must not reference deprecated mcp__bkd__* tools"
        )

    def test_done_archive_uses_gh_pr_list_for_pr_discovery(self):
        """Spec: done_archive uses gh pr list --head feat/{req_id} for each source repo."""
        content = (_PROMPTS_DIR / "done_archive.md.j2").read_text()
        assert "gh pr list" in content, (
            "done_archive.md.j2 must use 'gh pr list' for PR discovery"
        )
