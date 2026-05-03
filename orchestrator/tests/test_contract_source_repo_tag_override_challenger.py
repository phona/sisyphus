"""Challenger contract tests for REQ-fix-orch-source-repo-tag-1777824479.

Black-box contracts derived **exclusively** from:

  openspec/changes/REQ-fix-orch-source-repo-tag-1777824479/specs/
    multi-layer-involved-repos-fallback/spec.md

Scenarios covered:

  MLIRF-S1  tags.source-repo wins over every other layer (L0 priority)
  SRTO-S1   source-repo: overrides helm default (closes #362) — and
            clone_involved_repos_into_runner clones the override slug only
  SRTO-S2   source-repo: wins even when intake_finalized_intent declares
            a different repo
  SRTO-S3   multiple source-repo: tags merge to a deduped, ordered list
            (invalid slugs dropped silently — but emit a warning event)
  SRTO-S4   source-repo and repo extractors are independent (no
            double-counting; resolve_repos picks L0 before L3 falls through)

Plus a few derived properties spelled out as MUSTs in the new ADDED
Requirement that ride along the same scenarios:

  * `_extract_source_repo_tags` MUST tolerate non-string entries
    without raising
  * `_extract_source_repo_tags` MUST log a warning event named
    `clone.invalid_source_repo_tag` when a `source-repo:` tag has an
    invalid slug

Dev MUST NOT modify these tests to make them pass — fix the
implementation instead.  If a test is genuinely wrong, escalate to
spec_fixer; do not patch around it in code.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from orchestrator.actions import _clone

# ─── helpers ─────────────────────────────────────────────────────────────────


class _FakeExecResult:
    def __init__(
        self,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        duration_sec: float = 0.0,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_sec = duration_sec


class _CapturingRC:
    """Records every exec_in_runner invocation for clone-command assertions."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def exec_in_runner(self, req_id: str, cmd: str, timeout_sec: int = 600):
        self.calls.append({"req_id": req_id, "cmd": cmd, "timeout": timeout_sec})
        return _FakeExecResult(exit_code=0, stdout="ok")


def _resolve(ctx, tags, default_repos):
    return _clone.resolve_repos(ctx, tags=tags, default_repos=default_repos)


# ─── canonical seam: extractor MUST exist ────────────────────────────────────


def test_extract_source_repo_tags_symbol_exists():
    """The new ADDED Requirement mandates `_extract_source_repo_tags` as the
    canonical extractor for `source-repo:` tags. Missing the symbol is itself
    a contract violation — surface it as a test failure rather than a
    collection error so the agent gets a readable signal."""
    fn = getattr(_clone, "_extract_source_repo_tags", None)
    assert callable(fn), (
        "Spec mandates orchestrator.actions._clone._extract_source_repo_tags "
        "as the canonical extractor for `source-repo:<org>/<name>` BKD intent "
        "issue tags. Function MUST be importable from the _clone module."
    )


# ─── MLIRF-S1: L0 priority over every other layer ───────────────────────────


def test_mlirf_s1_source_repo_tag_wins_over_every_other_layer():
    """MLIRF-S1: GIVEN intake_finalized_intent=['L1/x'], involved=['L2/x'],
    tags=['source-repo:L0/x','repo:L3/x'], default=['L4/x']; THEN result is
    (['L0/x'], 'tags.source-repo')."""
    repos, src = _resolve(
        ctx={
            "intake_finalized_intent": {"involved_repos": ["L1/x"]},
            "involved_repos": ["L2/x"],
        },
        tags=["source-repo:L0/x", "repo:L3/x"],
        default_repos=["L4/x"],
    )
    assert (repos, src) == (["L0/x"], "tags.source-repo"), (
        "L0 (tags.source-repo) MUST win over L1/L2/L3/L4 (MLIRF-S1)."
    )


# ─── SRTO-S1: closes #362 — override beats helm default; clone uses override ─


