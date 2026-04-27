"""Cross-link URL helpers (REQ-pr-issue-traceability-1777218612).

Three concerns, kept separate so each is testable in isolation:

1. ``bkd_issue_url(project_id, issue_id)`` — render a clickable BKD frontend
   URL for an issue id, with two-tier base resolution
   (``settings.bkd_frontend_url`` override → ``settings.bkd_base_url`` minus
   trailing ``/api``).
2. ``format_pr_links_md(pr_urls)`` — render a sorted list of markdown bullet
   strings for ``{repo: html_url}`` dict.
3. ``discover_pr_urls(repos, branch)`` — best-effort GH REST probe to
   resolve ``feat/<REQ>`` PR html_urls per repo, used by
   ``actions.create_pr_ci_watch`` to populate ``ctx.pr_urls``.

All three return safe sentinels (``None`` / ``[]`` / ``{}``) on bad input or
network error — they are *cosmetic* helpers, never on the critical path.
"""
from __future__ import annotations

import re

import httpx
import structlog

from .config import settings

log = structlog.get_logger(__name__)

_GH_API = "https://api.github.com"
_PR_NUMBER_RE = re.compile(r"/pull/(\d+)")
_DISCOVER_TIMEOUT_SEC = 15.0


def _resolve_frontend_base() -> str | None:
    """Return the BKD frontend base URL (no trailing slash) or None.

    Priority: ``settings.bkd_frontend_url`` (explicit override) >
    ``settings.bkd_base_url`` minus a trailing ``/api`` segment.
    """
    override = (settings.bkd_frontend_url or "").strip()
    if override:
        base = override.rstrip("/")
    else:
        base = (settings.bkd_base_url or "").strip().rstrip("/")
        if base.endswith("/api"):
            base = base[: -len("/api")]
    if not base:
        return None
    if "://" not in base:
        # Reject paths without scheme — we never want to inject a relative URL
        # into an issue body.
        return None
    return base


def bkd_issue_url(project_id: str | None, issue_id: str | None) -> str | None:
    """Render a clickable BKD frontend URL for ``(project_id, issue_id)``.

    Returns None when either id is empty or no frontend base resolves.
    """
    if not project_id or not issue_id:
        return None
    base = _resolve_frontend_base()
    if base is None:
        return None
    return f"{base}/projects/{project_id}/issues/{issue_id}"


def format_pr_links_md(pr_urls: object) -> list[str]:
    """Render markdown bullet links sorted by repo, one per ``(repo, url)``.

    - dict keys / values not coercible to ``str`` are skipped.
    - URL without a ``/pull/<n>`` segment falls back to ``[<repo>](<url>)``.
    - Non-dict / empty / None input → ``[]``.
    """
    if not isinstance(pr_urls, dict) or not pr_urls:
        return []
    bullets: list[str] = []
    for repo in sorted(pr_urls):
        url = pr_urls[repo]
        if not isinstance(repo, str) or not isinstance(url, str) or not url:
            continue
        m = _PR_NUMBER_RE.search(url)
        label = f"{repo}#{m.group(1)}" if m else repo
        bullets.append(f"- [{label}]({url})")
    return bullets


def format_pr_links_inline(pr_urls: object) -> str:
    """Comma-separated single-line variant of ``format_pr_links_md``.

    Used by ``gh_incident._format_body`` so the issue body stays compact.
    Returns ``""`` when the dict is empty / None.
    """
    bullets = format_pr_links_md(pr_urls)
    # Strip the leading "- " on each bullet for inline rendering.
    return ", ".join(b[2:] if b.startswith("- ") else b for b in bullets)


async def discover_pr_urls(
    repos: list[str] | None,
    branch: str,
    *,
    timeout_sec: float = _DISCOVER_TIMEOUT_SEC,
) -> dict[str, str]:
    """Probe GH REST for ``feat/<REQ>`` PR html_urls per repo.

    Best-effort: any HTTP error / empty list yields a partial result. Caller
    persists the dict only when non-empty (see
    ``actions.create_pr_ci_watch.create_pr_ci_watch``).

    Mirrors the lookup pattern used by ``checkers.pr_ci_watch._get_pr_info``
    so we are consistent about which PR is "the" PR for a branch (open
    first, falling back to merged/closed).
    """
    repo_list = [r for r in (repos or []) if r and "/" in r]
    if not repo_list:
        return {}
    token = (settings.github_token or "").strip()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out: dict[str, str] = {}
    async with httpx.AsyncClient(
        base_url=_GH_API, headers=headers, timeout=timeout_sec,
    ) as client:
        for repo in repo_list:
            owner, _ = repo.split("/", 1)
            url = await _lookup_pr_html_url(client, repo, owner, branch)
            if url:
                out[repo] = url
    return out


async def _lookup_pr_html_url(
    client: httpx.AsyncClient, repo: str, owner: str, branch: str,
) -> str | None:
    """Return html_url of the head=owner:branch PR (any state), or None."""
    for state in ("open", "all"):
        try:
            r = await client.get(
                f"/repos/{repo}/pulls",
                params={"head": f"{owner}:{branch}", "state": state, "per_page": 5},
            )
            r.raise_for_status()
            pulls = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("links.discover_pr_urls.api_error",
                        repo=repo, branch=branch, state=state, error=str(e))
            return None
        if pulls:
            html_url = pulls[0].get("html_url")
            if isinstance(html_url, str) and html_url:
                return html_url
    return None
