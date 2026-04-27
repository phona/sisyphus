# PLAN-001 Active CI dispatch before pr-ci-watch

- **status**: implementing
- **createdAt**: 2026-04-27 00:00
- **approvedAt**: 2026-04-27
- **relatedTask**: FEAT-001

## Context

### Problem

`dispatch.yml` in ttpos business repos only triggers on `repository_dispatch`
events — not on `pull_request` events. When analyze-agent opens a PR
programmatically via `gh pr create` in a Coder workspace, no
`repository_dispatch` event fires, so `ttpos-ci ci-go.yml` never starts. The
pr-ci-watch checker then polls indefinitely and times out with `no-gha`.

### Relevant files

| File | Role |
|------|------|
| `orchestrator/src/orchestrator/actions/create_pr_ci_watch.py` | Action entry; owns `_run_checker()` path |
| `orchestrator/src/orchestrator/checkers/pr_ci_watch.py` | Polling loop; reads check-runs only |
| `orchestrator/src/orchestrator/config.py` | All feature flags |
| `orchestrator/tests/test_actions_create_pr_ci_watch.py` | Existing unit tests for action layer |

### Call chain (checker path)

```
create_pr_ci_watch()
  └─ _capture_pr_urls()          ← best-effort PR URL discovery (already exists)
  └─ _run_checker()
       └─ _discover_repos_from_runner()
       └─ checker.watch_pr_ci()  ← polls; currently no dispatch happens before this
```

### GitHub API

`POST /repos/{owner}/{repo}/dispatches` with body:
```json
{"event_type": "<configurable>", "client_payload": {"branch": "feat/REQ-x", "req_id": "REQ-x"}}
```
Requires `github_token` with `repo` scope (classic) or `Actions: write`
permission (fine-grained PAT). The orchestrator already has `github_token`
available in `settings`.

## Proposal

### 1. `config.py` — two new flags

```python
# Active dispatch before pr-ci-watch polling
pr_ci_dispatch_enabled: bool = False
pr_ci_dispatch_event_type: str = "ci-trigger"
```

### 2. `create_pr_ci_watch.py` — add `_dispatch_ci_trigger()`

```python
async def _dispatch_ci_trigger(*, repos: list[str], branch: str, req_id: str) -> None:
    """Best-effort: fire repository_dispatch on each repo to start CI.

    Failure per repo → warning only; never raises. Blocked by
    pr_ci_dispatch_enabled flag (caller must check).
    """
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {settings.github_token}",
    }
    payload = {
        "event_type": settings.pr_ci_dispatch_event_type,
        "client_payload": {"branch": branch, "req_id": req_id},
    }
    async with httpx.AsyncClient(base_url="https://api.github.com",
                                  headers=headers, timeout=15.0) as client:
        for repo in repos:
            try:
                r = await client.post(f"/repos/{repo}/dispatches", json=payload)
                r.raise_for_status()
                log.info("create_pr_ci_watch.dispatch_ok", repo=repo,
                         event_type=settings.pr_ci_dispatch_event_type)
            except Exception as e:
                log.warning("create_pr_ci_watch.dispatch_failed",
                            repo=repo, error=str(e))
```

### 3. `_run_checker()` — call dispatch after repo discovery, before polling

```python
async def _run_checker(*, req_id: str, ctx: dict) -> dict:
    ...
    repos = await _discover_repos_from_runner(req_id)
    if not repos:
        ...

    if settings.pr_ci_dispatch_enabled and repos:
        await _dispatch_ci_trigger(repos=repos, branch=branch, req_id=req_id)

    result = await checker.watch_pr_ci(...)
```

### 4. Tests — new cases in `test_actions_create_pr_ci_watch.py`

- `test_dispatch_ci_trigger_calls_gh_api` — happy path, two repos each get a POST
- `test_dispatch_ci_trigger_tolerates_per_repo_error` — one repo 422, other succeeds
- `test_run_checker_skips_dispatch_when_disabled` — flag off → no POST

## Risks

| Risk | Mitigation |
|------|-----------|
| github_token lacks `Actions: write` | Dispatch returns 422/403; failure is best-effort, polling continues; verifier catches `no-gha` |
| Double-trigger if `dispatch.yml` also triggers on `pull_request` | Acceptable: duplicate GHA run, only one set of check-runs matters |
| Wrong `event_type` (mismatch with dispatch.yml) | CI still won't start; same outcome as before; verifier handles `no-gha` |
| No token configured (`github_token=""`) | `Authorization: Bearer ` header returns 401; per-repo warning, no crash |

## Scope

3 files:
- `config.py` (+2 lines)
- `create_pr_ci_watch.py` (+~25 lines: helper + call site)
- `test_actions_create_pr_ci_watch.py` (+~50 lines: 3 new tests)

## Alternatives

**A. Trigger inside `watch_pr_ci` checker**: rejected — checker is a pure read-only
poller; dispatch is an orchestrator write concern.

**B. Always dispatch (no flag)**: rejected — rollout risk for deployments whose
`dispatch.yml` uses a different `event_type` or doesn't exist.

## Annotations

(User annotations and responses.)
