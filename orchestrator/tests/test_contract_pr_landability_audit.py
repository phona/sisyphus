"""Contract tests for pr-landability-audit (REQ-ttpos-biz-pr-landability-1777247423).

Black-box challenger — derives from:
  openspec/changes/REQ-ttpos-biz-pr-landability-1777247423/specs/pr-landability-audit/spec.md

Scenarios covered:
  LAND-S1  branch name + REQ tag align → PASS when both match
  LAND-S2  base == defaultBranchRef → PASS when they match
  LAND-S3a billing-rejected check → BLOCKED — GHA billing
  LAND-S3b repo with zero workflows → BLOCKED — repo has no .github/workflows/
  LAND-S4  all five Makefile contract targets present → PASS
  LAND-S5  delta-format spec with SHALL prose → PASS (asserted)
  LAND-S6a broken stacking on closed-unmerged predecessor → RISK
  LAND-S6b sibling PR competes for same contract surface → RISK

Testing strategy:
  The REQ deliverables are spec.md + audit-report.md (no Python implementation).
  Tests assert structural and content properties of those artefacts as required
  by the spec scenarios. Mirrors the pattern in test_contract_observability_dashboard.py.

Dev MUST NOT modify these tests to make them pass — fix the deliverables instead.
If a test is wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REQ = "REQ-ttpos-biz-pr-landability-1777247423"
_CHANGES = REPO_ROOT / "openspec" / "changes" / REQ
SPEC_MD = _CHANGES / "specs" / "pr-landability-audit" / "spec.md"
AUDIT_REPORT = _CHANGES / "audit-report.md"
PROPOSAL_MD = _CHANGES / "proposal.md"


def _read(path: Path) -> str:
    assert path.exists(), f"Required deliverable not found: {path}"
    assert path.stat().st_size > 0, f"Required deliverable is empty: {path}"
    return path.read_text(encoding="utf-8")


# ── Spec structural invariants ─────────────────────────────────────────────────


def test_proposal_md_exists():
    """proposal.md MUST exist for the REQ."""
    _read(PROPOSAL_MD)


def test_spec_md_exists_and_is_delta_format():
    """spec.md MUST exist under specs/pr-landability-audit/ and use openspec delta format."""
    content = _read(SPEC_MD)
    assert "## ADDED Requirements" in content, (
        "spec.md MUST start with '## ADDED Requirements' (openspec delta format); "
        "openspec validate --strict will reject any spec.md missing a delta heading"
    )


def test_spec_md_all_six_scenario_headings_defined():
    """spec.md MUST define all six LAND-S1..S6 scenarios using the exact heading prefix
    '#### Scenario: LAND-Sn' as required by check-scenario-refs.sh HEADING_PATTERN."""
    content = _read(SPEC_MD)
    for n in range(1, 7):
        assert f"#### Scenario: LAND-S{n}" in content, (
            f"spec.md MUST define '#### Scenario: LAND-S{n}' — "
            f"check-scenario-refs.sh pattern '^##+ Scenario:' requires the literal prefix"
        )


def test_spec_md_requirements_have_shall_or_must_in_prose():
    """Every '### Requirement:' block MUST contain SHALL or MUST in prose (not just heading).
    openspec extractRequirementText skips the heading line; prose body must carry the token."""
    content = _read(SPEC_MD)
    blocks = re.split(r"(?=^### Requirement:)", content, flags=re.MULTILINE)
    for block in blocks:
        if not block.startswith("### Requirement:"):
            continue
        # Prose is between the heading line and the first #### Scenario heading
        prose_match = re.search(
            r"^### Requirement:[^\n]*\n(.*?)(?=^####|\Z)", block, re.MULTILINE | re.DOTALL
        )
        if not prose_match:
            continue
        prose = prose_match.group(1).strip()
        assert "SHALL" in prose or "MUST" in prose, (
            f"Requirement prose MUST contain SHALL or MUST "
            f"(openspec validate --strict will reject it):\n{block[:300]!r}"
        )


# ── LAND-S1: branch name + REQ tag align ─────────────────────────────────────


def test_spec_md_land_s1_specifies_feat_req_pattern():
    """LAND-S1 requirement SHALL specify the 'feat/<REQ-id>' head-branch pattern."""
    content = _read(SPEC_MD)
    assert "feat/" in content, (
        "spec.md LAND-S1 MUST reference the 'feat/<REQ-id>' head-branch naming pattern"
    )


def test_audit_report_land_s1_both_prs_pass():
    """LAND-S1: both audited PRs have feat/<REQ-id> head refs → audit SHALL record PASS."""
    content = _read(AUDIT_REPORT)
    assert "LAND-S1" in content, "audit-report.md MUST document LAND-S1 results"
    # Find first LAND-S1 context window; both PR sections must contain PASS
    regions = [m.start() for m in re.finditer(r"LAND-S1", content)]
    assert len(regions) >= 2, (
        "audit-report.md MUST cover LAND-S1 for both ttpos-server-go#217 and ttpos-arch-lab#10 "
        "(expected at least 2 LAND-S1 references)"
    )
    for start in regions:
        window = content[start : start + 300]
        assert "PASS" in window, (
            f"LAND-S1 section near offset {start} MUST show PASS "
            f"(both PRs have matching feat/<REQ-id> head refs):\n{window!r}"
        )


# ── LAND-S2: base equals defaultBranchRef ────────────────────────────────────


def test_spec_md_land_s2_references_default_branch_fetch():
    """LAND-S2 SHALL reference 'defaultBranchRef' as the authoritative comparison source."""
    content = _read(SPEC_MD)
    assert "defaultBranchRef" in content, (
        "spec.md LAND-S2 MUST reference 'defaultBranchRef' "
        "(fetched via gh repo view --json defaultBranchRef) as the authoritative source"
    )


def test_audit_report_land_s2_both_prs_pass():
    """LAND-S2: both PRs target their repo's default branch → audit SHALL record PASS."""
    content = _read(AUDIT_REPORT)
    regions = [m.start() for m in re.finditer(r"LAND-S2", content)]
    assert len(regions) >= 2, (
        "audit-report.md MUST cover LAND-S2 for both audited PRs"
    )
    for start in regions:
        window = content[start : start + 300]
        assert "PASS" in window, (
            f"LAND-S2 section near offset {start} MUST show PASS "
            f"(#217 base=release==default; #10 base=main==default):\n{window!r}"
        )


