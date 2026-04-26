"""Contract tests: escalate action's PR-merged shortcut to DONE.

REQ-archive-state-cleanup-1777195098

Black-box challenger. Derived from:
  openspec/changes/REQ-archive-state-cleanup-1777195098/specs/escalate-pr-merged-override/spec.md

Scenarios covered: PMO-S1 through PMO-S8.

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

# ─── Shared helpers ──────────────────────────────────────────────────────────


class _FakeBody:
    """Minimal fake WebhookBody for escalate action tests."""
    def __init__(
        self,
        event: str = "session.completed",
        issue_id: str = "issue-test",
        project_id: str = "proj-test",
    ):
        self.event = event
        self.issueId = issue_id
        self.projectId = project_id
        self.issueNumber = None


class _FakeBKD:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def follow_up_issue(self, *a, **kw):
        pass

    async def merge_tags_and_update(self, proj, issue_id, *, add=None, remove=None, status_id=None):
        pass


def _make_collecting_bkd(tag_log: list, status_log: list | None = None):
    class _CollectingBKD(_FakeBKD):
        async def merge_tags_and_update(self, proj, issue_id, *, add=None, remove=None, status_id=None):
            tag_log.append(list(add or []))
            if status_log is not None:
                status_log.append(status_id)

    return _CollectingBKD


def _make_ctx(involved_repos=None, **extra):
    ctx = dict(extra)
    if involved_repos is not None:
        ctx["involved_repos"] = involved_repos
    return ctx


def _gh_response(merged: bool):
    """A fake GH /pulls JSON entry (single PR) with desired merge state."""
    return [
        {
            "number": 1,
            "head": {"sha": "deadbeefdeadbeef"},
            "merged_at": "2026-04-26T08:00:00Z" if merged else None,
        },
    ]


def _gh_response_empty():
    return []


class _FakeHTTPResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=MagicMock(), response=MagicMock(status_code=self.status_code),
            )


def _patch_httpx(monkeypatch, *, repo_to_response: dict | None = None,
                 raise_for_repo: dict | None = None):
    """Patch httpx.AsyncClient inside escalate module so GET /repos/{repo}/pulls
    returns a fake response per repo. Use repo_to_response for normal returns;
    use raise_for_repo to raise an Exception for a specific repo path.
    """
    repo_to_response = repo_to_response or {}
    raise_for_repo = raise_for_repo or {}
    captured_calls: list[dict] = []

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            captured_calls.append({"url": url, "params": params})
            for repo, exc in raise_for_repo.items():
                if f"/repos/{repo}/" in url:
                    raise exc
            for repo, response in repo_to_response.items():
                if f"/repos/{repo}/" in url:
                    return response
            # Default: empty list
            return _FakeHTTPResponse(200, [])

    from orchestrator.actions import escalate as esc_mod
    monkeypatch.setattr(esc_mod, "httpx", MagicMock(
        AsyncClient=_FakeClient,
        HTTPError=__import__("httpx").HTTPError,
    ))
    return captured_calls


async def _run_escalate(
    monkeypatch,
    ctx,
    body=None,
    tags=None,
    *,
    bkd_cls=None,
    open_incident_mock=None,
    cas_returns=True,
    initial_state="accept-running",
):
    """Helper: call actions.escalate with mocked deps, return (result, captures).

    captures = {
        "ctx_updates": [],
        "cas_calls": [],
        "cleanup_calls": [],
    }
    """
    from orchestrator import gh_incident as ghi
    from orchestrator import k8s_runner as krm
    from orchestrator.actions import escalate as esc_mod
    from orchestrator.state import ReqState
    from orchestrator.store import db
    from orchestrator.store import req_state as rs_mod

    if body is None:
        body = _FakeBody()
    if tags is None:
        tags = []

    captures: dict = {
        "ctx_updates": [],
        "cas_calls": [],
        "cleanup_calls": [],
    }

    async def _capture_update(pool, req_id, patch):
        captures["ctx_updates"].append(dict(patch))

    async def _capture_cas(pool, req_id, expected, target, event, action, context_patch=None):
        captures["cas_calls"].append({
            "expected": expected,
            "target": target,
            "event": event,
            "action": action,
            "context_patch": dict(context_patch) if context_patch else None,
        })
        if context_patch:
            captures["ctx_updates"].append(dict(context_patch))
        return cas_returns

    monkeypatch.setattr(rs_mod, "update_context", _capture_update)
    monkeypatch.setattr(rs_mod, "cas_transition", _capture_cas)

    class _FakeRow:
        state = ReqState(initial_state)
        context: ClassVar = {}

    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=_FakeRow()))
    monkeypatch.setattr(db, "get_pool", lambda: MagicMock())

    if bkd_cls is not None:
        monkeypatch.setattr(esc_mod, "BKDClient", bkd_cls)
    else:
        monkeypatch.setattr(esc_mod, "BKDClient", _FakeBKD)

    if open_incident_mock is not None:
        monkeypatch.setattr(ghi, "open_incident", open_incident_mock)

    # Mock the runner controller so cleanup_runner doesn't blow up
    class _FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            captures["cleanup_calls"].append({
                "req_id": req_id, "retain_pvc": retain_pvc,
            })

    monkeypatch.setattr(krm, "get_controller", lambda: _FakeController())

    result = await esc_mod.escalate(body=body, req_id="REQ-test", tags=tags, ctx=ctx)
    return result, captures


# ═══════════════════════════════════════════════════════════════════════════════
# PMO-S1: single repo, PR merged → state=done, no escalated tag
# ═══════════════════════════════════════════════════════════════════════════════


async def test_pmo_s1_single_repo_merged_overrides_to_done(monkeypatch):
    """PMO-S1: GH says PR merged → cas_transition to DONE, BKD gets done+via:pr-merge tags, no escalate side-effects."""
    from orchestrator.config import settings as cfg
    from orchestrator.state import Event, ReqState

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "default_involved_repos", [])
    monkeypatch.setattr(cfg, "gh_incident_repo", "")

    _patch_httpx(monkeypatch, repo_to_response={
        "phona/sisyphus": _FakeHTTPResponse(200, _gh_response(merged=True)),
    })

    open_called: list = []

    async def _open_incident_should_not_be_called(*, repo, **kw):
        open_called.append(repo)
        return None

    tag_log: list = []
    status_log: list = []
    ctx = _make_ctx(
        involved_repos=["phona/sisyphus"],
        intent_issue_id="intent-s1",
        escalated_reason="verifier-decision-escalate",
    )

    result, captures = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s1"),
        bkd_cls=_make_collecting_bkd(tag_log, status_log),
        open_incident_mock=_open_incident_should_not_be_called,
        initial_state="accept-running",
    )

    # Contract 1: cas_transition called with target=DONE, event=ARCHIVE_DONE
    assert captures["cas_calls"], "PMO-S1: cas_transition MUST be invoked on override"
    cas = captures["cas_calls"][0]
    assert cas["target"] == ReqState.DONE, (
        f"PMO-S1: cas target MUST be ReqState.DONE, got {cas['target']!r}"
    )
    assert cas["event"] == Event.ARCHIVE_DONE, (
        f"PMO-S1: cas event MUST be Event.ARCHIVE_DONE, got {cas['event']!r}"
    )
    assert cas["action"] == "escalate_pr_merged_override", (
        f"PMO-S1: cas action MUST be 'escalate_pr_merged_override', got {cas['action']!r}"
    )

    # Contract 2: open_incident NOT called
    assert not open_called, (
        f"PMO-S1: open_incident MUST NOT be called on override path. Got: {open_called}"
    )

    # Contract 3: BKD tag set contains 'done' and 'via:pr-merge' but NOT 'escalated' / 'github-incident' / 'reason:*'
    assert tag_log, "PMO-S1: bkd.merge_tags_and_update MUST be called"
    flat_tags = [t for tags in tag_log for t in tags]
    assert "done" in flat_tags, (
        f"PMO-S1: BKD tags MUST include 'done'. Got: {tag_log}"
    )
    assert "via:pr-merge" in flat_tags, (
        f"PMO-S1: BKD tags MUST include 'via:pr-merge'. Got: {tag_log}"
    )
    assert "escalated" not in flat_tags, (
        f"PMO-S1: BKD tags MUST NOT include 'escalated'. Got: {tag_log}"
    )
    assert "github-incident" not in flat_tags, (
        f"PMO-S1: BKD tags MUST NOT include 'github-incident'. Got: {tag_log}"
    )
    reason_tags = [t for t in flat_tags if t.startswith("reason:")]
    assert not reason_tags, (
        f"PMO-S1: BKD tags MUST NOT include any 'reason:*' tag. Got: {reason_tags}"
    )

    # Contract 4: result indicates override
    assert isinstance(result, dict), f"PMO-S1: result MUST be dict, got {type(result)}"
    assert result.get("escalated") is False, (
        f"PMO-S1: result['escalated'] MUST be False, got {result.get('escalated')!r}"
    )
    assert result.get("completed_via") == "pr-merge", (
        f"PMO-S1: result['completed_via'] MUST be 'pr-merge', got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PMO-S2: single repo with open PR → falls through to original escalate
# ═══════════════════════════════════════════════════════════════════════════════


async def test_pmo_s2_single_repo_open_pr_falls_through(monkeypatch):
    """PMO-S2: PR not merged → original escalate flow runs (escalated tag + open_incident called)."""
    from orchestrator.config import settings as cfg
    from orchestrator.state import ReqState

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "default_involved_repos", [])
    monkeypatch.setattr(cfg, "gh_incident_repo", "")

    _patch_httpx(monkeypatch, repo_to_response={
        "phona/sisyphus": _FakeHTTPResponse(200, _gh_response(merged=False)),
    })

    open_calls: list = []

    async def _mock_open(*, repo, **kw):
        open_calls.append(repo)
        return f"https://github.com/{repo}/issues/1"

    tag_log: list = []
    ctx = _make_ctx(
        involved_repos=["phona/sisyphus"],
        intent_issue_id="intent-s2",
        escalated_reason="verifier-decision-escalate",
    )

    result, captures = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s2"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_mock_open,
        initial_state="review-running",
    )

    # Contract 1: NO cas with target=DONE
    done_cas = [c for c in captures["cas_calls"] if c["target"] == ReqState.DONE]
    assert not done_cas, (
        f"PMO-S2: cas_transition MUST NOT target DONE on fall-through. Got: {done_cas}"
    )

    # Contract 2: BKD tags include 'escalated'
    flat_tags = [t for tags in tag_log for t in tags]
    assert "escalated" in flat_tags, (
        f"PMO-S2: BKD tags MUST include 'escalated' on fall-through. Got: {tag_log}"
    )

    # Contract 3: open_incident called exactly once for phona/sisyphus
    assert len(open_calls) == 1 and open_calls[0] == "phona/sisyphus", (
        f"PMO-S2: open_incident MUST be called once for phona/sisyphus. Got: {open_calls}"
    )

    # Contract 4: result.escalated == True
    assert isinstance(result, dict) and result.get("escalated") is True, (
        f"PMO-S2: result['escalated'] MUST be True on fall-through, got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PMO-S3: multi-repo all merged → DONE
# ═══════════════════════════════════════════════════════════════════════════════


async def test_pmo_s3_multi_repo_all_merged_overrides_to_done(monkeypatch):
    """PMO-S3: both repos' PRs merged → state=DONE, completed_repos contains both."""
    from orchestrator.config import settings as cfg
    from orchestrator.state import ReqState

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "default_involved_repos", [])
    monkeypatch.setattr(cfg, "gh_incident_repo", "")

    _patch_httpx(monkeypatch, repo_to_response={
        "phona/repo-a": _FakeHTTPResponse(200, _gh_response(merged=True)),
        "phona/repo-b": _FakeHTTPResponse(200, _gh_response(merged=True)),
    })

    tag_log: list = []
    ctx = _make_ctx(
        involved_repos=["phona/repo-a", "phona/repo-b"],
        intent_issue_id="intent-s3",
    )

    result, captures = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.failed", issue_id="issue-s3"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=AsyncMock(return_value="should-not-call"),
        initial_state="archiving",
    )

    # Contract 1: cas_transition target=DONE
    assert captures["cas_calls"], "PMO-S3: cas_transition MUST be invoked"
    assert captures["cas_calls"][0]["target"] == ReqState.DONE, (
        f"PMO-S3: cas target MUST be DONE, got {captures['cas_calls'][0]['target']!r}"
    )

    # Contract 2: result.completed_repos contains both repos
    assert isinstance(result, dict) and result.get("completed_via") == "pr-merge", (
        f"PMO-S3: result['completed_via'] MUST be 'pr-merge', got {result!r}"
    )
    repos_in_result = set(result.get("repos") or [])
    assert "phona/repo-a" in repos_in_result and "phona/repo-b" in repos_in_result, (
        f"PMO-S3: result.repos MUST list both repos, got {repos_in_result}"
    )

    # Contract 3: ctx.completed_repos persisted
    completed_updates = [u for u in captures["ctx_updates"] if "completed_repos" in u]
    assert completed_updates, (
        f"PMO-S3: ctx.completed_repos MUST be set. ctx_updates={captures['ctx_updates']}"
    )
    assert set(completed_updates[-1]["completed_repos"]) == {
        "phona/repo-a", "phona/repo-b",
    }, (
        f"PMO-S3: ctx.completed_repos MUST contain both repos, "
        f"got {completed_updates[-1]['completed_repos']}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PMO-S4: multi-repo, one open → fall through
# ═══════════════════════════════════════════════════════════════════════════════


async def test_pmo_s4_multi_repo_partial_merged_falls_through(monkeypatch):
    """PMO-S4: repo-a merged, repo-b open → original escalate path runs."""
    from orchestrator.config import settings as cfg
    from orchestrator.state import ReqState

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "default_involved_repos", [])
    monkeypatch.setattr(cfg, "gh_incident_repo", "")

    _patch_httpx(monkeypatch, repo_to_response={
        "phona/repo-a": _FakeHTTPResponse(200, _gh_response(merged=True)),
        "phona/repo-b": _FakeHTTPResponse(200, _gh_response(merged=False)),
    })

    open_calls: list = []

    async def _mock_open(*, repo, **kw):
        open_calls.append(repo)
        return f"https://github.com/{repo}/issues/1"

    tag_log: list = []
    ctx = _make_ctx(
        involved_repos=["phona/repo-a", "phona/repo-b"],
        intent_issue_id="intent-s4",
        escalated_reason="verifier-decision-escalate",
    )

    result, captures = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s4"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_mock_open,
        initial_state="accept-running",
    )

    # Contract 1: no DONE cas
    done_cas = [c for c in captures["cas_calls"] if c["target"] == ReqState.DONE]
    assert not done_cas, (
        f"PMO-S4: cas_transition MUST NOT target DONE on partial merge. Got: {done_cas}"
    )

    # Contract 2: escalated tag added
    flat_tags = [t for tags in tag_log for t in tags]
    assert "escalated" in flat_tags, (
        f"PMO-S4: BKD tags MUST include 'escalated' on fall-through. Got: {tag_log}"
    )

    # Contract 3: open_incident called for both repos
    assert set(open_calls) == {"phona/repo-a", "phona/repo-b"}, (
        f"PMO-S4: open_incident MUST be called for both repos. Got: {open_calls}"
    )

    # Contract 4: result escalated=True
    assert isinstance(result, dict) and result.get("escalated") is True, (
        f"PMO-S4: result['escalated'] MUST be True, got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PMO-S5: no involved_repos → fall through (no GH probe)
# ═══════════════════════════════════════════════════════════════════════════════


async def test_pmo_s5_no_involved_repos_falls_through(monkeypatch):
    """PMO-S5: empty resolve_repos → no GH HTTP call, fall through to original escalate."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "default_involved_repos", [])
    monkeypatch.setattr(cfg, "gh_incident_repo", "")

    captured_calls = _patch_httpx(monkeypatch, repo_to_response={})

    open_calls: list = []

    async def _mock_open(*, repo, **kw):
        open_calls.append(repo)
        return None  # GH outage path simulating no incidents

    tag_log: list = []
    ctx = _make_ctx(
        intent_issue_id="intent-s5",
        escalated_reason="intake-fail",
    )

    result, captures = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s5"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=_mock_open,
        initial_state="intaking",
    )

    # Contract 1: no GH HTTP calls
    pull_calls = [c for c in captured_calls if "/pulls" in c["url"]]
    assert not pull_calls, (
        f"PMO-S5: NO GH /pulls call MUST happen when involved_repos empty. Got: {pull_calls}"
    )

    # Contract 2: cas_transition NOT called with target=DONE
    from orchestrator.state import ReqState
    done_cas = [c for c in captures["cas_calls"] if c["target"] == ReqState.DONE]
    assert not done_cas, (
        f"PMO-S5: NO cas_transition to DONE on empty repos. Got: {done_cas}"
    )

    # Contract 3: original escalate flow runs (escalated tag added)
    flat_tags = [t for tags in tag_log for t in tags]
    assert "escalated" in flat_tags, (
        f"PMO-S5: BKD tags MUST include 'escalated' on fall-through. Got: {tag_log}"
    )

    # Contract 4: result escalated=True
    assert isinstance(result, dict) and result.get("escalated") is True, (
        f"PMO-S5: result['escalated'] MUST be True, got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PMO-S6: GH 503 during probe → fall through, no exception
# ═══════════════════════════════════════════════════════════════════════════════


async def test_pmo_s6_gh_api_failure_falls_through(monkeypatch):
    """PMO-S6: GH /pulls raises HTTPError → helper returns False, original flow runs, no exception leaks."""
    import httpx

    from orchestrator.config import settings as cfg
    from orchestrator.state import ReqState

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "default_involved_repos", [])
    monkeypatch.setattr(cfg, "gh_incident_repo", "")

    _patch_httpx(monkeypatch, raise_for_repo={
        "phona/sisyphus": httpx.ConnectError("GH unreachable"),
    })

    open_calls: list = []

    async def _mock_open(*, repo, **kw):
        open_calls.append(repo)
        return f"https://github.com/{repo}/issues/1"

    tag_log: list = []
    ctx = _make_ctx(
        involved_repos=["phona/sisyphus"],
        intent_issue_id="intent-s6",
        escalated_reason="verifier-decision-escalate",
    )

    raised: Exception | None = None
    try:
        result, captures = await _run_escalate(
            monkeypatch,
            ctx=ctx,
            body=_FakeBody(event="session.completed", issue_id="issue-s6"),
            bkd_cls=_make_collecting_bkd(tag_log),
            open_incident_mock=_mock_open,
            initial_state="accept-running",
        )
    except Exception as e:
        raised = e

    # Contract 1: no exception leaked
    assert raised is None, (
        f"PMO-S6: escalate MUST NOT raise on GH probe failure. Got: {type(raised).__name__}: {raised}"
    )

    # Contract 2: no DONE cas
    done_cas = [c for c in captures["cas_calls"] if c["target"] == ReqState.DONE]  # type: ignore[possibly-undefined]
    assert not done_cas, (
        f"PMO-S6: cas to DONE MUST NOT happen on GH probe failure. Got: {done_cas}"
    )

    # Contract 3: original escalate path runs (escalated tag added)
    flat_tags = [t for tags in tag_log for t in tags]
    assert "escalated" in flat_tags, (
        f"PMO-S6: BKD tags MUST include 'escalated' on GH probe failure. Got: {tag_log}"
    )

    # Contract 4: result escalated=True
    assert isinstance(result, dict) and result.get("escalated") is True, (  # type: ignore[possibly-undefined]
        f"PMO-S6: result['escalated'] MUST be True, got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PMO-S7: override path triggers cleanup_runner with retain_pvc=False
# ═══════════════════════════════════════════════════════════════════════════════


async def test_pmo_s7_override_cleanup_runner_no_retain(monkeypatch):
    """PMO-S7: PR merged override path MUST cleanup_runner with retain_pvc=False (DONE semantics)."""
    import asyncio

    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "default_involved_repos", [])
    monkeypatch.setattr(cfg, "gh_incident_repo", "")

    _patch_httpx(monkeypatch, repo_to_response={
        "phona/sisyphus": _FakeHTTPResponse(200, _gh_response(merged=True)),
    })

    tag_log: list = []
    ctx = _make_ctx(
        involved_repos=["phona/sisyphus"],
        intent_issue_id="intent-s7",
    )

    result, captures = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s7"),
        bkd_cls=_make_collecting_bkd(tag_log),
        open_incident_mock=AsyncMock(return_value="should-not-call"),
        initial_state="archiving",
    )

    # The override schedules cleanup as fire-and-forget via asyncio.create_task.
    # Yield to the loop so the task runs and records its call.
    await asyncio.sleep(0)

    # Contract: cleanup_runner called with retain_pvc=False
    assert captures["cleanup_calls"], (
        f"PMO-S7: cleanup_runner MUST be invoked on override. Got: {captures['cleanup_calls']}"
    )
    cleanup = captures["cleanup_calls"][-1]
    assert cleanup["retain_pvc"] is False, (
        f"PMO-S7: cleanup_runner MUST be called with retain_pvc=False, got {cleanup}"
    )

    # Sanity: result indicates override
    assert isinstance(result, dict) and result.get("completed_via") == "pr-merge", (
        f"PMO-S7: result MUST indicate completed_via='pr-merge'. Got: {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PMO-S8: override BKD tag set has done + via:pr-merge but NOT escalated
# ═══════════════════════════════════════════════════════════════════════════════


async def test_pmo_s8_override_bkd_tag_set(monkeypatch):
    """PMO-S8: override BKD merge_tags_and_update.add MUST contain done + via:pr-merge, MUST NOT contain escalated/reason:*/github-incident."""
    from orchestrator.config import settings as cfg

    monkeypatch.setattr(cfg, "github_token", "gh-real-token")
    monkeypatch.setattr(cfg, "default_involved_repos", [])
    monkeypatch.setattr(cfg, "gh_incident_repo", "")

    _patch_httpx(monkeypatch, repo_to_response={
        "phona/sisyphus": _FakeHTTPResponse(200, _gh_response(merged=True)),
    })

    tag_log: list = []
    status_log: list = []
    ctx = _make_ctx(
        involved_repos=["phona/sisyphus"],
        intent_issue_id="intent-s8",
    )

    await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-s8"),
        bkd_cls=_make_collecting_bkd(tag_log, status_log),
        open_incident_mock=AsyncMock(return_value="should-not-call"),
        initial_state="archiving",
    )

    assert tag_log, "PMO-S8: bkd.merge_tags_and_update MUST be invoked at least once"
    flat_tags = [t for tags in tag_log for t in tags]

    assert "done" in flat_tags, (
        f"PMO-S8: BKD add tags MUST include 'done'. Got: {tag_log}"
    )
    assert "via:pr-merge" in flat_tags, (
        f"PMO-S8: BKD add tags MUST include 'via:pr-merge'. Got: {tag_log}"
    )
    assert "escalated" not in flat_tags, (
        f"PMO-S8: BKD add tags MUST NOT include 'escalated'. Got: {tag_log}"
    )
    reason_tags = [t for t in flat_tags if t.startswith("reason:")]
    assert not reason_tags, (
        f"PMO-S8: BKD add tags MUST NOT include 'reason:*'. Got: {reason_tags}"
    )
    assert "github-incident" not in flat_tags, (
        f"PMO-S8: BKD add tags MUST NOT include 'github-incident'. Got: {tag_log}"
    )

    # Sanity: statusId=done if PATCHed
    if status_log:
        assert any(s == "done" for s in status_log), (
            f"PMO-S8: When statusId is patched, it MUST be 'done'. Got: {status_log}"
        )
