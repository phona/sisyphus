"""Builder for the ``status_block`` context dict (REQ-ux-status-block-1777257283).

Shared by ``start_intake`` / ``start_analyze`` /
``start_analyze_with_finalized_intent``: each callsite hands over whatever
identity / location facts it has at dispatch time, the helper normalises
empty values to ``None`` so the partial's row guards drop optional rows
cleanly, and ``pr_urls`` is pre-rendered through ``links.format_pr_links_inline``
so the Jinja2 template only emits a flat string.
"""
from __future__ import annotations

from .. import links


def build_status_block_ctx(
    *,
    req_id: str,
    stage: str,
    bkd_intent_issue_url: str | None = None,
    cloned_repos: list[str] | None = None,
    pr_urls: dict[str, str] | None = None,
) -> dict:
    """Assemble the dict consumed by ``_shared/status_block.md.j2``."""
    return {
        "req_id": req_id,
        "stage": stage,
        "bkd_intent_issue_url": (bkd_intent_issue_url or None),
        "cloned_repos": (list(cloned_repos) if cloned_repos else None),
        "pr_links_inline": (links.format_pr_links_inline(pr_urls) or None),
    }