def test_srto_s1_source_repo_overrides_helm_default_resolve():
    """SRTO-S1 (resolve part): the helm-default sisyphus self-dogfood case
    where direct-analyze REQ carries `source-repo:ZonEaseTech/ttpos-flutter`
    MUST resolve to the ttpos slug and never fall through to L4."""
    repos, src = _resolve(
        ctx={"intent_title": "fix something in ttpos-flutter"},
        tags=["intent:analyze", "source-repo:ZonEaseTech/ttpos-flutter"],
        default_repos=["phona/sisyphus"],
    )
    assert repos == ["ZonEaseTech/ttpos-flutter"], (
        "SRTO-S1: source-repo override MUST resolve to the per-REQ slug, "
        "not the helm default."
    )
    assert src == "tags.source-repo", (
        "SRTO-S1: source_label MUST identify the L0 layer for observability."
    )
    assert "phona/sisyphus" not in repos, (
        "SRTO-S1: the L4 helm default MUST NOT appear once L0 hits."
    )


@pytest.mark.asyncio
async def test_srto_s1_clone_helper_clones_override_only_not_helm_default():
    """SRTO-S1 (clone-helper part): subsequent
    `clone_involved_repos_into_runner` invocations on the same inputs MUST
    issue exactly one clone for the override repo and zero clones of the
    helm default. The black-box assertion is on the shell command shipped
    to the runner pod."""
    captured = _CapturingRC()
    with patch(
        "orchestrator.actions._clone.k8s_runner.get_controller",
        return_value=captured,
    ):
        repos, exit_code = await _clone.clone_involved_repos_into_runner(
            "REQ-fix-orch-source-repo-tag-1777824479",
            {"involved_repos": ["ZonEaseTech/ttpos-flutter"]},
        )

    assert repos == ["ZonEaseTech/ttpos-flutter"]
    assert exit_code is None
    assert len(captured.calls) == 1, (
        "SRTO-S1: clone helper MUST issue exactly one runner-side clone "
        "command for the resolved repo list."
    )
    cmd = captured.calls[0]["cmd"]
    assert "ZonEaseTech/ttpos-flutter" in cmd, (
        "SRTO-S1: the runner-side clone command MUST mention the override "
        "slug ZonEaseTech/ttpos-flutter."
    )
    assert "phona/sisyphus" not in cmd, (
        "SRTO-S1: the runner-side clone command MUST NOT mention the helm "
        "default phona/sisyphus once the override is in effect."
    )


# ─── SRTO-S2: source-repo wins even over intake_finalized_intent ─────────────


def test_srto_s2_source_repo_wins_over_intake_finalized_intent():
    """SRTO-S2: explicit per-REQ tag overrides intake-agent's pick."""
    repos, src = _resolve(
        ctx={"intake_finalized_intent": {"involved_repos": ["other/intake-pick"]}},
        tags=["source-repo:explicit/override"],
        default_repos=[],
    )
    assert (repos, src) == (["explicit/override"], "tags.source-repo"), (
        "SRTO-S2: source-repo: tag MUST override intake_finalized_intent."
    )
    assert "other/intake-pick" not in repos, (
        "SRTO-S2: intake's pick MUST NOT appear once explicit override exists."
    )


# ─── SRTO-S3: dedup, order, invalid drop ─────────────────────────────────────


def test_srto_s3_multiple_source_repo_tags_dedup_and_drop_invalid():
    """SRTO-S3: extractor MUST return deduped, first-occurrence-ordered
    list with invalid slugs dropped. Exact spec input/output."""
    out = _clone._extract_source_repo_tags(
        [
            "intent:analyze",
            "source-repo:phona/sisyphus",
            "source-repo:ZonEaseTech/ttpos-flutter",
            "source-repo:phona/sisyphus",  # duplicate
            "source-repo:invalid org/name",  # space → invalid slug
            "source-repo:/missing-org",  # empty org
            "source-repo:phona/repo-with.dots_and-dash",
        ]
    )
    assert out == [
        "phona/sisyphus",
        "ZonEaseTech/ttpos-flutter",
        "phona/repo-with.dots_and-dash",
    ], (
        "SRTO-S3: extractor MUST preserve first occurrence, drop "
        "duplicates and invalid slugs, and yield exactly the three "
        "valid slugs in the order they first appear."
    )


