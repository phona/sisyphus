"""Challenger contract tests for REQ-flutter-mobile-accept-cookbook-1777247423.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-flutter-mobile-accept-cookbook-1777247423/specs/mobile-accept-cookbook/spec.md

Scenarios covered:
  FMAC-S1  cookbook file exists at docs/cookbook/ttpos-flutter-mobile-accept-env.md
  FMAC-S2  cookbook is reachable from ttpos-flutter-makefile.md §4.3 and §9 table
  FMAC-S3  cookbook is reachable from integration-contracts.md §4.2.2
  FMAC-S4  cookbook §1 decision tree distinguishes self-hosted vs arch-lab
  FMAC-S5  Makefile template emits final-line JSON with endpoint, namespace, stack key
  FMAC-S6  mock backend compose skeleton uses dynamic host port and healthcheck
  FMAC-S7  anti-patterns explicitly forbid embedding emulator in Flutter source repo
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COOKBOOK_DIR = REPO_ROOT / "docs" / "cookbook"
COOKBOOK_NEW = COOKBOOK_DIR / "ttpos-flutter-mobile-accept-env.md"
COOKBOOK_MAKEFILE = COOKBOOK_DIR / "ttpos-flutter-makefile.md"
INTEGRATION_CONTRACTS = REPO_ROOT / "docs" / "integration-contracts.md"


# ──────────────────────────────────────────────────────────────────────────
# FMAC-S1: cookbook file exists at the published path
# ──────────────────────────────────────────────────────────────────────────


def test_fmac_s1_cookbook_file_exists() -> None:
    """FMAC-S1: docs/cookbook/ttpos-flutter-mobile-accept-env.md exists."""
    assert COOKBOOK_NEW.exists(), (
        f"docs/cookbook/ttpos-flutter-mobile-accept-env.md not found; "
        f"expected alongside {[p.name for p in COOKBOOK_DIR.glob('*.md')]}"
    )


def test_fmac_s1_cookbook_dir_has_all_three_cookbooks() -> None:
    """FMAC-S1: cookbook dir contains the new file alongside the two existing ones."""
    expected = {
        "ttpos-flutter-mobile-accept-env.md",
        "ttpos-flutter-makefile.md",
        "ttpos-arch-lab-accept-env.md",
    }
    present = {p.name for p in COOKBOOK_DIR.glob("*.md")}
    missing = expected - present
    assert not missing, (
        f"docs/cookbook/ is missing expected files: {missing}; found: {present}"
    )


# ──────────────────────────────────────────────────────────────────────────
# FMAC-S2: cookbook reachable from ttpos-flutter-makefile.md §4.3 and §9 table
# ──────────────────────────────────────────────────────────────────────────


def test_fmac_s2_makefile_cookbook_section_43_links_new_cookbook() -> None:
    """FMAC-S2: ttpos-flutter-makefile.md §4.3 contains an inline link to the new cookbook."""
    assert COOKBOOK_MAKEFILE.exists(), "ttpos-flutter-makefile.md not found"
    text = COOKBOOK_MAKEFILE.read_text()

    # Locate §4.3 section
    m = re.search(r"#+\s*4\.3\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "ttpos-flutter-makefile.md must contain a §4.3 section"

    section_43 = m.group(0)
    assert "ttpos-flutter-mobile-accept-env.md" in section_43, (
        "§4.3 of ttpos-flutter-makefile.md must contain an inline link to "
        "ttpos-flutter-mobile-accept-env.md"
    )


def test_fmac_s2_makefile_cookbook_section_9_relationship_table_has_new_cookbook() -> None:
    """FMAC-S2: §9 relationship table in ttpos-flutter-makefile.md lists the new cookbook."""
    assert COOKBOOK_MAKEFILE.exists(), "ttpos-flutter-makefile.md not found"
    text = COOKBOOK_MAKEFILE.read_text()

    # §9 may be titled "关系" or "Relationship" or similar
    m = re.search(r"#+\s*9\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "ttpos-flutter-makefile.md must contain a §9 section (relationship table)"

    section_9 = m.group(0)
    assert "ttpos-flutter-mobile-accept-env" in section_9, (
        "§9 relationship table of ttpos-flutter-makefile.md must reference "
        "ttpos-flutter-mobile-accept-env (new cookbook) as a third column/entry"
    )


# ──────────────────────────────────────────────────────────────────────────
# FMAC-S3: cookbook reachable from integration-contracts.md §4.2.2
# ──────────────────────────────────────────────────────────────────────────


def test_fmac_s3_integration_contracts_4_2_2_links_new_cookbook() -> None:
    """FMAC-S3: docs/integration-contracts.md §4.2.2 references the new cookbook."""
    assert INTEGRATION_CONTRACTS.exists(), "docs/integration-contracts.md not found"
    text = INTEGRATION_CONTRACTS.read_text()

    # Find §4.2.2 section
    m = re.search(r"#+\s*4\.2\.2\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "docs/integration-contracts.md must contain a §4.2.2 section"

    section_422 = m.group(0)
    assert "ttpos-flutter-mobile-accept-env" in section_422, (
        "§4.2.2 of docs/integration-contracts.md must include an explicit link to "
        "cookbook/ttpos-flutter-mobile-accept-env.md"
    )


# ──────────────────────────────────────────────────────────────────────────
# FMAC-S4: cookbook §1 decision tree distinguishes self-hosted vs arch-lab
# ──────────────────────────────────────────────────────────────────────────


def test_fmac_s4_cookbook_has_section_1_decision_tree() -> None:
    """FMAC-S4: cookbook contains a §1 decision-tree section."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    m = re.search(r"#+\s*1\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §1 section (decision tree)"