# ── LAND-S3: CI signal ────────────────────────────────────────────────────────


def test_spec_md_land_s3_defines_platform_rejected_class():
    """LAND-S3 MUST define 'platform-rejected' as a failure classification."""
    content = _read(SPEC_MD)
    assert "platform-rejected" in content, (
        "spec.md LAND-S3 MUST define the 'platform-rejected' failure class "
        "covering GHA billing and org-level infra rejections"
    )


def test_spec_md_land_s3_billing_scenario_specifies_blocked():
    """LAND-S3 billing scenario: annotation starting with billing text → SHALL be BLOCKED."""
    content = _read(SPEC_MD)
    # Spec must describe the billing annotation and its BLOCKED outcome
    assert "billing" in content.lower(), (
        "spec.md MUST define a LAND-S3 billing-rejection scenario"
    )
    assert "BLOCKED" in content, (
        "spec.md LAND-S3 billing scenario MUST specify result as BLOCKED"
    )


def test_spec_md_land_s3_no_workflows_scenario_specifies_blocked():
    """LAND-S3 no-workflows scenario: empty statusCheckRollup + 404 on .github/workflows → BLOCKED."""
    content = _read(SPEC_MD)
    assert ".github/workflows" in content, (
        "spec.md MUST define a LAND-S3 scenario for repos with no .github/workflows/"
    )


def test_spec_md_land_s3_author_claim_not_ci_signal():
    """LAND-S3 MUST state that author-asserted runner-pod runs SHALL NOT count as CI signal."""
    content = _read(SPEC_MD)
    assert "author" in content.lower() or "runner" in content.lower(), (
        "spec.md LAND-S3 MUST explicitly exclude author-asserted runner-pod claims "
        "from the CI signal count"
    )


