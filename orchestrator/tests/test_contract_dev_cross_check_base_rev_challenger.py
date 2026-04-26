"""Challenger contract tests for REQ-fix-base-rev-default-branch-1777214183.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-fix-base-rev-default-branch-1777214183/specs/dev-cross-check-base-rev/spec.md

Scenarios covered:
  DCC-S1  BASE_REV 用仓 origin/HEAD 解析的默认分支（release 仓）
  DCC-S2  origin/HEAD 缺失时退到静态 main 链（[ -n ] gate 存在）
  DCC-S3  默认分支既不是 main 也不是 master/develop/dev 但 origin/HEAD 命中
  DCC-S4  全 miss 退空字符串，ci-lint 退化全量扫
  DCC-S5  静态链顺序固定 main → master → develop → dev

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import re

# ─── Black-box entry point ────────────────────────────────────────────────────


def _get_build_cmd(req_id: str = "REQ-test-challenger") -> str:
    """Call dev_cross_check._build_cmd() and return the generated shell string.

    This is the sole entry point for challenger tests — black-box call only.
    The function is a module-level callable as specified in the proposal.
    """
    from orchestrator.checkers import dev_cross_check as dcc

    return dcc._build_cmd(req_id)


# ─── DCC-S1 ──────────────────────────────────────────────────────────────────


def test_dcc_s1_dynamic_default_branch_resolved_from_origin_head() -> None:
    """DCC-S1: shell MUST resolve default_branch from origin/HEAD symbolic ref.

    Spec: GIVEN a cloned repo where git symbolic-ref resolves to refs/remotes/origin/release,
    THEN the shell sets default_branch=release and BASE_REV=$(git merge-base HEAD origin/release).

    Black-box: verify the generated shell uses git symbolic-ref to read refs/remotes/origin/HEAD,
    strips the 'origin/' prefix via sed, and uses the result variable in a merge-base call.
    This ensures any non-main default branch (e.g. 'release', 'trunk') is dynamically resolved.
    """
    cmd = _get_build_cmd()

    # Must read the origin/HEAD symbolic ref to discover the repo's actual default branch
    assert "git symbolic-ref" in cmd and "refs/remotes/origin/HEAD" in cmd, (
        "DCC-S1: shell MUST contain 'git symbolic-ref ... refs/remotes/origin/HEAD' "
        "to resolve the repo's actual default branch. "
        f"Relevant cmd excerpt: {cmd[:600]!r}"
    )

    # Must strip the 'origin/' prefix so default_branch becomes e.g. 'release' not 'origin/release'
    assert "sed" in cmd and "origin/" in cmd, (
        "DCC-S1: shell MUST strip 'origin/' prefix from symbolic-ref output via sed "
        "so that default_branch holds the bare branch name. "
        f"Relevant cmd excerpt: {cmd[:600]!r}"
    )

    # Must use the resolved $default_branch variable in a merge-base call (not a hardcoded name)
    assert re.search(r"git merge-base HEAD ['\"]?origin/\$default_branch['\"]?", cmd), (
        "DCC-S1: shell MUST call 'git merge-base HEAD origin/$default_branch' using the "
        "dynamically resolved variable so that non-main branches (e.g. release, trunk) work. "
        f"Relevant cmd excerpt: {cmd[:600]!r}"
    )


# ─── DCC-S2 ──────────────────────────────────────────────────────────────────


def test_dcc_s2_empty_default_branch_gated_before_dynamic_merge_base() -> None:
    """DCC-S2: shell MUST gate the dynamic merge-base with [ -n "$default_branch" ].

    Spec: GIVEN a cloned repo where git symbolic-ref exits non-zero (mirror clone),
    THEN default_branch is empty; the [ -n "$default_branch" ] guard MUST prevent
    'git merge-base HEAD origin/' (ambiguous) from being invoked; falls to origin/main.

    Black-box: the gate must be present in the shell AND must appear before origin/main fallback.
    """
    cmd = _get_build_cmd()

    # The gate MUST be present to prevent empty $default_branch from hitting merge-base
    has_gate = '[ -n "$default_branch" ]' in cmd
    assert has_gate, (
        "DCC-S2: shell MUST contain '[ -n \"$default_branch\" ]' guard to prevent "
        "'git merge-base HEAD origin/' being called with an empty variable — "
        "which would be an ambiguous git reference. "
        f"Relevant cmd excerpt: {cmd[:600]!r}"
    )

    # The gate must appear BEFORE the static 'origin/main' fallback so the ordering makes sense
    gate_pos = cmd.find('[ -n "$default_branch" ]')
    main_pos = cmd.find("origin/main")
    assert main_pos != -1, (
        "DCC-S2: 'origin/main' MUST appear in the static fallback chain"
    )
    assert gate_pos < main_pos, (
        f"DCC-S2: '[ -n \"$default_branch\" ]' gate (pos {gate_pos}) MUST appear "
        f"before 'origin/main' fallback (pos {main_pos}) in the shell command"
    )


# ─── DCC-S3 ──────────────────────────────────────────────────────────────────


def test_dcc_s3_any_branch_name_handled_via_variable_substitution() -> None:
    """DCC-S3: dynamic merge-base uses $default_branch variable, not hardcoded names.

    Spec: GIVEN a repo whose default branch is 'trunk' and origin/HEAD → origin/trunk,
    THEN default_branch=trunk, git merge-base HEAD origin/trunk succeeds;
    the static chain is NOT reached (short-circuit).

    Black-box: verify the shell uses a variable (not a fixed list of known names)
    so that ANY branch name returned by git symbolic-ref is dynamically substituted.
    """
    cmd = _get_build_cmd()

    # The dynamic merge-base MUST use the $default_branch variable
    dynamic_pattern = re.compile(r"git merge-base HEAD ['\"]?origin/\$default_branch['\"]?")
    assert dynamic_pattern.search(cmd), (
        "DCC-S3: dynamic merge-base MUST use '$default_branch' variable substitution "
        "(not hardcoded branch names like 'release' or 'trunk') so that ANY branch name "
        "returned by git symbolic-ref is handled correctly. "
        f"Relevant cmd excerpt: {cmd[:600]!r}"
    )

    # Confirm the dynamic attempt uses short-circuit logic (AND/&&) so it short-circuits
    # on success and does NOT proceed to the static chain
    assert "&&" in cmd or "and" in cmd.lower(), (
        "DCC-S3: dynamic merge-base MUST be connected via '&&' or equivalent short-circuit "
        "logic so that a successful dynamic merge-base prevents the static chain from running. "
        f"Relevant cmd excerpt: {cmd[:600]!r}"
    )


# ─── DCC-S4 ──────────────────────────────────────────────────────────────────


def test_dcc_s4_full_miss_produces_empty_string_not_error() -> None:
    """DCC-S4: when all merge-base attempts fail, BASE_REV MUST be empty string.

    Spec: GIVEN a repo with no origin/HEAD and no origin/main/master/develop/dev,
    THEN BASE_REV="" → make ci-lint receives empty string → full scan (degraded, not an error).

    Black-box: the shell MUST have a final '|| echo ""' (or equivalent) fallback
    at the end of the base_rev computation chain.
    """
    cmd = _get_build_cmd()

    # Must have a final empty-string fallback so non-zero merge-base exits don't fail the runner
    has_empty_fallback = '|| echo ""' in cmd or "|| echo ''" in cmd
    assert has_empty_fallback, (
        "DCC-S4: shell MUST end the BASE_REV computation chain with '|| echo \"\"' "
        "to ensure a full-miss yields empty string (not a non-zero exit). "
        "An empty BASE_REV tells ci-lint to perform a full scan (known degraded path). "
        f"Relevant cmd excerpt: {cmd[:800]!r}"
    )

    # BASE_REV must be passed to make ci-lint as an env variable
    assert "BASE_REV=" in cmd, (
        "DCC-S4: 'BASE_REV=' MUST appear in the shell to pass the resolved value "
        "(possibly empty) to make ci-lint. "
        f"Relevant cmd excerpt: {cmd[:600]!r}"
    )


# ─── DCC-S5 ──────────────────────────────────────────────────────────────────


def test_dcc_s5_static_chain_order_main_master_develop_dev() -> None:
    """DCC-S5: static fallback chain MUST appear in exactly this order: main → master → develop → dev.

    Spec: 'git merge-base HEAD origin/main' MUST appear before 'origin/master';
    'origin/master' before 'origin/develop'; 'origin/develop' before 'origin/dev' (standalone).

    Black-box: parse the _build_cmd output and assert position ordering.
    """
    cmd = _get_build_cmd()

    pos_main = cmd.find("origin/main")
    pos_master = cmd.find("origin/master")
    pos_develop = cmd.find("origin/develop")

    # Find standalone 'origin/dev' — NOT 'origin/develop'
    dev_only_matches = [m.start() for m in re.finditer(r"origin/dev(?!elop)", cmd)]

    assert pos_main != -1, "DCC-S5: 'origin/main' MUST appear in static fallback chain"
    assert pos_master != -1, "DCC-S5: 'origin/master' MUST appear in static fallback chain"
    assert pos_develop != -1, "DCC-S5: 'origin/develop' MUST appear in static fallback chain"
    assert dev_only_matches, (
        "DCC-S5: 'origin/dev' (not 'origin/develop') MUST appear in static fallback chain"
    )
    pos_dev = dev_only_matches[-1]

    assert pos_main < pos_master, (
        f"DCC-S5: 'origin/main' (pos={pos_main}) MUST appear before "
        f"'origin/master' (pos={pos_master}) in static fallback chain"
    )
    assert pos_master < pos_develop, (
        f"DCC-S5: 'origin/master' (pos={pos_master}) MUST appear before "
        f"'origin/develop' (pos={pos_develop}) in static fallback chain"
    )
    assert pos_develop < pos_dev, (
        f"DCC-S5: 'origin/develop' (pos={pos_develop}) MUST appear before "
        f"'origin/dev' standalone (pos={pos_dev}) in static fallback chain"
    )
