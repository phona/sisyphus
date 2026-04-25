"""contract regression for REQ-clone-fallback-direct-analyze-1777119520:

multi-layer involved_repos fallback for direct analyze entry。
- _resolve_repos / resolve_repos 必须按 4-layer 顺序：ctx.intake → ctx.involved
  → tags.repo → settings.default
- _clone helper 不能从自由文本（intent_title / prompt body）反向推断 repos
  （假阳性风险高，规则强制改用显式 tag 或 settings env）
- start_analyze + start_analyze_with_finalized_intent 必须把 tags +
  settings.default_involved_repos 透传给 clone helper
- settings.default_involved_repos field 必须存在，env=SISYPHUS_DEFAULT_INVOLVED_REPOS
"""
from __future__ import annotations

from pathlib import Path

from orchestrator.actions import _clone
from orchestrator.config import Settings

_PRODUCTION_SOURCE = Path(__file__).resolve().parent.parent / "src" / "orchestrator"


def test_resolve_repos_layer_priority():
    """priority: L1 > L2 > L3 > L4。"""
    repos, src = _clone.resolve_repos(
        {"intake_finalized_intent": {"involved_repos": ["L1/x"]},
         "involved_repos": ["L2/x"]},
        tags=["repo:L3/x"], default_repos=["L4/x"],
    )
    assert repos == ["L1/x"]
    assert src == "ctx.intake_finalized_intent.involved_repos"

    repos, src = _clone.resolve_repos(
        {"involved_repos": ["L2/x"]},
        tags=["repo:L3/x"], default_repos=["L4/x"],
    )
    assert (repos, src) == (["L2/x"], "ctx.involved_repos")

    repos, src = _clone.resolve_repos(
        {}, tags=["repo:L3/x"], default_repos=["L4/x"],
    )
    assert (repos, src) == (["L3/x"], "tags.repo")

    repos, src = _clone.resolve_repos({}, tags=[], default_repos=["L4/x"])
    assert (repos, src) == (["L4/x"], "settings.default_involved_repos")

    repos, src = _clone.resolve_repos({}, tags=[], default_repos=[])
    assert (repos, src) == ([], "none")


def test_default_involved_repos_setting_exists():
    """settings 必须有 default_involved_repos field（env=SISYPHUS_DEFAULT_INVOLVED_REPOS）。"""
    fields = Settings.model_fields
    assert "default_involved_repos" in fields, (
        "Settings.default_involved_repos must exist for "
        "REQ-clone-fallback-direct-analyze-1777119520 (L4 fallback)."
    )
    f = fields["default_involved_repos"]
    # 默认空 list（多仓部署不强加默认；单仓部署用 env 显式设）
    default = (
        f.default_factory() if f.default_factory is not None else f.default
    )
    assert default == [], (
        "default_involved_repos must default to empty list — opt-in only."
    )


def test_clone_helper_does_not_parse_intent_title_or_body():
    """_clone.py 不准从 intent_title / 自由文本 fuzzy parse repo slug。

    这条 guard 故意写得严格 —— 任何未来想引入 "title slug 解析" 的人必须先
    把 REQ 当面拿来重新讨论假阳性风险（"src/orchestrator"、"M14b/M14c" 之类
    路径会被误命中）。
    """
    src = (_PRODUCTION_SOURCE / "actions" / "_clone.py").read_text(encoding="utf-8")
    forbidden_substrings = [
        "intent_title",          # 不应读 ctx.intent_title
        "get_issue",             # 不应主动 fetch BKD prompt body
        "description",           # 不应解析 issue.description
    ]
    hits = [s for s in forbidden_substrings if s in src]
    assert hits == [], (
        f"_clone.py must NOT introspect free-text fields {hits} for repo slugs "
        "(REQ-clone-fallback-direct-analyze-1777119520). Use explicit "
        "`repo:<org>/<name>` tags or settings.default_involved_repos."
    )


def test_start_analyze_actions_pass_tags_and_default_to_clone():
    """start_analyze + start_analyze_with_finalized_intent 必须透传 tags +
    settings.default_involved_repos 给 _clone helper。"""
    for action_filename in ("start_analyze.py", "start_analyze_with_finalized_intent.py"):
        path = _PRODUCTION_SOURCE / "actions" / action_filename
        text = path.read_text(encoding="utf-8")
        assert "tags=tags" in text, (
            f"{action_filename} must pass tags=tags to clone_involved_repos_into_runner "
            "(REQ-clone-fallback-direct-analyze-1777119520)."
        )
        assert "default_repos=settings.default_involved_repos" in text, (
            f"{action_filename} must pass default_repos=settings.default_involved_repos "
            "to clone_involved_repos_into_runner "
            "(REQ-clone-fallback-direct-analyze-1777119520)."
        )


def test_repo_tag_extraction_validates_slug():
    """非法 slug 必须拒绝，不准把 `repo:invalid X` 当 repo 名扔给 helper。"""
    out = _clone._extract_repo_tags([
        "repo:phona/sisyphus",
        "repo:bad slug",
        "repo:no-slash",
        "repo:/empty-org",
        "repo:org/",
        "repo:phona/repo-with.dots_and-dash",
    ])
    assert out == ["phona/sisyphus", "phona/repo-with.dots_and-dash"]