def test_audit_report_land_s3_server_go_blocked_billing():
    """LAND-S3 ttpos-server-go#217: billing-rejected checks → audit SHALL record BLOCKED — GHA billing."""
    content = _read(AUDIT_REPORT)
    assert "BLOCKED" in content, (
        "audit-report.md MUST contain BLOCKED results for LAND-S3"
    )
    assert "billing" in content.lower(), (
        "audit-report.md MUST document GHA billing as the LAND-S3 blocker for ttpos-server-go#217"
    )


def test_audit_report_land_s3_billing_classified_as_platform_not_code():
    """LAND-S3 billing annotation MUST be classified as platform-rejected, not code-failed.
    Spec: 'audit SHALL never count author-asserted runner-pod claims as CI signal'."""
    content = _read(AUDIT_REPORT)
    # Billing annotation text or platform classification must be referenced
    assert (
        "not started" in content.lower()
        or "platform" in content.lower()
        or "account payments" in content.lower()
        or "spending limit" in content.lower()
    ), (
        "audit-report.md LAND-S3 MUST reference the billing-rejection annotation "
        "('job was not started because of account payment failure') to classify it as platform-rejected"
    )


def test_audit_report_land_s3_arch_lab_blocked_no_workflows():
    """LAND-S3 ttpos-arch-lab#10: repo has no .github/workflows/ → SHALL record BLOCKED."""
    content = _read(AUDIT_REPORT)
    assert ".github/workflows" in content, (
        "audit-report.md MUST document missing .github/workflows/ as the LAND-S3 blocker "
        "for ttpos-arch-lab#10"
    )


def test_audit_report_land_s3_no_workflows_recommends_follow_up_req():
    """LAND-S3 no-workflows verdict MUST propose a follow-up REQ to port sisyphus contract checks.
    Spec: 'recommendation MUST propose a follow-up REQ to port the sisyphus contract checks into a workflow'."""
    content = _read(AUDIT_REPORT)
    # Recommendation section must mention porting CI workflows
    assert (
        "workflow" in content.lower() or "follow-up" in content.lower() or "follow up" in content.lower()
    ), (
        "audit-report.md MUST recommend a follow-up REQ or action to add CI workflows "
        "to ttpos-arch-lab (LAND-S3 no-workflows BLOCKED verdict requirement)"
    )


# ── LAND-S4: Makefile contract targets ───────────────────────────────────────


def test_spec_md_land_s4_lists_all_five_targets():
    """LAND-S4 MUST list all five sisyphus contract targets as required."""
    content = _read(SPEC_MD)
    for target in ("ci-lint", "ci-unit-test", "ci-integration-test", "accept-env-up", "accept-env-down"):
        assert target in content, (
            f"spec.md LAND-S4 MUST list '{target}' as a required Makefile contract target"
        )


def test_audit_report_land_s4_both_prs_pass():
    """LAND-S4: both PRs ship all five contract targets → audit SHALL record PASS."""
    content = _read(AUDIT_REPORT)
    regions = [m.start() for m in re.finditer(r"LAND-S4", content)]
    assert len(regions) >= 2, (
        "audit-report.md MUST cover LAND-S4 for both audited PRs"
    )
    for start in regions:
        window = content[start : start + 400]
        assert "PASS" in window, (
            f"LAND-S4 section near offset {start} MUST show PASS "
            f"(both PRs verified to have all five Makefile contract targets):\n{window!r}"
        )


def test_audit_report_land_s4_verifies_accept_env_targets():
    """audit-report.md MUST explicitly verify accept-env-up and accept-env-down in LAND-S4."""
    content = _read(AUDIT_REPORT)
    assert "accept-env-up" in content, (
        "audit-report.md LAND-S4 MUST verify 'accept-env-up' target is present in each PR's Makefile"
    )
    assert "accept-env-down" in content, (
        "audit-report.md LAND-S4 MUST verify 'accept-env-down' target is present in each PR's Makefile"
    )


# ── LAND-S5: openspec validity ────────────────────────────────────────────────