def test_fmac_s4_decision_tree_mentions_arch_lab() -> None:
    """FMAC-S4: §1 recommends arch-lab when UI/emulator validation is needed."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    m = re.search(r"#+\s*1\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §1 section"
    section_1 = m.group(0).lower()

    assert "arch-lab" in section_1, (
        "§1 decision tree must recommend arch-lab when UI/emulator validation is needed"
    )


def test_fmac_s4_decision_tree_mentions_http_limitation() -> None:
    """FMAC-S4: §1 notes self-hosted is limited to HTTP-level validation."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    m = re.search(r"#+\s*1\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §1 section"
    section_1 = m.group(0).lower()

    # Accept either English or Chinese keywords for HTTP / HTTP-level
    has_http = re.search(r"http", section_1)
    assert has_http, (
        "§1 decision tree must state that self-hosted is limited to HTTP-level "
        "scenarios (no UI / emulator validation)"
    )


# ──────────────────────────────────────────────────────────────────────────
# FMAC-S5: Makefile template emits final-line JSON with required keys
# ──────────────────────────────────────────────────────────────────────────


def test_fmac_s5_cookbook_has_section_4_makefile() -> None:
    """FMAC-S5: cookbook contains a §4 Makefile section."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    m = re.search(r"#+\s*4\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §4 Makefile section"


def test_fmac_s5_makefile_template_has_accept_env_up_target() -> None:
    """FMAC-S5: §4 Makefile template defines accept-env-up target."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    assert "accept-env-up" in text, (
        "cookbook §4 Makefile template must define the accept-env-up target"
    )
    assert "accept-env-down" in text, (
        "cookbook §4 Makefile template must define the accept-env-down target"
    )


def test_fmac_s5_makefile_template_emits_endpoint_json_key() -> None:
    """FMAC-S5: §4 Makefile template contains 'endpoint' key in final-line JSON."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    assert '"endpoint"' in text or "'endpoint'" in text or "endpoint" in text, (
        "cookbook §4 Makefile template must emit JSON with 'endpoint' key "
        "(integration-contracts §3 contract)"
    )


def test_fmac_s5_makefile_template_emits_namespace_key() -> None:
    """FMAC-S5: §4 Makefile template contains 'namespace' key in final-line JSON."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    assert "namespace" in text, (
        "cookbook §4 Makefile template must emit JSON with 'namespace' key"
    )


def test_fmac_s5_makefile_template_emits_stack_flutter_self_hosted() -> None:
    """FMAC-S5: §4 Makefile template contains stack: 'flutter-self-hosted' JSON key."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    assert "flutter-self-hosted" in text, (
        "cookbook §4 Makefile template must emit JSON with "
        "stack: \"flutter-self-hosted\" extension key"
    )


def test_fmac_s5_makefile_template_routes_progress_to_stderr() -> None:
    """FMAC-S5: cookbook explains that progress logs go to stderr (not stdout)."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    # Should mention stderr in context of progress logs
    assert "stderr" in text.lower(), (
        "cookbook must explain that progress logs (compose --wait etc.) are "
        "written to stderr so they do not contaminate the final-line JSON parser"
    )


# ──────────────────────────────────────────────────────────────────────────
# FMAC-S6: mock backend compose skeleton uses dynamic port + healthcheck
# ──────────────────────────────────────────────────────────────────────────


def test_fmac_s6_cookbook_has_section_3_compose() -> None:
    """FMAC-S6: cookbook contains a §3 mock backend / compose section."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    m = re.search(r"#+\s*3\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §3 section (mock backend stack)"


def test_fmac_s6_compose_skeleton_references_accept_yml() -> None:
    """FMAC-S6: §3 references tests/docker-compose.accept.yml."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    assert "docker-compose.accept.yml" in text or "docker-compose.accept" in text, (
        "cookbook §3 must reference tests/docker-compose.accept.yml skeleton"
    )


