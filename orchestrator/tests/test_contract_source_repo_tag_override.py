"""contract regression for REQ-fix-orch-source-repo-tag-1777824479 (closes #362):

per-REQ source-repo tag override. The `source-repo:<org>/<name>` BKD tag
is L0 (top priority) of the multi-layer involved_repos fallback — it
wins over intake / ctx / `repo:` / settings.default_involved_repos.

Use case: helm `default_involved_repos = [phona/sisyphus]` (single-repo
self-dogfood deployment) but a one-off REQ targets a different repo
(e.g., `ZonEaseTech/ttpos-flutter`). Without this override the orch
clones the helm-default and validates the REQ's `base:` against the
wrong repo — observed 5/3 Phase D dogfood as repeated escalate of every
cross-repo REQ until operator gave up and used `--no-intent` direct
BKD mode (losing orch pipeline dogfood value).
"""
from __future__ import annotations

from orchestrator.actions import _clone


def test_source_repo_tag_overrides_helm_default_repro_362():
    """实证 case from phona/sisyphus#362: helm default = phona/sisyphus,
    REQ uses `source-repo:ZonEaseTech/ttpos-flutter`, expect ttpos-flutter
    cloned (not phona/sisyphus).
    """
    repos, src = _clone.resolve_repos(
        {"intent_title": "fix something in ttpos-flutter"},
        tags=["intent:execute", "source-repo:ZonEaseTech/ttpos-flutter"],
        default_repos=["phona/sisyphus"],
    )
    assert repos == ["ZonEaseTech/ttpos-flutter"]
    assert src == "tags.source-repo"
    assert "phona/sisyphus" not in repos


def test_source_repo_tag_overrides_intake_finalized_intent():
    """L0 must win over L1 (intake finalized intent) — explicit per-REQ
    tag should override even what intake-agent figured out, because the
    user manually pinned a different source repo at REQ creation time.
    """
    repos, src = _clone.resolve_repos(
        {"intake_finalized_intent": {"involved_repos": ["other/intake-pick"]}},
        tags=["source-repo:explicit/override"],
        default_repos=[],
    )
    assert repos == ["explicit/override"]
    assert src == "tags.source-repo"


def test_multiple_source_repo_tags_merge_dedup_preserve_order():
    """Multiple `source-repo:` tags merge to a list, dedup by first
    occurrence, preserve order, drop invalid slugs.
    """
    out = _clone._extract_source_repo_tags([
        "intent:execute",
        "source-repo:phona/sisyphus",
        "source-repo:ZonEaseTech/ttpos-flutter",
        "source-repo:phona/sisyphus",            # dup -> drop
        "source-repo:invalid org/name",          # space in slug -> drop
        "source-repo:/missing-org",              # empty org -> drop
        "source-repo:no-slash-here",             # no slash -> drop
        "source-repo:phona/repo-with.dots_and-dash",
    ])
    assert out == [
        "phona/sisyphus",
        "ZonEaseTech/ttpos-flutter",
        "phona/repo-with.dots_and-dash",
    ]


def test_source_repo_extractor_isolated_from_repo_extractor():
    """`source-repo:foo/bar` MUST NOT also be picked up by
    `_extract_repo_tags`, and vice versa. Otherwise L0 + L3 would
    double-count and `resolve_repos` priority semantics would break.
    """
    tags = ["source-repo:A/x", "repo:B/y"]
    assert _clone._extract_source_repo_tags(tags) == ["A/x"]
    assert _clone._extract_repo_tags(tags) == ["B/y"]

    # End-to-end via resolve_repos: L0 hits, L3 is shadowed.
    repos, src = _clone.resolve_repos({}, tags=tags, default_repos=[])
    assert repos == ["A/x"]
    assert src == "tags.source-repo"


def test_source_repo_extractor_tolerates_non_string_tags():
    """非字符串 tag 必须不报错 —— 跟 `_extract_repo_tags` 同等鲁棒。"""
    out = _clone._extract_source_repo_tags(
        ["source-repo:phona/x", 42, None, "source-repo:phona/y"]
    )
    assert out == ["phona/x", "phona/y"]


def test_source_repo_no_tag_falls_through_to_lower_layers():
    """No `source-repo:` tag → resolve_repos must fall through to
    L1/L2/L3/L4 unchanged (the new layer is purely additive on top).
    """
    # Falls through to L4 (settings default) when nothing else set
    repos, src = _clone.resolve_repos(
        {}, tags=["intent:execute"], default_repos=["phona/sisyphus"],
    )
    assert (repos, src) == (["phona/sisyphus"], "settings.default_involved_repos")

    # Falls through to L3 (`repo:` tag) when ctx empty and source-repo absent
    repos, src = _clone.resolve_repos(
        {}, tags=["repo:phona/sisyphus"], default_repos=["unused/x"],
    )
    assert (repos, src) == (["phona/sisyphus"], "tags.repo")