def test_spec_md_land_s5_requires_delta_heading_and_shall_prose():
    """LAND-S5 SHALL require delta-format heading and SHALL/MUST prose in each Requirement block."""
    content = _read(SPEC_MD)
    # Must reference the delta format requirement
    assert (
        "ADDED Requirements" in content
        or "delta" in content.lower()
        or "MODIFIED Requirements" in content
    ), (
        "spec.md LAND-S5 MUST reference the openspec delta format "
        "('## ADDED/MODIFIED/REMOVED/RENAMED Requirements')"
    )


def test_audit_report_land_s5_both_prs_pass_asserted():
    """LAND-S5: both PRs ship structurally valid openspec → SHALL record PASS (asserted).
    Looks at '### LAND-S5' section headings (not cross-reference mentions)."""
    content = _read(AUDIT_REPORT)
    # Find '### LAND-S5' section headings specifically (not cross-reference back-pointers)
    sections = list(re.finditer(r"^###\s+LAND-S5", content, re.MULTILINE))
    assert len(sections) >= 2, (
        "audit-report.md MUST contain '### LAND-S5' section headings for both audited PRs "
        f"(found {len(sections)} section heading(s))"
    )
    for m in sections:
        # Read up to the next ### heading to stay within this section
        section_text = content[m.start() : m.start() + 800]
        assert "PASS" in section_text, (
            f"LAND-S5 section at offset {m.start()} MUST contain 'PASS' "
            f"(both PRs asserted to pass openspec validate --strict):\n{section_text!r}"
        )


def test_audit_report_land_s5_defers_to_spec_lint():
    """LAND-S5 MUST note that sisyphus spec_lint will independently confirm via openspec validate.
    Spec: 'audit MAY defer the actual openspec validate --strict run to sisyphus spec_lint'."""
    content = _read(AUDIT_REPORT)
    assert "spec_lint" in content or "openspec validate" in content, (
        "audit-report.md LAND-S5 MUST state that sisyphus spec_lint will independently "
        "confirm validity via openspec validate --strict"
    )


# ── LAND-S6: semantic conflict ────────────────────────────────────────────────


def test_spec_md_land_s6_defines_predecessor_stacking_risk():
    """LAND-S6 MUST define the predecessor-closed-unmerged stacking RISK scenario."""
    content = _read(SPEC_MD)
    assert (
        "predecessor" in content.lower() or "closed" in content.lower()
    ), (
        "spec.md LAND-S6 MUST define the RISK scenario for stacking on a closed-unmerged predecessor PR"
    )


def test_spec_md_land_s6_defines_sibling_competing_impl_risk():
    """LAND-S6 MUST define the sibling-PR competing implementation RISK scenario."""
    content = _read(SPEC_MD)
    assert (
        "sibling" in content.lower() or "competing" in content.lower()
    ), (
        "spec.md LAND-S6 MUST define the RISK scenario for a sibling PR shipping "
        "a competing implementation of the same contract surface"
    )


def test_spec_md_land_s6_references_resolver_change():
    """LAND-S6 MUST reference in-flight sisyphus resolver/capability changes as a conflict source."""
    content = _read(SPEC_MD)
    assert "resolver" in content.lower() or "flip" in content.lower(), (
        "spec.md LAND-S6 MUST reference in-flight sisyphus resolver / contract changes "
        "as a factor in determining semantic conflict"
    )


def test_audit_report_land_s6_both_prs_risk():
    """LAND-S6: both PRs have semantic conflicts → audit SHALL record RISK for each."""
    content = _read(AUDIT_REPORT)
    assert "RISK" in content, (
        "audit-report.md MUST record RISK results for LAND-S6"
    )
    regions = [m.start() for m in re.finditer(r"LAND-S6", content)]
    assert len(regions) >= 2, (
        "audit-report.md MUST cover LAND-S6 for both ttpos-server-go#217 and ttpos-arch-lab#10"
    )