def test_srto_s3_extractor_tolerates_non_string_entries():
    """ADDED Requirement: extractor MUST tolerate non-string entries in
    the iterable without raising. This guards against accidental shape
    drift in BKD tag payloads (e.g. None / int slipping through)."""
    out = _clone._extract_source_repo_tags(
        [None, 42, {"k": "v"}, "source-repo:phona/sisyphus", b"source-repo:bytes/x"]
    )
    assert out == ["phona/sisyphus"], (
        "Extractor MUST silently ignore non-string entries and only emit "
        "valid slugs from str-typed `source-repo:` tags."
    )


def test_srto_s3_extractor_emits_warning_for_invalid_slug(capsys):
    """ADDED Requirement: extractor MUST log a `clone.invalid_source_repo_tag`
    warning when a `source-repo:` tag has an invalid slug, so operators
    can spot typos. The exact event name is part of the contract.

    structlog writes to stdout (caplog does not capture it), so we read
    the captured stdout/stderr via capsys.
    """
    out = _clone._extract_source_repo_tags(
        [
            "source-repo:phona/sisyphus",  # valid → no warning
            "source-repo:invalid org/name",  # invalid → warning
            "source-repo:/missing-org",  # invalid → warning
        ]
    )
    assert out == ["phona/sisyphus"]
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "clone.invalid_source_repo_tag" in output, (
        "Extractor MUST emit a warning event named "
        "`clone.invalid_source_repo_tag` for each invalid slug "
        "(operators rely on this event name for typo alerts).\n"
        f"Captured log output was:\n{output}"
    )


# ─── SRTO-S4: source-repo / repo extractors are independent ─────────────────


def test_srto_s4_extractors_are_independent_no_double_counting():
    """SRTO-S4: source-repo: and repo: extractors MUST NOT cross-pollute.
    A `source-repo:A/x` tag MUST NOT count as a `repo:` slug, and a
    `repo:B/y` tag MUST NOT count as a `source-repo:` slug."""
    tags = ["source-repo:A/x", "repo:B/y"]

    src_only = _clone._extract_source_repo_tags(tags)
    repo_only = _clone._extract_repo_tags(tags)

    assert src_only == ["A/x"], (
        "SRTO-S4: _extract_source_repo_tags MUST extract only A/x."
    )
    assert "B/y" not in src_only, (
        "SRTO-S4: _extract_source_repo_tags MUST NOT include the repo: tag."
    )
    assert repo_only == ["B/y"], (
        "SRTO-S4: _extract_repo_tags MUST extract only B/y."
    )
    assert "A/x" not in repo_only, (
        "SRTO-S4: _extract_repo_tags MUST NOT include the source-repo: tag "
        "(prefix `repo:` does not match `source-repo:`)."
    )


def test_srto_s4_resolve_repos_l0_hits_before_l3_falls_through():
    """SRTO-S4 (resolve part): with both layers populated and ctx empty,
    L0 (tags.source-repo) MUST be the layer that matches. The repo: L3
    tag MUST NOT contribute because the resolver picks the first non-empty
    layer in priority order — it does not concatenate."""
    repos, src = _resolve(
        ctx={},
        tags=["source-repo:A/x", "repo:B/y"],
        default_repos=[],
    )
    assert (repos, src) == (["A/x"], "tags.source-repo"), (
        "SRTO-S4: resolve_repos MUST stop at L0 once it has a non-empty "
        "extracted list — the L3 repo: tag MUST NOT appear in the result."
    )
    assert "B/y" not in repos, (
        "SRTO-S4: layers do NOT concatenate; only the first non-empty wins."
    )


# ─── Empty-everything safety net (cross-check the new layer doesn't break it) ─


def test_all_layers_empty_with_only_invalid_source_repo_tags_returns_none():
    """Defensive corollary of SRTO-S3 + the L0 layer addition: if the only
    `source-repo:` tags present are invalid, L0 MUST be considered empty
    and the resolver MUST fall through to subsequent layers. With every
    layer empty the result MUST be `([], 'none')` per the existing
    Requirement (last paragraph of the MODIFIED block)."""
    repos, src = _resolve(
        ctx={},
        tags=["source-repo:invalid org/name", "source-repo:/missing-org"],
        default_repos=[],
    )
    assert (repos, src) == ([], "none"), (
        "Invalid-only source-repo: tags MUST NOT count as L0 hits, and with "
        "every layer empty the result MUST be ([], 'none')."
    )
