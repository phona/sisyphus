"""Open a GitHub issue when a REQ enters ESCALATED.

Called from `actions/escalate.py` in the "real escalate" branch (auto-resume does not
open an incident — that would be noise). The GH issue is the canonical surface humans
use to triage sisyphus failures (`gh issue list --label sisyphus:incident`); BKD tags
remain the agent-facing signal.

Disabled when `repo` (explicit kwarg, resolved by escalate from involved_repos /
settings.gh_incident_repo fallback) OR `settings.github_token` is empty — both return
None without making any HTTP request.

Failure mode: any exception or non-2xx HTTP response logs a warning and returns None.
The escalate action is the retry boundary for the failure path; we do not re-enter
GH retry from inside escalate.
"""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog

from .config import settings

log = structlog.get_logger(__name__)

_GH_API = "https://api.github.com"


def _format_body(
    *,
    req_id: str,
    reason: str,
    retry_count: int,
    intent_issue_id: str,
    failed_issue_id: str,
    project_id: str,
    state: str | None,
) -> str:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    state_line = state or "(unknown)"
    return (
        f"**REQ**: `{req_id}`\n"
        f"**Reason**: `{reason}`\n"
        f"**State at escalate**: `{state_line}`\n"
        f"**Auto-retry count**: {retry_count}\n"
        f"**BKD project**: `{project_id}`\n"
        f"**BKD intent issue**: `{intent_issue_id}`\n"
        f"**Failed sub-issue**: `{failed_issue_id}`\n"
        f"**Opened**: {now}\n\n"
        "## What to do\n"
        f"1. Inspect the BKD intent issue (`{intent_issue_id}`) for the agent session log.\n"
        "2. Decide pass / fix / escalate; if recoverable, use the admin resume endpoint:\n"
        f"   `POST /admin/req/{req_id}/resume` with body "
        '`{"action": "pass" | "fix-needed", "stage"?: "...", "reason"?: "..."}`.\n'
        "3. Close this issue once the REQ is resolved.\n"
    )


async def open_incident(
    *,
    repo: str,
    req_id: str,
    reason: str,
    retry_count: int,
    intent_issue_id: str,
    failed_issue_id: str,
    project_id: str,
    state: str | None = None,
) -> str | None:
    """POST a fresh issue to `repo`. Returns html_url or None.

    Returns None (without HTTP) when `repo` or `settings.github_token` is empty.
    Returns None on any HTTP error (logged at warning) — never raises, so the
    escalate flow can proceed even if GitHub is unreachable / per-repo PAT scope
    is missing.
    """
    repo = (repo or "").strip()
    token = settings.github_token.strip()
    if not repo or not token:
        log.debug("gh_incident.disabled", req_id=req_id,
                  has_repo=bool(repo), has_token=bool(token))
        return None

    title = f"[REQ: {req_id}] escalated — {reason}"
    body = _format_body(
        req_id=req_id, reason=reason, retry_count=retry_count,
        intent_issue_id=intent_issue_id, failed_issue_id=failed_issue_id,
        project_id=project_id, state=state,
    )
    labels = [*settings.gh_incident_labels, f"reason:{reason}"]
    payload = {"title": title, "body": body, "labels": labels}

    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {token}",
    }
    url = f"{_GH_API}/repos/{repo}/issues"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 300:
            log.warning("gh_incident.http_error",
                        req_id=req_id, repo=repo, status=resp.status_code,
                        body_tail=resp.text[-200:])
            return None
        html_url = resp.json().get("html_url")
        if not html_url:
            log.warning("gh_incident.no_html_url", req_id=req_id, repo=repo,
                        body_tail=resp.text[-200:])
            return None
        log.info("gh_incident.opened", req_id=req_id, repo=repo,
                 url=html_url, reason=reason)
        return html_url
    except Exception as e:
        log.warning("gh_incident.failed", req_id=req_id, repo=repo, error=str(e))
        return None
