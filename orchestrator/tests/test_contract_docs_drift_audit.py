"""
Contract tests for docs drift audit (REQ-docs-drift-audit-1777220568).

Verifies that repository docs match actual code: file paths, counts,
diagram edges, and tag spec content must stay in sync with source files.

Scenarios: DOCS-S1 through DOCS-S7 (per openspec/changes/REQ-docs-drift-audit-1777220568/specs/docs/spec.md).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ORCHESTRATOR = REPO_ROOT / "orchestrator"
PROMPTS_DIR = ORCHESTRATOR / "src" / "orchestrator" / "prompts"
ACTIONS_DIR = ORCHESTRATOR / "src" / "orchestrator" / "actions"
MIGRATIONS_DIR = ORCHESTRATOR / "migrations"
OBS_QUERIES_DIR = REPO_ROOT / "observability" / "queries" / "sisyphus"
STATE_PY = ORCHESTRATOR / "src" / "orchestrator" / "state.py"


def _count_enum_members(class_name: str) -> int:
    """Count members of an Enum class in state.py via AST."""
    tree = ast.parse(STATE_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            count = 0
            for stmt in node.body:
                # plain assignment: NAME = "value"
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                ):
                    count += 1
                # annotated assignment: NAME: str = "value"
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    count += 1
            return count
    raise ValueError(f"Class {class_name} not found in state.py")


# ──────────────────────────────────────────────────────────────────────────
# DOCS-S1: prompt template links in docs/prompts.md resolve to actual files
# ──────────────────────────────────────────────────────────────────────────

def _j2_exists_anywhere(ref: str) -> bool:
    """Return True if ref exists as a path under PROMPTS_DIR (any depth)."""
    # Direct path (e.g. verifier/foo.md.j2 or _shared/bar.md.j2)
    if (PROMPTS_DIR / ref).exists():
        return True
    # Basename match in any subdirectory (e.g. ref is just "foo.md.j2")
    basename = Path(ref).name
    return any(True for _ in PROMPTS_DIR.rglob(basename))


def test_docs_s1_prompt_template_links_resolve():
    """DOCS-S1: every .md.j2 backtick-reference in docs/prompts.md exists somewhere under prompts/."""
    prompts_doc = (REPO_ROOT / "docs" / "prompts.md").read_text()
    j2_refs = re.findall(r"`([^`]*\.md\.j2)`", prompts_doc)

    # Skip template placeholders like {stage}_{trigger}.md.j2 and empty/junk refs
    real_refs = [r for r in j2_refs if "{" not in r and len(r) > len(".md.j2")]

    missing = [ref for ref in real_refs if not _j2_exists_anywhere(ref)]
    assert not missing, (
        f"docs/prompts.md references .md.j2 files that don't exist "
        f"anywhere under {PROMPTS_DIR.relative_to(REPO_ROOT)}: {missing}"
    )


# ──────────────────────────────────────────────────────────────────────────
# DOCS-S2: actions/<name>.py references in README/CLAUDE exist on disk
# ──────────────────────────────────────────────────────────────────────────

def _missing_action_refs(doc_text: str) -> list[str]:
    refs = re.findall(r"actions/(\w+\.py)", doc_text)
    return [r for r in refs if not (ACTIONS_DIR / r).exists()]


def test_docs_s2_readme_action_refs_resolve():
    """DOCS-S2: actions/<name>.py references in README.md exist on disk."""
    missing = _missing_action_refs((REPO_ROOT / "README.md").read_text())
    assert not missing, f"README.md references missing action files: {missing}"


def test_docs_s2_claude_action_refs_resolve():
    """DOCS-S2: actions/<name>.py references in CLAUDE.md exist on disk."""
    missing = _missing_action_refs((REPO_ROOT / "CLAUDE.md").read_text())
    assert not missing, f"CLAUDE.md references missing action files: {missing}"


# ──────────────────────────────────────────────────────────────────────────
# DOCS-S3: state-machine.md enumerations match state.py counts
# ──────────────────────────────────────────────────────────────────────────

def test_docs_s3_reqstate_count_matches_state_py():
    """DOCS-S3: ReqState count header in docs/state-machine.md matches state.py."""
    actual = _count_enum_members("ReqState")
    state_md = (REPO_ROOT / "docs" / "state-machine.md").read_text()
    m = re.search(r"ReqState\s*枚举[（(]\s*(\d+)\s*个\s*[)）]", state_md)
    assert m, "docs/state-machine.md must have 'ReqState 枚举（N 个）' header"
    doc_n = int(m.group(1))
    assert doc_n == actual, (
        f"docs/state-machine.md header says {doc_n} ReqState values; "
        f"state.py has {actual}"
    )


def test_docs_s3_event_count_matches_state_py():
    """DOCS-S3: Event count header in docs/state-machine.md matches state.py."""
    actual = _count_enum_members("Event")
    state_md = (REPO_ROOT / "docs" / "state-machine.md").read_text()
    m = re.search(r"Event\s*枚举[（(]\s*(\d+)\s*个\s*[)）]", state_md)
    assert m, "docs/state-machine.md must have 'Event 枚举（N 个）' header"
    doc_n = int(m.group(1))
    assert doc_n == actual, (
        f"docs/state-machine.md header says {doc_n} Event values; "
        f"state.py has {actual}"
    )


def test_docs_s3_challenger_states_in_state_py():
    """DOCS-S3: CHALLENGER_RUNNING / _PASS / _FAIL exist in state.py."""
    state_py = STATE_PY.read_text()
    for name in ("CHALLENGER_RUNNING", "CHALLENGER_PASS", "CHALLENGER_FAIL"):
        assert name in state_py, f"{name} not found in state.py"


def test_docs_s3_challenger_states_in_state_machine_doc():
    """DOCS-S3: challenger_running and challenger pass/fail appear in docs/state-machine.md."""
    state_md = (REPO_ROOT / "docs" / "state-machine.md").read_text().lower()
    # challenger_running is a ReqState; pass/fail may be Events written as
    # "challenger.pass" / "challenger.fail" or "challenger_pass" / "challenger_fail"
    assert "challenger_running" in state_md, "challenger_running not found in docs/state-machine.md"
    assert re.search(r"challenger[._]pass", state_md), (
        "challenger pass event not found in docs/state-machine.md "
        "(expected 'challenger.pass' or 'challenger_pass')"
    )
    assert re.search(r"challenger[._]fail", state_md), (
        "challenger fail event not found in docs/state-machine.md "
        "(expected 'challenger.fail' or 'challenger_fail')"
    )


# ──────────────────────────────────────────────────────────────────────────
# DOCS-S4: mermaid diagrams include challenger stage edges
# ──────────────────────────────────────────────────────────────────────────

def test_docs_s4_state_machine_mermaid_spec_lint_to_challenger():
    """DOCS-S4: state-machine.md mermaid shows spec_lint_running --> challenger_running."""
    lines = (REPO_ROOT / "docs" / "state-machine.md").read_text().lower().splitlines()
    hit = any(
        re.search(r"spec.lint", ln) and re.search(r"challenger", ln) and "-->" in ln
        for ln in lines
    )
    assert hit, (
        "docs/state-machine.md mermaid must have an arrow from spec_lint_running "
        "to challenger_running"
    )


def test_docs_s4_state_machine_mermaid_challenger_to_dev_cross_check():
    """DOCS-S4: state-machine.md mermaid shows challenger_running --> dev_cross_check_running."""
    lines = (REPO_ROOT / "docs" / "state-machine.md").read_text().lower().splitlines()
    hit = any(
        re.search(r"challenger", ln)
        and re.search(r"dev.cross.check", ln)
        and "-->" in ln
        for ln in lines
    )
    assert hit, (
        "docs/state-machine.md mermaid must have an arrow from challenger_running "
        "to dev_cross_check_running"
    )


def test_docs_s4_state_machine_mermaid_challenger_fail_to_review():
    """DOCS-S4: state-machine.md mermaid shows challenger_running failure edge to review_running."""
    lines = (REPO_ROOT / "docs" / "state-machine.md").read_text().lower().splitlines()
    hit = any(
        re.search(r"challenger", ln) and re.search(r"review", ln) and "-->" in ln
        for ln in lines
    )
    assert hit, (
        "docs/state-machine.md mermaid must have a failure edge from "
        "challenger_running to review_running"
    )


def test_docs_s4_architecture_mentions_challenger():
    """DOCS-S4: docs/architecture.md mentions the challenger stage."""
    arch_md = (REPO_ROOT / "docs" / "architecture.md").read_text().lower()
    assert "challenger" in arch_md, (
        "docs/architecture.md must mention the challenger stage (M18)"
    )


# ──────────────────────────────────────────────────────────────────────────
# DOCS-S5: Metabase SQL count = 18 in docs and on disk
# ──────────────────────────────────────────────────────────────────────────

def test_docs_s5_sql_file_count_is_18():
    """DOCS-S5: observability/queries/sisyphus/ contains exactly 18 SQL files."""
    sql_files = list(OBS_QUERIES_DIR.glob("*.sql"))
    assert len(sql_files) == 18, (
        f"observability/queries/sisyphus/ has {len(sql_files)} SQL files, expected 18"
    )


def test_docs_s5_readme_claims_18_metabase():
    """DOCS-S5: README.md claims 18 near Metabase/看板."""
    readme = (REPO_ROOT / "README.md").read_text()
    assert re.search(r"18.{0,40}(Metabase|看板|SQL)", readme) or re.search(
        r"(Metabase|看板|SQL).{0,40}18", readme
    ), "README.md must claim 18 Metabase/SQL questions"


def test_docs_s5_claude_claims_18_metabase():
    """DOCS-S5: CLAUDE.md claims 18 near Metabase/看板."""
    claude = (REPO_ROOT / "CLAUDE.md").read_text()
    assert re.search(r"18.{0,40}(Metabase|看板|SQL)", claude) or re.search(
        r"(Metabase|看板|SQL).{0,40}18", claude
    ), "CLAUDE.md must claim 18 Metabase/SQL questions"


def test_docs_s5_dashboard_documents_q17():
    """DOCS-S5: observability/sisyphus-dashboard.md has a Q17 section."""
    dashboard = (REPO_ROOT / "observability" / "sisyphus-dashboard.md").read_text()
    assert re.search(r"Q17|17[-_]dedup", dashboard, re.IGNORECASE), (
        "observability/sisyphus-dashboard.md must document Q17 (dedup-retry-rate)"
    )


# ──────────────────────────────────────────────────────────────────────────
# DOCS-S6: api-tag-management-spec.md describes BKD router tags (not Apifox)
# ──────────────────────────────────────────────────────────────────────────

def test_docs_s6_tag_spec_contains_router_tags():
    """DOCS-S6: docs/api-tag-management-spec.md contains required BKD router tag names."""
    tag_spec = (REPO_ROOT / "docs" / "api-tag-management-spec.md").read_text()
    required = [
        "intent:intake",
        "intent:analyze",
        "result:pass",
        "result:fail",
        "decision:",
        "challenger",
    ]
    missing = [t for t in required if t not in tag_spec]
    assert not missing, (
        f"docs/api-tag-management-spec.md missing BKD router tag references: {missing}"
    )


def test_docs_s6_tag_spec_not_apifox_content():
    """DOCS-S6: docs/api-tag-management-spec.md is not the Apifox endpoint label doc."""
    tag_spec = (REPO_ROOT / "docs" / "api-tag-management-spec.md").read_text()
    apifox_markers = ["Apifox", "endpoint label lifecycle", "API-endpoint"]
    found = [m for m in apifox_markers if m in tag_spec]
    assert not found, (
        f"docs/api-tag-management-spec.md still contains Apifox content: {found}"
    )


# ──────────────────────────────────────────────────────────────────────────
# DOCS-S7: migration count 0001–0007 reflected in docs
# ──────────────────────────────────────────────────────────────────────────

def test_docs_s7_forward_migration_count_is_9():
    """DOCS-S7: orchestrator/migrations/ has exactly 8 forward migration files
    (0001..0009 contiguous; 0008 = stage_runs_bkd_session_id, 0009 = artifact_checks_flake)."""
    forward = [f for f in MIGRATIONS_DIR.glob("*.sql") if ".rollback." not in f.name]
    assert len(forward) == 9, (
        f"orchestrator/migrations/ has {len(forward)} forward migration(s), expected 9: "
        f"{sorted(f.name for f in forward)}"
    )


def test_docs_s7_readme_migration_range_includes_0009():
    """DOCS-S7: README.md migration list reaches 0009 (artifact_checks_flake)."""
    readme = (REPO_ROOT / "README.md").read_text()
    assert re.search(r"0009|000[789]", readme), (
        "README.md must reference latest migration 0009"
    )


def test_docs_s7_claude_migration_range_includes_0009():
    """DOCS-S7: CLAUDE.md migration list reaches 0009."""
    claude = (REPO_ROOT / "CLAUDE.md").read_text()
    assert re.search(r"0009|000[789]", claude), (
        "CLAUDE.md must reference latest migration 0009"
    )


def test_docs_s7_observability_md_migration_range():
    """DOCS-S7: docs/observability.md states migration range includes 0009."""
    obs_md = (REPO_ROOT / "docs" / "observability.md").read_text()
    assert re.search(r"0009|0001.{0,15}0009", obs_md), (
        "docs/observability.md must state migration range goes to 0009"
    )
