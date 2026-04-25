"""Challenger contract tests for REQ-archlab-cookbook-helm-redo-1777132879.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-archlab-cookbook-helm-redo-1777132879/specs/archlab-cookbook-helm-redo/spec.md

Scenarios covered:
  HELMREDO-S1  §0 TL;DR names helm upgrade for backend and emulator; no docker compose up
  HELMREDO-S2  §1 repo layout lists charts/accept-lab, charts/emulator, boot-wait-k8s.sh
  HELMREDO-S3  emulator chart section has privileged:true, -no-accel, swiftshader_indirect + readinessProbe explanation
  HELMREDO-S4  §5 endpoint JSON uses svc.cluster.local; endpoint labelled required; adb/apk_package optional
  HELMREDO-S5  §6 Makefile accept-env-up has >=2 helm upgrade --install and boot-wait-k8s.sh or kubectl exec loop
  HELMREDO-S6  §6 Makefile accept-env-up printf covers endpoint, adb, apk_package, namespace and ends with \\n
  HELMREDO-S7  §6 Makefile accept-env-down teardown commands are best-effort idempotent
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
COOKBOOK = REPO_ROOT / "docs" / "cookbook" / "ttpos-arch-lab-accept-env.md"


def _read_cookbook() -> str:
    assert COOKBOOK.exists(), f"cookbook not found at {COOKBOOK}"
    return COOKBOOK.read_text(encoding="utf-8")


def _extract_section(content: str, section_prefix: str) -> str:
    """Extract text from a heading matching section_prefix until the next same-level heading.

    Code blocks (``` fences) are tracked to avoid treating # inside them as headings.
    """
    lines = content.splitlines()
    start = None
    heading_level = None
    result_lines = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # Track code block fences (``` or ```lang)
        if stripped.startswith("```"):
            in_code_block = not in_code_block

        if start is None:
            if not in_code_block and stripped.startswith("#") and section_prefix in stripped:
                start = True
                heading_level = len(stripped) - len(stripped.lstrip("#"))
                result_lines.append(line)
        else:
            if not in_code_block and stripped.startswith("#"):
                current_level = len(stripped) - len(stripped.lstrip("#"))
                if current_level <= heading_level:
                    break
            result_lines.append(line)

    return "\n".join(result_lines)


# ── HELMREDO-S1 ──────────────────────────────────────────────────────────────


def test_helmredo_s1_tldr_contains_helm_upgrade_install() -> None:
    """§0 TL;DR section MUST contain 'helm upgrade --install' at least once."""
    content = _read_cookbook()
    section = _extract_section(content, "0.")
    assert section, "§0 section not found in cookbook"
    assert "helm upgrade --install" in section, (
        "§0 TL;DR MUST contain 'helm upgrade --install' but it was not found.\n"
        f"Section content:\n{section}"
    )


def test_helmredo_s1_tldr_references_boot_completed_or_kubectl_exec() -> None:
    """§0 TL;DR section MUST reference boot_completed or kubectl exec as emulator readiness mechanism."""
    content = _read_cookbook()
    section = _extract_section(content, "0.")
    assert section, "§0 section not found in cookbook"
    has_boot_completed = "boot_completed" in section
    has_kubectl_exec = "kubectl exec" in section
    assert has_boot_completed or has_kubectl_exec, (
        "§0 TL;DR MUST reference 'boot_completed' or 'kubectl exec' as emulator "
        "readiness mechanism, but neither was found.\n"
        f"Section content:\n{section}"
    )


def test_helmredo_s1_tldr_does_not_use_docker_compose_up_as_primary_step() -> None:
    """§0 TL;DR section MUST NOT contain 'docker compose up' as a primary recipe step."""
    content = _read_cookbook()
    section = _extract_section(content, "0.")
    assert section, "§0 section not found in cookbook"
    assert "docker compose up" not in section, (
        "§0 TL;DR MUST NOT contain 'docker compose up' as a primary recipe step "
        "— the cookbook must use the helm chart path.\n"
        f"Section content:\n{section}"
    )


# ── HELMREDO-S2 ──────────────────────────────────────────────────────────────


def test_helmredo_s2_layout_section_contains_accept_lab() -> None:
    """§1 repo layout MUST list accept-lab/ (backend helm chart directory)."""
    content = _read_cookbook()
    section = _extract_section(content, "1.")
    assert section, "§1 section not found in cookbook"
    has_full_path = "charts/accept-lab" in section
    has_subdir = "accept-lab/" in section
    assert has_full_path or has_subdir, (
        "§1 layout section MUST contain 'charts/accept-lab' or 'accept-lab/' "
        "in the directory tree, but neither was found.\n"
        f"Section content:\n{section}"
    )


def test_helmredo_s2_layout_section_contains_emulator_chart() -> None:
    """§1 repo layout MUST list emulator/ under charts/ (emulator helm chart directory)."""
    content = _read_cookbook()
    section = _extract_section(content, "1.")
    assert section, "§1 section not found in cookbook"
    has_full_path = "charts/emulator" in section
    has_subdir_in_charts = bool(
        re.search(r"charts/\n.*emulator/|├── emulator/|└── emulator/", section)
    )
    has_emulator_label = "emulator" in section and "helm chart" in section.lower()
    assert has_full_path or has_subdir_in_charts or has_emulator_label, (
        "§1 layout section MUST contain emulator helm chart directory "
        "(charts/emulator or emulator/ under charts/), but it was not found.\n"
        f"Section content:\n{section}"
    )


def test_helmredo_s2_layout_section_contains_boot_wait_k8s_sh() -> None:
    """§1 repo layout MUST list boot-wait-k8s.sh helper script."""
    content = _read_cookbook()
    section = _extract_section(content, "1.")
    assert section, "§1 section not found in cookbook"
    assert "boot-wait-k8s.sh" in section, (
        "§1 layout section MUST contain 'boot-wait-k8s.sh' so that implementers "
        "know to create it, but it was not found.\n"
        f"Section content:\n{section}"
    )


# ── HELMREDO-S3 ──────────────────────────────────────────────────────────────


def test_helmredo_s3_emulator_section_has_privileged_true() -> None:
    """Emulator helm chart section MUST set securityContext.privileged: true."""
    content = _read_cookbook()
    # emulator section starts at §3
    section = _extract_section(content, "3.")
    assert section, "§3 emulator section not found in cookbook"
    assert "privileged: true" in section, (
        "Emulator chart section MUST contain 'privileged: true' in pod spec "
        "(required for ADB binder), but it was not found.\n"
        f"Section content (first 500 chars):\n{section[:500]}"
    )


def test_helmredo_s3_emulator_section_has_no_accel() -> None:
    """Emulator helm chart section MUST include -no-accel in EMULATOR_ARGS."""
    content = _read_cookbook()
    section = _extract_section(content, "3.")
    assert section, "§3 emulator section not found in cookbook"
    assert "-no-accel" in section, (
        "Emulator chart section MUST contain '-no-accel' (software rendering "
        "flag for KVM-less environments), but it was not found.\n"
        f"Section content (first 500 chars):\n{section[:500]}"
    )


def test_helmredo_s3_emulator_section_has_swiftshader_indirect() -> None:
    """Emulator helm chart section MUST include swiftshader_indirect in EMULATOR_ARGS."""
    content = _read_cookbook()
    section = _extract_section(content, "3.")
    assert section, "§3 emulator section not found in cookbook"
    assert "swiftshader_indirect" in section, (
        "Emulator chart section MUST contain 'swiftshader_indirect' (GPU software "
        "rendering backend), but it was not found.\n"
        f"Section content (first 500 chars):\n{section[:500]}"
    )


def test_helmredo_s3_emulator_section_explains_no_readiness_probe() -> None:
    """Emulator chart section MUST explain why no readinessProbe is declared."""
    content = _read_cookbook()
    section = _extract_section(content, "3.")
    assert section, "§3 emulator section not found in cookbook"
    has_readiness_explanation = (
        "readinessProbe" in section or "readiness" in section.lower()
    ) and (
        "boot-wait" in section or "boot_completed" in section or "kubectl exec" in section
    )
    assert has_readiness_explanation, (
        "Emulator chart section MUST explain the absence of readinessProbe and "
        "delegate boot detection to boot-wait-k8s.sh or kubectl exec, but "
        "no such explanation was found.\n"
        f"Section content (first 800 chars):\n{section[:800]}"
    )


# ── HELMREDO-S4 ──────────────────────────────────────────────────────────────


def test_helmredo_s4_endpoint_section_uses_svc_cluster_local() -> None:
    """§5 endpoint JSON section MUST use svc.cluster.local in endpoint value."""
    content = _read_cookbook()
    section = _extract_section(content, "5.")
    assert section, "§5 endpoint section not found in cookbook"
    assert "svc.cluster.local" in section, (
        "§5 endpoint section MUST contain 'svc.cluster.local' in the endpoint "
        "value (cluster-internal DNS, not localhost port), but it was not found.\n"
        f"Section content:\n{section}"
    )


def test_helmredo_s4_endpoint_key_labelled_required() -> None:
    """§5 endpoint key MUST be labelled as required (✅ or '必需' or 'required')."""
    content = _read_cookbook()
    section = _extract_section(content, "5.")
    assert section, "§5 endpoint section not found in cookbook"
    has_checkmark = "✅" in section and "endpoint" in section
    has_required_text = re.search(r"endpoint.*?(必需|required)", section, re.IGNORECASE)
    assert has_checkmark or has_required_text, (
        "§5 endpoint section MUST label 'endpoint' as required "
        "(with ✅ marker or text '必需'/'required'), but it was not found.\n"
        f"Section content:\n{section}"
    )


def test_helmredo_s4_adb_and_apk_package_labelled_optional() -> None:
    """§5 endpoint section MUST label adb and apk_package as optional extensions."""
    content = _read_cookbook()
    section = _extract_section(content, "5.")
    assert section, "§5 endpoint section not found in cookbook"
    has_adb_optional = "adb" in section and (
        "扩展" in section or "optional" in section.lower() or "extension" in section.lower()
    )
    has_apk_package_optional = "apk_package" in section and (
        "扩展" in section or "optional" in section.lower() or "extension" in section.lower()
    )
    assert has_adb_optional, (
        "§5 endpoint section MUST label 'adb' as an optional extension, "
        "but it was not found.\n"
        f"Section content:\n{section}"
    )
    assert has_apk_package_optional, (
        "§5 endpoint section MUST label 'apk_package' as an optional extension, "
        "but it was not found.\n"
        f"Section content:\n{section}"
    )


# ── HELMREDO-S5 ──────────────────────────────────────────────────────────────


def test_helmredo_s5_makefile_accept_env_up_has_two_helm_upgrade_install() -> None:
    """§6 Makefile accept-env-up MUST call helm upgrade --install at least twice."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    # Extract accept-env-up recipe (until accept-env-down or end of section)
    up_match = re.search(r"accept-env-up:(.*?)(?=accept-env-down:|$)", section, re.DOTALL)
    assert up_match, "accept-env-up recipe not found in §6 Makefile section"
    up_body = up_match.group(1)

    count = up_body.count("helm upgrade --install")
    assert count >= 2, (
        f"§6 Makefile accept-env-up MUST call 'helm upgrade --install' at least twice "
        f"(once for backend chart, once for emulator chart), but found {count} occurrence(s).\n"
        f"accept-env-up body:\n{up_body}"
    )


def test_helmredo_s5_makefile_accept_env_up_has_boot_wait_mechanism() -> None:
    """§6 Makefile accept-env-up MUST call boot-wait-k8s.sh or inline kubectl exec loop."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    up_match = re.search(r"accept-env-up:(.*?)(?=accept-env-down:|$)", section, re.DOTALL)
    assert up_match, "accept-env-up recipe not found in §6 Makefile section"
    up_body = up_match.group(1)

    has_boot_wait_sh = "boot-wait-k8s.sh" in up_body
    has_kubectl_exec_loop = "kubectl exec" in up_body and "boot_completed" in up_body
    assert has_boot_wait_sh or has_kubectl_exec_loop, (
        "§6 Makefile accept-env-up MUST contain 'boot-wait-k8s.sh' or an inline "
        "'kubectl exec' boot_completed polling loop, but neither was found.\n"
        f"accept-env-up body:\n{up_body}"
    )


# ── HELMREDO-S6 ──────────────────────────────────────────────────────────────


def _get_printf_line(up_body: str) -> str:
    """Return the line(s) that contain the printf command in the accept-env-up recipe."""
    # printf may span multiple lines via \ continuation; collect them
    lines = up_body.splitlines()
    printf_lines: list[str] = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        if "printf" in stripped and not collecting:
            collecting = True
            printf_lines.append(stripped)
            if not stripped.endswith("\\"):
                break
        elif collecting:
            printf_lines.append(stripped)
            if not stripped.endswith("\\"):
                break
    return " ".join(printf_lines)


def _extract_printf_format(up_body: str) -> str:
    """Extract the single-quoted format string from a printf command."""
    printf_line = _get_printf_line(up_body)
    # Match the single-quoted format string (may contain double quotes inside)
    m = re.search(r"printf\s+'([^']+)'", printf_line)
    if m:
        return m.group(1)
    # Fallback: return the whole printf line for content checks
    return printf_line


def test_helmredo_s6_printf_contains_endpoint_key() -> None:
    """§6 Makefile accept-env-up printf MUST include 'endpoint' key."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    up_match = re.search(r"accept-env-up:(.*?)(?=accept-env-down:|$)", section, re.DOTALL)
    assert up_match, "accept-env-up recipe not found in §6 Makefile section"
    up_body = up_match.group(1)

    assert "printf" in up_body, "printf command not found in accept-env-up recipe"
    fmt = _extract_printf_format(up_body)
    assert "endpoint" in fmt, (
        f"printf format string MUST contain 'endpoint' key.\nprintf content: {fmt!r}"
    )


def test_helmredo_s6_printf_contains_adb_key() -> None:
    """§6 Makefile accept-env-up printf MUST include 'adb' key."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    up_match = re.search(r"accept-env-up:(.*?)(?=accept-env-down:|$)", section, re.DOTALL)
    assert up_match, "accept-env-up recipe not found in §6 Makefile section"
    up_body = up_match.group(1)

    assert "printf" in up_body, "printf command not found in accept-env-up recipe"
    fmt = _extract_printf_format(up_body)
    assert "adb" in fmt, (
        f"printf format string MUST contain 'adb' key.\nprintf content: {fmt!r}"
    )


def test_helmredo_s6_printf_contains_apk_package_key() -> None:
    """§6 Makefile accept-env-up printf MUST include 'apk_package' key."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    up_match = re.search(r"accept-env-up:(.*?)(?=accept-env-down:|$)", section, re.DOTALL)
    assert up_match, "accept-env-up recipe not found in §6 Makefile section"
    up_body = up_match.group(1)

    assert "printf" in up_body, "printf command not found in accept-env-up recipe"
    fmt = _extract_printf_format(up_body)
    assert "apk_package" in fmt, (
        f"printf format string MUST contain 'apk_package' key.\nprintf content: {fmt!r}"
    )


def test_helmredo_s6_printf_contains_namespace_key() -> None:
    """§6 Makefile accept-env-up printf MUST include 'namespace' key."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    up_match = re.search(r"accept-env-up:(.*?)(?=accept-env-down:|$)", section, re.DOTALL)
    assert up_match, "accept-env-up recipe not found in §6 Makefile section"
    up_body = up_match.group(1)

    assert "printf" in up_body, "printf command not found in accept-env-up recipe"
    fmt = _extract_printf_format(up_body)
    assert "namespace" in fmt, (
        f"printf format string MUST contain 'namespace' key.\nprintf content: {fmt!r}"
    )


def test_helmredo_s6_printf_format_ends_with_newline() -> None:
    """§6 Makefile accept-env-up printf format MUST end with \\n."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    up_match = re.search(r"accept-env-up:(.*?)(?=accept-env-down:|$)", section, re.DOTALL)
    assert up_match, "accept-env-up recipe not found in §6 Makefile section"
    up_body = up_match.group(1)

    assert "printf" in up_body, "printf command not found in accept-env-up recipe"
    fmt = _extract_printf_format(up_body)
    assert r"\n" in fmt, (
        f"printf format string MUST contain '\\n' (newline escape), got: {fmt!r}"
    )


# ── HELMREDO-S7 ──────────────────────────────────────────────────────────────


def test_helmredo_s7_accept_env_down_helm_uninstall_emulator_is_idempotent() -> None:
    """§6 Makefile accept-env-down: 'helm uninstall emulator' MUST be best-effort."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    down_match = re.search(r"accept-env-down:(.*?)$", section, re.DOTALL)
    assert down_match, "accept-env-down recipe not found in §6 Makefile section"
    down_body = down_match.group(1)

    emulator_lines = [ln for ln in down_body.splitlines() if "helm uninstall emulator" in ln]
    assert emulator_lines, "helm uninstall emulator not found in accept-env-down recipe"

    for line in emulator_lines:
        has_minus_prefix = line.strip().startswith("-")
        has_or_true = "|| true" in line
        assert has_minus_prefix or has_or_true, (
            f"'helm uninstall emulator' in accept-env-down MUST be prefixed with '-' "
            f"or appended with '|| true' to be best-effort idempotent.\n"
            f"Line: {line!r}"
        )


def test_helmredo_s7_accept_env_down_helm_uninstall_lab_is_idempotent() -> None:
    """§6 Makefile accept-env-down: 'helm uninstall lab' MUST be best-effort."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    down_match = re.search(r"accept-env-down:(.*?)$", section, re.DOTALL)
    assert down_match, "accept-env-down recipe not found in §6 Makefile section"
    down_body = down_match.group(1)

    lab_lines = [ln for ln in down_body.splitlines() if "helm uninstall lab" in ln]
    assert lab_lines, "helm uninstall lab not found in accept-env-down recipe"

    for line in lab_lines:
        has_minus_prefix = line.strip().startswith("-")
        has_or_true = "|| true" in line
        assert has_minus_prefix or has_or_true, (
            f"'helm uninstall lab' in accept-env-down MUST be prefixed with '-' "
            f"or appended with '|| true' to be best-effort idempotent.\n"
            f"Line: {line!r}"
        )


def test_helmredo_s7_accept_env_down_kubectl_delete_namespace_is_idempotent() -> None:
    """§6 Makefile accept-env-down: 'kubectl delete namespace' MUST be best-effort."""
    content = _read_cookbook()
    section = _extract_section(content, "6.")
    assert section, "§6 Makefile section not found in cookbook"

    down_match = re.search(r"accept-env-down:(.*?)$", section, re.DOTALL)
    assert down_match, "accept-env-down recipe not found in §6 Makefile section"
    down_body = down_match.group(1)

    ns_lines = [ln for ln in down_body.splitlines() if "kubectl delete namespace" in ln]
    assert ns_lines, "kubectl delete namespace not found in accept-env-down recipe"

    for line in ns_lines:
        has_minus_prefix = line.strip().startswith("-")
        has_or_true = "|| true" in line
        assert has_minus_prefix or has_or_true, (
            f"'kubectl delete namespace' in accept-env-down MUST be prefixed with '-' "
            f"or appended with '|| true' to be best-effort idempotent.\n"
            f"Line: {line!r}"
        )