def test_fmac_s6_compose_skeleton_uses_dynamic_host_port() -> None:
    """FMAC-S6: §3 compose skeleton uses ports: ['8080'] (no fixed host port binding)."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    # Extract §3 section only — the cookbook may reference "8080:8080" elsewhere
    # (e.g., §9 anti-patterns showing what NOT to do). Only the §3 compose skeleton
    # is required to use dynamic host-port allocation.
    m = re.search(r"(#+\s*3\b.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §3 section"
    section_3 = m.group(0)

    # The §3 YAML code block must show dynamic port ("8080" not "8080:8080")
    has_dynamic_port = (
        re.search(r'ports:\s*\[?"8080"\]?', section_3)
        or re.search(r'- "8080"', section_3)
        or re.search(r"- '8080'", section_3)
        or re.search(r'"8080"', section_3)
    )
    assert has_dynamic_port, (
        "cookbook §3 compose skeleton must expose `ports: [\"8080\"]` "
        "(container port only, no fixed host port) to allow concurrent REQ isolation"
    )

    # Within the §3 compose YAML blocks, must NOT have a fixed 8080:8080 binding
    yaml_blocks = re.findall(r"```(?:ya?ml)?\n(.+?)```", section_3, re.DOTALL)
    for block in yaml_blocks:
        assert "8080:8080" not in block, (
            "cookbook §3 compose skeleton YAML must NOT bind fixed host port 8080:8080; "
            "use `ports: [\"8080\"]` for dynamic host-port allocation"
        )


def test_fmac_s6_compose_skeleton_declares_healthcheck() -> None:
    """FMAC-S6: §3 compose skeleton backend service declares a healthcheck."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    assert "healthcheck" in text.lower(), (
        "cookbook §3 compose skeleton must declare a healthcheck on the backend "
        "service so that `docker compose up --wait` blocks until the service is ready"
    )


def test_fmac_s6_explains_why_dynamic_port_needed() -> None:
    """FMAC-S6: cookbook explains that dynamic ports prevent concurrent REQ host-port collisions."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text().lower()

    has_collision_explanation = (
        "collision" in text
        or "撞" in text
        or "concurrent" in text
        or "并发" in text
        or "port collision" in text
    )
    assert has_collision_explanation, (
        "cookbook must explain that dynamic host-port allocation (no fixed 8080:8080) "
        "prevents host-port collisions when multiple concurrent REQs run"
    )


# ──────────────────────────────────────────────────────────────────────────
# FMAC-S7: §9 anti-patterns explicitly forbid emulator in Flutter source repo
# ──────────────────────────────────────────────────────────────────────────


def test_fmac_s7_cookbook_has_section_9_anti_patterns() -> None:
    """FMAC-S7: cookbook contains a §9 anti-patterns section."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    m = re.search(r"#+\s*9\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §9 section (anti-patterns / 不要做的事)"


def test_fmac_s7_anti_patterns_forbid_emulator_in_flutter_repo() -> None:
    """FMAC-S7: §9 states emulator containers MUST NOT be added to Flutter source repo."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    m = re.search(r"#+\s*9\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §9 section"
    section_9 = m.group(0).lower()

    # Must mention emulator prohibition
    has_emulator = "emulator" in section_9 or "模拟器" in section_9
    assert has_emulator, (
        "§9 anti-patterns must explicitly address emulator containers "
        "(they MUST NOT be added to the Flutter source repo)"
    )


def test_fmac_s7_anti_patterns_back_pointer_to_arch_lab_cookbook() -> None:
    """FMAC-S7: §9 includes a back-pointer to ttpos-arch-lab-accept-env.md."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    m = re.search(r"#+\s*9\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §9 section"
    section_9 = m.group(0)

    assert "ttpos-arch-lab-accept-env" in section_9, (
        "§9 anti-patterns must include a back-pointer to ttpos-arch-lab-accept-env.md "
        "for engineers who need UI/flutter-drive validation"
    )


def test_fmac_s7_anti_patterns_ui_drive_belongs_in_arch_lab() -> None:
    """FMAC-S7: §9 states UI / flutter drive validation belongs in arch-lab integration repo."""
    assert COOKBOOK_NEW.exists(), "ttpos-flutter-mobile-accept-env.md not found"
    text = COOKBOOK_NEW.read_text()

    m = re.search(r"#+\s*9\b(.+?)(?=^#+\s|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "cookbook must contain a §9 section"
    section_9 = m.group(0).lower()

    has_ui_or_drive = (
        "flutter drive" in section_9
        or "flutter_drive" in section_9
        or "ui" in section_9
        or "arch-lab" in section_9
    )
    assert has_ui_or_drive, (
        "§9 must state that UI / flutter drive validation belongs in the arch-lab "
        "integration repo, not the Flutter source repo"
    )
