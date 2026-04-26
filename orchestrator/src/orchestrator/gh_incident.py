"""GitHub-side incident surfacing for escalated REQs.

Two paths, both called from `actions/escalate.py` in the real-escalate branch
(auto-resume never opens any GH artifact — that would be noise):

1. `find_pr_for_branch` + `comment_on_pr` — preferred. Posts the incident
   metadata as a comment on the existing `feat/{REQ}` PR. Keeps the "1 REQ =
   1 PR" invariant: humans triage on the same PR they'd review the diff on.

2. `open_incident` — legacy fallback. Creates a fresh GitHub issue when no PR
   exists for the REQ on this repo (escalations during INTAKING / early
   ANALYZING that fire pre-push, or `gh_incident_repo` triage-inbox
   deployments). The issue is the canonical surface humans use to triage in
   that fallback case (`gh issue list --label sisyphus:incident`); BKD tags
   remain the agent-facing signal regardless of which path landed.

All three functions are disabled (return `None` without HTTP) when their
required input is missing — `repo`/`pr_number`/`branch` empty or
`settings.github_token` empty. Failure mode is uniform: any exception or non-2xx
HTTP response logs a warning and returns `None`. The escalate action is the
retry boundary for the failure path; we do not re-enter GH retry from inside
the helpers.
"""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog

from .config import settings

log = structlog.get_logger(__name__)

_GH_API = "https://api.github.com"
_TIMEOUT = 15.0


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
        "3. Close this incident once the REQ is resolved.\n"
    )


def _auth_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {settings.github_token.strip()}",
    }


async def find_pr_for_branch(*, repo: str, branch: str) -> int | None:
    """Look up the PR number for a (repo, branch) pair.

    Queries `GET /repos/{repo}/pulls?head={owner}:{branch}&state=all&per_page=5`
    and returns the first PR's `number`, or `None` when the response is empty.

    Accepts any state (open / closed / merged) — escalations can land after a
    PR is merged (e.g., archive crash) and a comment is still useful audit.

    Disabled (returns `None` without HTTP) when `repo` or
    `settings.github_token` is empty. Returns `None` on any HTTP error or when
    the response shape is unexpected.
    """
    repo = (repo or "").strip()
    branch = (branch or "").strip()
    token = settings.github_token.strip()
    if not repo or not branch or not token or "/" not in repo:
        log.debug("gh_incident.find_pr.disabled", repo=repo, branch=branch,
                  has_token=bool(token))
        return None
    owner = repo.split("/", 1)[0]
    url = f"{_GH_API}/repos/{repo}/pulls"
    params = {
        "head": f"{owner}:{branch}",
        "state": "all",
        "per_page": 5,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=_auth_headers(), params=params)
        if resp.status_code >= 300:
            log.warning("gh_incident.find_pr.http_error", repo=repo, branch=branch,
                        status=resp.status_code, body_tail=resp.text[-200:])
            return None
        pulls = resp.json()
        if not isinstance(pulls, list) or not pulls:
            return None
        number = pulls[0].get("number")
        if not isinstance(number, int) or number <= 0:
            log.warning("gh_incident.find_pr.bad_number", repo=repo, branch=branch,
                        body_tail=resp.text[-200:])
            return None
        return number
    except Exception as e:
        log.warning("gh_incident.find_pr.failed", repo=repo, branch=branch, error=str(e))
        return None


async def comment_on_pr(
    *,
    repo: str,
    pr_number: int,
    req_id: str,
    reason: str,
    retry_count: int,
    intent_issue_id: str,
    failed_issue_id: str,
    project_id: str,
    state: str | None = None,
) -> str | None:
    """POST a comment to `repo`'s PR `pr_number`. Returns html_url or None.

    Endpoint: `POST /repos/{repo}/issues/{pr_number}/comments` (PR comments are
    issue-level comments — the PR is an issue subtype). Body shape mirrors the
    `_format_body` metadata `open_incident` writes.

    Returns None (without HTTP) when `repo` / `pr_number` / `settings.github_token`
    is missing or invalid. Returns None on any HTTP error — never raises, so
    the escalate flow can proceed even if GitHub is unreachable / per-repo PAT
    scope is missing.
    """
    repo = (repo or "").strip()
    token = settings.github_token.strip()
    if not repo or not token or pr_number <= 0:
        log.debug("gh_incident.comment.disabled", req_id=req_id, repo=repo,
                  pr_number=pr_number, has_token=bool(token))
        return None

    body = _format_body(
        req_id=req_id, reason=reason, retry_count=retry_count,
        intent_issue_id=intent_issue_id, failed_issue_id=failed_issue_id,
        project_id=project_id, state=state,
    )
    payload = {"body": body}
    url = f"{_GH_API}/repos/{repo}/issues/{pr_number}/comments"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=_auth_headers(), json=payload)
        if resp.status_code >= 300:
            log.warning("gh_incident.comment.http_error",
                        req_id=req_id, repo=repo, pr_number=pr_number,
                        status=resp.status_code, body_tail=resp.text[-200:])
            return None
        html_url = resp.json().get("html_url")
        if not html_url:
            log.warning("gh_incident.comment.no_html_url",
                        req_id=req_id, repo=repo, pr_number=pr_number,
                        body_tail=resp.text[-200:])
            return None
        log.info("gh_incident.commented",
                 req_id=req_id, repo=repo, pr_number=pr_number,
                 url=html_url, reason=reason)
        return html_url
    except Exception as e:
        log.warning("gh_incident.comment.failed",
                    req_id=req_id, repo=repo, pr_number=pr_number, error=str(e))
        return None


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
    url = f"{_GH_API}/repos/{repo}/issues"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, headers=_auth_headers(), json=payload)
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
