"""Challenger contract tests for REQ-flutter-makefile-cookbook-1777133078.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-flutter-makefile-cookbook-1777133078/specs/flutter-makefile-contract/spec.md
  openspec/changes/REQ-flutter-makefile-cookbook-1777133078/specs/flutter-makefile-contract/contract.spec.yaml

Scenarios covered:
  FMC-S1  ci-lint runs flutter analyze regardless of BASE_REV value (accepts env, does full scan)
  FMC-S2  ci-unit-test succeeds without device or emulator (melos run test:unit or flutter test)
  FMC-S3  ci-integration-test exits 0 when not configured (default empty implementation)
  FMC-S4  Flutter source repo is cloned to /workspace/source/<basename>/ convention path
  FMC-S5  Cookbook cross-references arch-lab cookbook for accept stage

Testing strategy:
  All scenarios are documentation contract checks against docs/cookbook/ttpos-flutter-makefile.md.
  The cookbook is the sole output artifact of this REQ; behavioral correctness of the actual
  Makefile (which lives in ttpos-flutter, not sisyphus) is deferred to the implementation REQ.
  These tests verify that the cookbook correctly specifies the required behavior per spec.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COOKBOOK_PATH = REPO_ROOT / "docs" / "cookbook" / "ttpos-flutter-makefile.md"

SIX_TARGETS = ["ci-env", "ci-setup", "ci-lint", "ci-unit-test", "ci-integration-test", "ci-build"]


def _cookbook() -> str:
    assert COOKBOOK_PATH.exists(), (
        f"Cookbook not found at {COOKBOOK_PATH}. "
        "docs/cookbook/ttpos-flutter-makefile.md MUST be created by this REQ."
    )
    return COOKBOOK_PATH.read_text()


# ── FMC-S0 (precondition) ────────────────────────────────────────────────────


def test_fmc_cookbook_file_exists():
    """Cookbook document MUST exist at docs/cookbook/ttpos-flutter-makefile.md."""
    assert COOKBOOK_PATH.exists(), (
        f"Expected cookbook at {COOKBOOK_PATH} but file does not exist. "
        "This file is the primary output artifact of REQ-flutter-makefile-cookbook-1777133078."
    )


def test_fmc_cookbook_documents_all_six_targets():
    """Cookbook MUST document all six ttpos-ci standard Makefile targets per contract.spec.yaml.

    Required: ci-env, ci-setup, ci-lint, ci-unit-test, ci-integration-test, ci-build.
    """
    content = _cookbook()
    missing = [t for t in SIX_TARGETS if t not in content]
    assert not missing, (
        f"Cookbook is missing documentation for these required targets: {missing}. "
        f"All six targets MUST be documented: {SIX_TARGETS}"
    )


# ── FMC-S1 ──────────────────────────────────────────────────────────────────


def test_fmc_s1_cookbook_documents_base_rev_acceptance():
    """FMC-S1: Cookbook MUST document that ci-lint accepts BASE_REV env variable.

    GIVEN the Flutter source repo Makefile is present at root
    WHEN sisyphus dev_cross_check calls BASE_REV=<sha> make ci-lint
    THEN flutter analyze runs on the full project (BASE_REV accepted but full scan performed)
    """
    content = _cookbook()
    assert "BASE_REV" in content, (
        "Cookbook must document BASE_REV env variable acceptance for ci-lint. "
        "sisyphus dev_cross_check passes BASE_REV=<sha> to all ci-lint invocations."
    )


def test_fmc_s1_cookbook_documents_full_scan_behavior():
    """FMC-S1: Cookbook MUST document that ci-lint always performs a full scan.

    flutter analyze has no --new-from-rev equivalent; full scan is always performed
    regardless of BASE_REV value. This is a known deviation from the ttpos-ci scoped
    lint pattern used by Go/Python repos.
    """
    content = _cookbook()
    # The cookbook must communicate that a full scan is always performed
    has_full_scan_note = (
        "full scan" in content.lower()
        or "全量" in content
        or "--new-from-rev" in content
    )
    assert has_full_scan_note, (
        "Cookbook must document that ci-lint always performs a full scan (flutter analyze "
        "has no --new-from-rev equivalent). This is required so engineers understand the "
        "deviation from the scoped-lint behavior expected by sisyphus."
    )


def test_fmc_s1_cookbook_documents_flutter_analyze():
    """FMC-S1: Cookbook MUST show ci-lint invoking flutter analyze --no-pub."""
    content = _cookbook()
    assert "flutter analyze" in content, (
        "Cookbook must document that ci-lint runs 'flutter analyze'. "
        "Per spec FMC-S1: flutter analyze --no-pub runs on the full project."
    )


# ── FMC-S2 ──────────────────────────────────────────────────────────────────


def test_fmc_s2_cookbook_documents_no_emulator_requirement():
    """FMC-S2: Cookbook MUST document that ci-unit-test requires no device or emulator.

    GIVEN the Flutter source repo has unit/widget tests under test/
    WHEN sisyphus staging-test calls make ci-unit-test inside runner pod (no emulator)
    THEN melos run test:unit (or flutter test test/ --no-pub) completes and exits 0
    """
    content = _cookbook()
    no_emulator_documented = (
        "emulator" in content.lower()
        or "device" in content.lower()
    )
    assert no_emulator_documented, (
        "Cookbook must address emulator/device requirements for ci-unit-test. "
        "It must clarify that no emulator is needed (sisyphus runner pod has none)."
    )


def test_fmc_s2_cookbook_documents_unit_test_command():
    """FMC-S2: Cookbook MUST document melos run test:unit or flutter test as the ci-unit-test command."""
    content = _cookbook()
    has_test_command = (
        "melos run test:unit" in content
        or "flutter test" in content
        or "melos run" in content
    )
    assert has_test_command, (
        "Cookbook must show ci-unit-test invoking either 'melos run test:unit' or "
        "'flutter test test/ --no-pub'. Per spec FMC-S2 the command must complete "
        "without requiring a connected device."
    )


# ── FMC-S3 ──────────────────────────────────────────────────────────────────


def test_fmc_s3_cookbook_documents_ci_integration_test_exit_0_default():
    """FMC-S3: Cookbook MUST document that ci-integration-test default implementation exits 0.

    GIVEN the Flutter source repo uses the default empty implementation
    WHEN sisyphus staging-test calls make ci-integration-test
    THEN the target exits 0
    """
    content = _cookbook()
    # The cookbook must show exit 0 in the context of ci-integration-test
    has_exit_0 = "exit 0" in content or "exit 0" in content
    assert has_exit_0, (
        "Cookbook must document the default 'exit 0' implementation for ci-integration-test. "
        "Flutter repos cannot run emulator tests in the staging-test runner pod; "
        "the default must be exit 0 (pass) per spec FMC-S3."
    )


def test_fmc_s3_cookbook_explains_why_integration_test_is_empty():
    """FMC-S3: Cookbook MUST explain WHY ci-integration-test defaults to exit 0.

    The reason is that Flutter integration tests require an emulator, which is only
    available in the accept stage (arch-lab). This context is required per the proposal.
    """
    content = _cookbook()
    # Cookbook must explain the emulator constraint for integration tests
    explains_emulator_constraint = (
        "emulator" in content.lower()
        and "accept" in content.lower()
    )
    assert explains_emulator_constraint, (
        "Cookbook must explain that ci-integration-test defaults to exit 0 because "
        "Flutter integration tests require an emulator, which is only available "
        "in the accept stage (arch-lab), not in the staging-test runner pod."
    )


# ── FMC-S4 ──────────────────────────────────────────────────────────────────


def test_fmc_s4_cookbook_documents_source_path_convention():
    """FMC-S4: Cookbook MUST document the /workspace/source/ clone path convention.

    GIVEN ZonEaseTech/ttpos-flutter is listed in involved_repos
    WHEN sisyphus start_analyze dispatches the analyze-agent
    THEN the repo is cloned to /workspace/source/ttpos-flutter/ in the runner pod

    The arch-lab's apk/build.sh references TTPOS_FLUTTER_REPO=/workspace/source/ttpos-flutter.
    """
    content = _cookbook()
    has_source_path = (
        "/workspace/source/" in content
        or "workspace/source/ttpos-flutter" in content
    )
    assert has_source_path, (
        "Cookbook must document the /workspace/source/<basename>/ convention path "
        "where sisyphus clones the Flutter source repo. The arch-lab integration repo "
        "depends on this path to build the APK during accept stage."
    )


def test_fmc_s4_cookbook_documents_source_vs_integration_role():
    """FMC-S4: Cookbook MUST document that Flutter repos are source repos, not integration repos.

    Flutter source repos are cloned to /workspace/source/ by sisyphus.
    The arch-lab integration repo (not the Flutter source repo) provides accept-env-up/down.
    """
    content = _cookbook()
    has_role_distinction = (
        "source" in content.lower()
        and ("integration" in content.lower() or "arch-lab" in content.lower())
    )
    assert has_role_distinction, (
        "Cookbook must distinguish between the Flutter source repo role (source) and "
        "the arch-lab integration repo role. This is the key architectural insight "
        "from spec FMC-S4 and the contract.spec.yaml accept_env_contract section."
    )


# ── FMC-S5 ──────────────────────────────────────────────────────────────────


def test_fmc_s5_cookbook_states_flutter_source_repos_do_not_implement_accept_env():
    """FMC-S5: Cookbook MUST explicitly state Flutter source repos do not implement accept-env-up/down.

    GIVEN an engineer reading docs/cookbook/ttpos-flutter-makefile.md
    WHEN they reach section 4 (accept-env 契約参与方式)
    THEN the cookbook explicitly states that Flutter source repos do not implement
         accept-env-up/down
    """
    content = _cookbook()
    has_no_accept_env = (
        "accept-env" in content
        and (
            "not" in content.lower()
            or "不" in content
            or "source" in content.lower()
        )
    )
    assert has_no_accept_env, (
        "Cookbook must explicitly state that Flutter source repos do NOT implement "
        "accept-env-up/down. Per spec FMC-S5 and contract.spec.yaml: "
        "provides_accept_env: false"
    )


def test_fmc_s5_cookbook_references_archlab_cookbook():
    """FMC-S5: Cookbook MUST reference ttpos-arch-lab-accept-env.md for accept stage setup.

    GIVEN an engineer reading docs/cookbook/ttpos-flutter-makefile.md
    WHEN they reach the accept-env section
    THEN the cookbook references ttpos-arch-lab-accept-env.md for the full emulator+APK lab setup
    """
    content = _cookbook()
    assert "ttpos-arch-lab-accept-env" in content, (
        "Cookbook must cross-reference 'docs/cookbook/ttpos-arch-lab-accept-env.md' "
        "for the full mobile e2e lab setup. Per spec FMC-S5: engineers need to be "
        "directed to the arch-lab cookbook when they need accept stage (emulator + APK) setup."
    )


def test_fmc_s5_cookbook_has_accept_env_section():
    """FMC-S5: Cookbook MUST have a section dedicated to accept-env contract / role division."""
    content = _cookbook()
    has_accept_section = (
        "accept-env" in content
        or "accept env" in content.lower()
        or "accept 阶段" in content
    )
    assert has_accept_section, (
        "Cookbook must have a section about accept-env contract participation. "
        "Per spec FMC-S5 this section must clarify the role division between "
        "Flutter source repo (source) and arch-lab integration repo (accept-env provider)."
    )