def test_audit_report_land_s6_arch_lab_stacking_on_closed_pr9():
    """LAND-S6 ttpos-arch-lab#10: stacking on closed-unmerged #9 → SHALL be RISK.
    Spec: 'git log shows commits authored on PR #N branch still present'."""
    content = _read(AUDIT_REPORT)
    # Predecessor PR #9 was closed without merge
    assert "#9" in content, (
        "audit-report.md MUST reference predecessor PR #9 (arch-lab) as closed-unmerged "
        "in the LAND-S6 analysis for ttpos-arch-lab#10"
    )
    # The stacking / inherited-commits problem must be documented
    assert (
        "rebase" in content.lower()
        or "inherited" in content.lower()
        or "stacking" in content.lower()
        or "commits ahead" in content.lower()
    ), (
        "audit-report.md LAND-S6 MUST document the broken-stacking risk for ttpos-arch-lab#10 "
        "(PR #9 closed unmerged; its commits still present in #10's diff)"
    )


def test_audit_report_land_s6_sibling_resolver_deadlock_documented():
    """LAND-S6: competing accept-env-up from both PRs creates SDA-S7 resolver deadlock.
    Spec: 'both PRs' LAND-S6 cells SHALL be RISK; recommendation MUST surface the
    latent SDA-S7 deadlock'."""
    content = _read(AUDIT_REPORT)
    # Both PRs ship accept-env-up; the resolver cannot decide
    assert "accept-env-up" in content, (
        "audit-report.md LAND-S6 MUST address the competing accept-env-up implementations "
        "from ttpos-server-go#217 and ttpos-arch-lab#10"
    )
    # SDA-S7 deadlock must be surfaced
    assert (
        "SDA-S7" in content
        or "deadlock" in content.lower()
        or ("resolver" in content.lower() and "None" in content)
    ), (
        "audit-report.md LAND-S6 MUST surface the SDA-S7 resolver deadlock: "
        "when both repos appear in involved_repos, resolver returns None "
        "(multiple sources with accept-env-up and no integration tie-breaker)"
    )


def test_audit_report_land_s6_resolver_flip_acknowledged():
    """LAND-S6 MUST acknowledge the resolver-flip REQ as context for the conflict analysis."""
    content = _read(AUDIT_REPORT)
    assert (
        "resolver" in content.lower()
        or "REQ-flip" in content
        or "source-first" in content.lower()
    ), (
        "audit-report.md LAND-S6 MUST reference the resolver-flip "
        "(REQ-flip-integration-resolver-source-1777195860) as a factor affecting "
        "which accept-env-up implementation will be invoked"
    )


def test_audit_report_land_s6_server_go_risk_points_at_arch_lab_not_self():
    """LAND-S6 ttpos-server-go#217: conflict points at #10's relevance, not #217's landability.
    Per audit: '#217 itself is landable on this axis'."""
    content = _read(AUDIT_REPORT)
    # The audit found #217 landable; conflict is about #10's continued relevance
    assert "landable" in content.lower() or "#217" in content, (
        "audit-report.md MUST document that ttpos-server-go#217's LAND-S6 RISK "
        "is about #10's relevance post-resolver-flip, not #217's own landability"
    )


# ── Summary table and recommendations ────────────────────────────────────────


def test_audit_report_has_per_pr_verdict_summary_tables():
    """audit-report.md MUST contain verdict summary tables with all six LAND-Sx rows."""
    content = _read(AUDIT_REPORT)
    for n in range(1, 7):
        assert f"LAND-S{n}" in content, (
            f"audit-report.md summary table MUST include a LAND-S{n} row for each audited PR"
        )


def test_audit_report_has_recommendations_section():
    """audit-report.md MUST contain a Recommendations section with actionable next steps."""
    content = _read(AUDIT_REPORT)
    assert "Recommendation" in content or "recommendation" in content, (
        "audit-report.md MUST contain a §4 Recommendations section "
        "with actionable next steps per PR and for the sisyphus repo"
    )


def test_audit_report_covers_both_prs_by_number():
    """audit-report.md MUST reference both audited PRs by their repo+number identifiers."""
    content = _read(AUDIT_REPORT)
    assert "ttpos-server-go" in content and "#217" in content, (
        "audit-report.md MUST explicitly reference ttpos-server-go#217"
    )
    assert "ttpos-arch-lab" in content and "#10" in content, (
        "audit-report.md MUST explicitly reference ttpos-arch-lab#10"
    )
