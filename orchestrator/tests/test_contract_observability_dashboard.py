"""Contract tests for REQ-observability-metabase-1777189271.

Black-box behavioral contracts derived from:
  openspec/changes/REQ-observability-metabase-1777189271/specs/observability-dashboard/spec.md

Scenarios covered:
  ODB-S1   Q1 stuck-checks SQL exists and reads artifact_checks
  ODB-S2   Q2 check-duration-anomaly SQL exists and reads artifact_checks
  ODB-S3   Q3 stage-success-rate SQL exists and reads artifact_checks
  ODB-S4   Q4 fail-kind-distribution SQL exists and reads artifact_checks
  ODB-S5   Q5 active-req-overview SQL joins artifact_checks with req_state
  ODB-S6   Q6 weekly stage success rate reads stage_runs
  ODB-S7   Q7 stage duration percentiles reads stage_runs
  ODB-S8   Q8 verifier decision accuracy reads verifier_decisions
  ODB-S9   Q9 fix success rate reads verifier_decisions
  ODB-S10  Q10 token cost reads stage_runs
  ODB-S11  Q11 parallel dev speedup reads stage_runs
  ODB-S12  Q12 bugfix loop anomaly reads stage_runs
  ODB-S13  Q13 watchdog escalate frequency joins stage_runs and verifier_decisions
  ODB-S14  dashboard md links every SQL file by relative path
  ODB-S15  dashboard md states the M7 (5) + M14e (8) split in overview prose
  ODB-S16  dashboard md publishes refresh frequency for every question

Testing strategy:
  All scenarios are pure file-system assertions on the checked-in observability
  artefacts. No service is started. Tests verify file existence, non-zero size,
  and content constraints (substring presence / absence) as specified in spec.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SQL_DIR = REPO_ROOT / "observability" / "queries" / "sisyphus"
DASHBOARD_MD = REPO_ROOT / "observability" / "sisyphus-dashboard.md"


def _sql(filename: str) -> Path:
    return SQL_DIR / filename


def _read(path: Path) -> str:
    assert path.exists(), f"File not found: {path}"
    assert path.stat().st_size > 0, f"File is empty: {path}"
    return path.read_text(encoding="utf-8")


# ── ODB-S1 ───────────────────────────────────────────────────────────────────


def test_ODB_S1_q1_stuck_checks_exists_and_reads_artifact_checks():
    """Q1 stuck-checks SQL exists and primary table is artifact_checks."""
    content = _read(_sql("01-stuck-checks.sql"))
    assert "FROM artifact_checks" in content, (
        "01-stuck-checks.sql MUST contain 'FROM artifact_checks'"
    )
    for forbidden in ("FROM bkd_snapshot", "FROM event_log", "FROM stage_runs", "FROM verifier_decisions"):
        assert forbidden not in content, (
            f"01-stuck-checks.sql MUST NOT contain '{forbidden}' (M7 surface is artifact_checks only)"
        )


# ── ODB-S2 ───────────────────────────────────────────────────────────────────


def test_ODB_S2_q2_check_duration_anomaly_exists_and_reads_artifact_checks():
    """Q2 check-duration-anomaly SQL exists and primary table is artifact_checks."""
    content = _read(_sql("02-check-duration-anomaly.sql"))
    assert "FROM artifact_checks" in content, (
        "02-check-duration-anomaly.sql MUST contain 'FROM artifact_checks'"
    )
    assert "FROM stage_runs" not in content, (
        "02-check-duration-anomaly.sql MUST NOT contain 'FROM stage_runs'"
    )
    assert "FROM verifier_decisions" not in content, (
        "02-check-duration-anomaly.sql MUST NOT contain 'FROM verifier_decisions'"
    )


# ── ODB-S3 ───────────────────────────────────────────────────────────────────


def test_ODB_S3_q3_stage_success_rate_exists_and_reads_artifact_checks():
    """Q3 stage-success-rate SQL exists and primary table is artifact_checks."""
    content = _read(_sql("03-stage-success-rate.sql"))
    assert "FROM artifact_checks" in content, (
        "03-stage-success-rate.sql MUST contain 'FROM artifact_checks'"
    )


# ── ODB-S4 ───────────────────────────────────────────────────────────────────


def test_ODB_S4_q4_fail_kind_distribution_exists_and_reads_artifact_checks():
    """Q4 fail-kind-distribution SQL exists and primary table is artifact_checks."""
    content = _read(_sql("04-fail-kind-distribution.sql"))
    assert "FROM artifact_checks" in content, (
        "04-fail-kind-distribution.sql MUST contain 'FROM artifact_checks'"
    )


# ── ODB-S5 ───────────────────────────────────────────────────────────────────


def test_ODB_S5_q5_active_req_overview_joins_artifact_checks_and_req_state():
    """Q5 active-req-overview SQL references both artifact_checks and req_state."""
    content = _read(_sql("05-active-req-overview.sql"))
    assert "FROM artifact_checks" in content, (
        "05-active-req-overview.sql MUST contain 'FROM artifact_checks'"
    )
    assert "req_state" in content, (
        "05-active-req-overview.sql MUST contain 'req_state' (Q5 surfaces the live REQ state column)"
    )


# ── ODB-S6 ───────────────────────────────────────────────────────────────────


def test_ODB_S6_q6_weekly_stage_success_rate_reads_stage_runs():
    """Q6 stage-success-rate-by-week SQL exists and primary table is stage_runs."""
    content = _read(_sql("06-stage-success-rate-by-week.sql"))
    assert "FROM stage_runs" in content, (
        "06-stage-success-rate-by-week.sql MUST contain 'FROM stage_runs'"
    )


# ── ODB-S7 ───────────────────────────────────────────────────────────────────


def test_ODB_S7_q7_stage_duration_percentiles_reads_stage_runs():
    """Q7 stage-duration-percentiles SQL exists and primary table is stage_runs."""
    content = _read(_sql("07-stage-duration-percentiles.sql"))
    assert "FROM stage_runs" in content, (
        "07-stage-duration-percentiles.sql MUST contain 'FROM stage_runs'"
    )


# ── ODB-S8 ───────────────────────────────────────────────────────────────────


def test_ODB_S8_q8_verifier_decision_accuracy_reads_verifier_decisions():
    """Q8 verifier-decision-accuracy SQL exists and primary table is verifier_decisions."""
    content = _read(_sql("08-verifier-decision-accuracy.sql"))
    assert "FROM verifier_decisions" in content, (
        "08-verifier-decision-accuracy.sql MUST contain 'FROM verifier_decisions'"
    )


# ── ODB-S9 ───────────────────────────────────────────────────────────────────


def test_ODB_S9_q9_fix_success_rate_reads_verifier_decisions():
    """Q9 fix-success-rate-by-fixer SQL exists and primary table is verifier_decisions."""
    content = _read(_sql("09-fix-success-rate-by-fixer.sql"))
    assert "FROM verifier_decisions" in content, (
        "09-fix-success-rate-by-fixer.sql MUST contain 'FROM verifier_decisions'"
    )


# ── ODB-S10 ──────────────────────────────────────────────────────────────────


def test_ODB_S10_q10_token_cost_reads_stage_runs():
    """Q10 token-cost-by-req SQL exists and primary table is stage_runs."""
    content = _read(_sql("10-token-cost-by-req.sql"))
    assert "FROM stage_runs" in content, (
        "10-token-cost-by-req.sql MUST contain 'FROM stage_runs'"
    )


# ── ODB-S11 ──────────────────────────────────────────────────────────────────


def test_ODB_S11_q11_parallel_dev_speedup_reads_stage_runs():
    """Q11 parallel-dev-speedup SQL exists and primary table is stage_runs."""
    content = _read(_sql("11-parallel-dev-speedup.sql"))
    assert "FROM stage_runs" in content, (
        "11-parallel-dev-speedup.sql MUST contain 'FROM stage_runs'"
    )


# ── ODB-S12 ──────────────────────────────────────────────────────────────────


def test_ODB_S12_q12_bugfix_loop_anomaly_reads_stage_runs():
    """Q12 bugfix-loop-anomaly SQL exists and primary table is stage_runs."""
    content = _read(_sql("12-bugfix-loop-anomaly.sql"))
    assert "FROM stage_runs" in content, (
        "12-bugfix-loop-anomaly.sql MUST contain 'FROM stage_runs'"
    )


# ── ODB-S13 ──────────────────────────────────────────────────────────────────


def test_ODB_S13_q13_watchdog_escalate_frequency_joins_both_tables():
    """Q13 watchdog-escalate-frequency SQL references both stage_runs and verifier_decisions."""
    content = _read(_sql("13-watchdog-escalate-frequency.sql"))
    assert "stage_runs" in content, (
        "13-watchdog-escalate-frequency.sql MUST contain 'stage_runs'"
    )
    assert "verifier_decisions" in content, (
        "13-watchdog-escalate-frequency.sql MUST contain 'verifier_decisions' "
        "(Q13 attributes escalates by stage across both tables)"
    )


# ── ODB-S14 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("nn", ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13"])
def test_ODB_S14_dashboard_md_links_every_sql_file(nn: str):
    """Dashboard md links every SQL file by relative path (queries/sisyphus/<NN>-)."""
    content = DASHBOARD_MD.read_text(encoding="utf-8") if DASHBOARD_MD.exists() else ""
    assert DASHBOARD_MD.exists(), "observability/sisyphus-dashboard.md not found"
    pattern = f"queries/sisyphus/{nn}-"
    assert pattern in content, (
        f"sisyphus-dashboard.md MUST contain a link to 'queries/sisyphus/{nn}-…sql' "
        f"but pattern '{pattern}' was not found"
    )


# ── ODB-S15 ──────────────────────────────────────────────────────────────────


def test_ODB_S15_dashboard_md_states_5_plus_8_split():
    """Dashboard md contains '5 + 8' literal announcing the M7/M14e split."""
    content = _read(DASHBOARD_MD)
    assert "5 + 8" in content, (
        "sisyphus-dashboard.md MUST contain the literal '5 + 8' to announce the M7/M14e question split"
    )


def test_ODB_S15_dashboard_md_mentions_M7_and_M14e():
    """Dashboard md explicitly names both M7 and M14e cohorts."""
    content = _read(DASHBOARD_MD)
    assert "M7" in content, "sisyphus-dashboard.md MUST contain 'M7'"
    assert "M14e" in content, "sisyphus-dashboard.md MUST contain 'M14e'"


def test_ODB_S15_dashboard_md_names_both_source_tables():
    """Dashboard md names artifact_checks and at least one of stage_runs/verifier_decisions."""
    content = _read(DASHBOARD_MD)
    assert "artifact_checks" in content, (
        "sisyphus-dashboard.md MUST contain 'artifact_checks' (M7 source)"
    )
    has_m14e_source = "stage_runs" in content or "verifier_decisions" in content
    assert has_m14e_source, (
        "sisyphus-dashboard.md MUST contain 'stage_runs' or 'verifier_decisions' (M14e source)"
    )


# ── ODB-S16 ──────────────────────────────────────────────────────────────────


def test_ODB_S16_dashboard_md_has_refresh_frequency_heading():
    """Dashboard md has a '## 刷新频率' section header."""
    content = _read(DASHBOARD_MD)
    assert "## 刷新频率" in content, (
        "sisyphus-dashboard.md MUST contain a heading '## 刷新频率' (canonical refresh-section header)"
    )


@pytest.mark.parametrize("q_label", ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9", "Q10", "Q11", "Q12", "Q13"])
def test_ODB_S16_refresh_section_mentions_every_question(q_label: str):
    """Every Q1–Q13 appears after the '## 刷新频率' heading."""
    content = _read(DASHBOARD_MD)
    refresh_marker = "## 刷新频率"
    assert refresh_marker in content, "No '## 刷新频率' section found in dashboard md"
    refresh_section = content[content.index(refresh_marker):]
    assert q_label in refresh_section, (
        f"sisyphus-dashboard.md refresh section MUST mention '{q_label}' "
        f"(every question's cadence must be documented)"
    )
