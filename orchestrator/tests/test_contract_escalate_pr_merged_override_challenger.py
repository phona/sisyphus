"""Challenger contract tests: escalate PR-merged override to DONE.

REQ-archive-state-cleanup-1777195098

Black-box challenger. Derived exclusively from:
  openspec/changes/REQ-archive-state-cleanup-1777195098/specs/escalate-pr-merged-override/spec.md
  openspec/changes/REQ-archive-state-cleanup-1777195098/specs/escalate-pr-merged-override/contract.spec.yaml

Scenarios: PMO-S1 through PMO-S8 (all spec scenarios, one test per scenario).

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import httpx

# ─── Test infrastructure (black-box: mocks external deps only) ───────────────


class _FakeWebhookBody:
    def __init__(self, event: str = "session.completed", issue_id: str = "test-issue"):
        self.event = event
        self.issueId = issue_id
        self.projectId = "test-project"
        self.issueNumber = None


def _merged_pr() -> list[dict]:
    return [{"number": 42, "head": {"sha": "abc123"}, "merged_at": "2026-04-26T00:00:00Z"}]


def _open_pr() -> list[dict]:
    return [{"number": 43, "head": {"sha": "def456"}, "merged_at": None}]


def _empty_pr_list() -> list[dict]:
    return []


class _FakeResponse:
    def __init__(self, status_code: int, body: list):
        self.status_code = status_code
        self._body = body

    def json(self) -> list:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=MagicMock(),
                response=MagicMock(status_code=self.status_code),
            )


def _make_httpx_patcher(repo_responses: dict[str, list], raise_for: dict[str, Exception] | None = None):
    """Return (patcher_fn, http_calls_log).

    patcher_fn(monkeypatch) patches httpx.AsyncClient inside escalate module.
    """
    raise_for = raise_for or {}
    calls: list[str] = []

    class _FakeClient:
        def __init__(self, *_args, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def get(self, url: str, params=None, **_kwargs):
            calls.append(url)
            for repo, exc in raise_for.items():
                if f"/repos/{repo}/" in url:
                    raise exc
            for repo, payload in repo_responses.items():
                if f"/repos/{repo}/" in url:
                    return _FakeResponse(200, payload)
            return _FakeResponse(200, [])

    def _patch(monkeypatch):
        from orchestrator.actions import escalate as esc_mod
        monkeypatch.setattr(esc_mod, "httpx", MagicMock(
            AsyncClient=_FakeClient,
            HTTPError=httpx.HTTPError,
        ))

    return _patch, calls


class _TagCollector:
    """Fake BKDClient that records merge_tags_and_update calls."""
    def __init__(self, tag_log: list, status_log: list | None = None):
        self._tag_log = tag_log
        self._status_log = status_log

    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    async def follow_up_issue(self, *_, **__): pass
    async def merge_tags_and_update(self, _proj, _id, *, add=None, remove=None, status_id=None):
        self._tag_log.append(list(add or []))
        if self._status_log is not None:
            self._status_log.append(status_id)


def _tag_collector_factory(tag_log, status_log=None):
    def _factory(*_, **__):
        return _TagCollector(tag_log, status_log)
    return _factory


async def _invoke_escalate(
    monkeypatch,
    *,
    ctx: dict,
    tags: list[str] | None = None,
    body=None,
    bkd_factory=None,
    open_incident_mock=None,
    gh_token: str = "fake-token",
    initial_state: str = "accept-running",
) -> tuple[dict, dict]:
    """Invoke actions.escalate with fully-mocked external deps.

    Returns (result, captures) where captures = {cas_calls, ctx_updates, cleanup_calls}.
    """
    from orchestrator import gh_incident as ghi_mod
    from orchestrator import k8s_runner as krm
    from orchestrator.actions import escalate as esc_mod
    from orchestrator.config import settings as cfg
    from orchestrator.state import ReqState
    from orchestrator.store import db
    from orchestrator.store import req_state as rs

    monkeypatch.setattr(cfg, "github_token", gh_token)
    monkeypatch.setattr(cfg, "default_involved_repos", [])
    monkeypatch.setattr(cfg, "gh_incident_repo", "")

    if body is None:
        body = _FakeWebhookBody()
    if tags is None:
        tags = []

    caps: dict = {"cas_calls": [], "ctx_updates": [], "cleanup_calls": []}

    async def _fake_cas(pool, req_id, expected, target, event, action, context_patch=None):
        caps["cas_calls"].append({
            "target": target, "event": event, "action": action,
            "context_patch": dict(context_patch) if context_patch else None,
        })
        if context_patch:
            caps["ctx_updates"].append(dict(context_patch))
        return True

    async def _fake_update_ctx(pool, req_id, patch):
        caps["ctx_updates"].append(dict(patch))

    class _FakeRow:
        state = ReqState(initial_state)
        context: ClassVar = {}

    monkeypatch.setattr(rs, "get", AsyncMock(return_value=_FakeRow()))
    monkeypatch.setattr(rs, "cas_transition", _fake_cas)
    monkeypatch.setattr(rs, "update_context", _fake_update_ctx)
    monkeypatch.setattr(db, "get_pool", lambda: MagicMock())

    if bkd_factory is not None:
        monkeypatch.setattr(esc_mod, "BKDClient", bkd_factory)
    else:
        class _NullBKD:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): return False
            async def follow_up_issue(self, *_, **__): pass
            async def merge_tags_and_update(self, *_, **__): pass
        monkeypatch.setattr(esc_mod, "BKDClient", lambda *_, **__: _NullBKD())

    if open_incident_mock is not None:
        monkeypatch.setattr(ghi_mod, "open_incident", open_incident_mock)

    class _FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc: bool = False):
            caps["cleanup_calls"].append({"req_id": req_id, "retain_pvc": retain_pvc})

    monkeypatch.setattr(krm, "get_controller", lambda: _FakeController())

    result = await esc_mod.escalate(body=body, req_id="REQ-test", tags=tags, ctx=ctx)
    return result, caps


# ─── PMO-S1 ──────────────────────────────────────────────────────────────────


async def test_pmo_s1_single_repo_merged_overrides_escalate_to_done(monkeypatch):
    """PMO-S1: single repo with merged PR → CAS to DONE with correct event/action, no escalated tag."""
    from orchestrator.state import Event, ReqState

    patch_httpx, _ = _make_httpx_patcher({"phona/sisyphus": _merged_pr()})
    patch_httpx(monkeypatch)

    open_calls: list = []
    async def _should_not_call(*, repo, **_): open_calls.append(repo)

    tag_log: list = []
    result, caps = await _invoke_escalate(
        monkeypatch,
        ctx={"involved_repos": ["phona/sisyphus"], "intent_issue_id": "iid-s1",
             "escalated_reason": "verifier-decision-escalate"},
        body=_FakeWebhookBody(event="session.completed", issue_id="bid-s1"),
        bkd_factory=_tag_collector_factory(tag_log),
        open_incident_mock=_should_not_call,
        initial_state="accept-running",
    )

    # Contract: cas_transition called with DONE + ARCHIVE_DONE + escalate_pr_merged_override
    assert caps["cas_calls"], "PMO-S1: cas_transition MUST be called"
    cas = caps["cas_calls"][0]
    assert cas["target"] == ReqState.DONE, \
        f"PMO-S1: next_state MUST be DONE, got {cas['target']!r}"
    assert cas["event"] == Event.ARCHIVE_DONE, \
        f"PMO-S1: event MUST be ARCHIVE_DONE, got {cas['event']!r}"
    assert cas["action"] == "escalate_pr_merged_override", \
        f"PMO-S1: action MUST be 'escalate_pr_merged_override', got {cas['action']!r}"

    # Contract: open_incident MUST NOT be called
    assert not open_calls, \
        f"PMO-S1: open_incident MUST NOT be called on override. Called with: {open_calls}"

    # Contract: BKD tags contain done + via:pr-merge; MUST NOT contain escalated/reason:*/github-incident
    flat = [t for ts in tag_log for t in ts]
    assert "done" in flat, f"PMO-S1: 'done' MUST be in BKD add tags. Got: {tag_log}"
    assert "via:pr-merge" in flat, f"PMO-S1: 'via:pr-merge' MUST be in BKD add tags. Got: {tag_log}"
    assert "escalated" not in flat, f"PMO-S1: 'escalated' MUST NOT be in BKD add tags. Got: {tag_log}"
    assert "github-incident" not in flat, \
        f"PMO-S1: 'github-incident' MUST NOT be in BKD add tags. Got: {tag_log}"
    reason_tags = [t for t in flat if t.startswith("reason:")]
    assert not reason_tags, f"PMO-S1: reason:* MUST NOT be in BKD add tags. Got: {reason_tags}"

    # Contract: return value
    assert isinstance(result, dict), f"PMO-S1: result MUST be dict, got {type(result)}"
    assert result.get("escalated") is False, \
        f"PMO-S1: result['escalated'] MUST be False, got {result!r}"
    assert result.get("completed_via") == "pr-merge", \
        f"PMO-S1: result['completed_via'] MUST be 'pr-merge', got {result!r}"


# ─── PMO-S2 ──────────────────────────────────────────────────────────────────


async def test_pmo_s2_single_repo_open_pr_falls_through(monkeypatch):
    """PMO-S2: single repo with open (non-merged) PR → original escalate flow runs."""
    from orchestrator.state import ReqState

    patch_httpx, _ = _make_httpx_patcher({"phona/sisyphus": _open_pr()})
    patch_httpx(monkeypatch)

    open_calls: list = []
    async def _record_open(*, repo, **_):
        open_calls.append(repo)
        return f"https://github.com/{repo}/issues/99"

    tag_log: list = []
    result, caps = await _invoke_escalate(
        monkeypatch,
        ctx={"involved_repos": ["phona/sisyphus"], "intent_issue_id": "iid-s2",
             "escalated_reason": "verifier-decision-escalate"},
        body=_FakeWebhookBody(event="session.completed"),
        bkd_factory=_tag_collector_factory(tag_log),
        open_incident_mock=_record_open,
        initial_state="review-running",
    )

    # Contract: MUST NOT CAS to DONE
    done_cas = [c for c in caps["cas_calls"] if c["target"] == ReqState.DONE]
    assert not done_cas, f"PMO-S2: MUST NOT CAS to DONE when PR is open. Got: {done_cas}"

    # Contract: BKD tags MUST include 'escalated'
    flat = [t for ts in tag_log for t in ts]
    assert "escalated" in flat, f"PMO-S2: 'escalated' MUST be in BKD tags on fall-through. Got: {tag_log}"

    # Contract: open_incident called exactly once for phona/sisyphus
    assert open_calls == ["phona/sisyphus"], \
        f"PMO-S2: open_incident MUST be called once for phona/sisyphus. Got: {open_calls}"

    # Contract: result.escalated == True
    assert isinstance(result, dict) and result.get("escalated") is True, \
        f"PMO-S2: result['escalated'] MUST be True on fall-through. Got: {result!r}"


# ─── PMO-S3 ──────────────────────────────────────────────────────────────────


async def test_pmo_s3_multi_repo_all_merged_overrides_to_done(monkeypatch):
    """PMO-S3: two repos, both PRs merged → state=DONE, result.completed_repos lists both."""
    from orchestrator.state import ReqState

    patch_httpx, _ = _make_httpx_patcher({
        "phona/repo-a": _merged_pr(),
        "phona/repo-b": _merged_pr(),
    })
    patch_httpx(monkeypatch)

    tag_log: list = []
    result, caps = await _invoke_escalate(
        monkeypatch,
        ctx={"involved_repos": ["phona/repo-a", "phona/repo-b"], "intent_issue_id": "iid-s3"},
        body=_FakeWebhookBody(event="session.failed"),
        bkd_factory=_tag_collector_factory(tag_log),
        open_incident_mock=AsyncMock(return_value="should-not-reach"),
        initial_state="archiving",
    )

    # Contract: CAS to DONE
    assert caps["cas_calls"], "PMO-S3: cas_transition MUST be called"
    assert caps["cas_calls"][0]["target"] == ReqState.DONE, \
        f"PMO-S3: next_state MUST be DONE. Got: {caps['cas_calls'][0]['target']!r}"

    # Contract: result contains completed_via='pr-merge' (spec.md PMO-S3)
    assert isinstance(result, dict) and result.get("completed_via") == "pr-merge", \
        f"PMO-S3: result['completed_via'] MUST be 'pr-merge'. Got: {result!r}"

    # Contract: result contains completed_repos listing both repos (spec.md PMO-S3)
    # spec says "completed_repos listing both repos"
    completed = result.get("completed_repos") or result.get("repos") or []
    assert "phona/repo-a" in completed and "phona/repo-b" in completed, \
        f"PMO-S3: result MUST list both repos in completed_repos. Got result={result!r}"

    # Contract: ctx patch sets completed_repos (contract.spec.yaml context_patch_keys)
    ctx_with_completed = [u for u in caps["ctx_updates"] if "completed_repos" in u]
    assert ctx_with_completed, \
        f"PMO-S3: ctx.completed_repos MUST be patched. ctx_updates={caps['ctx_updates']}"
    assert set(ctx_with_completed[-1]["completed_repos"]) == {"phona/repo-a", "phona/repo-b"}, \
        f"PMO-S3: ctx.completed_repos MUST list both repos. Got: {ctx_with_completed[-1]}"


# ─── PMO-S4 ──────────────────────────────────────────────────────────────────


async def test_pmo_s4_multi_repo_partial_merged_falls_through(monkeypatch):
    """PMO-S4: repo-a merged, repo-b open → original escalate flow (not DONE)."""
    from orchestrator.state import ReqState

    patch_httpx, _ = _make_httpx_patcher({
        "phona/repo-a": _merged_pr(),
        "phona/repo-b": _open_pr(),
    })
    patch_httpx(monkeypatch)

    open_calls: list = []
    async def _record_open(*, repo, **_):
        open_calls.append(repo)
        return f"https://github.com/{repo}/issues/1"

    tag_log: list = []
    result, caps = await _invoke_escalate(
        monkeypatch,
        ctx={"involved_repos": ["phona/repo-a", "phona/repo-b"], "intent_issue_id": "iid-s4",
             "escalated_reason": "verifier-decision-escalate"},
        body=_FakeWebhookBody(event="session.completed"),
        bkd_factory=_tag_collector_factory(tag_log),
        open_incident_mock=_record_open,
        initial_state="accept-running",
    )

    # Contract: MUST NOT CAS to DONE
    done_cas = [c for c in caps["cas_calls"] if c["target"] == ReqState.DONE]
    assert not done_cas, \
        f"PMO-S4: MUST NOT CAS to DONE when one PR is not merged. Got: {done_cas}"

    # Contract: BKD escalated tag added
    flat = [t for ts in tag_log for t in ts]
    assert "escalated" in flat, \
        f"PMO-S4: 'escalated' MUST be in BKD tags on fall-through. Got: {tag_log}"

    # Contract: result escalated=True
    assert isinstance(result, dict) and result.get("escalated") is True, \
        f"PMO-S4: result['escalated'] MUST be True. Got: {result!r}"


# ─── PMO-S5 ──────────────────────────────────────────────────────────────────


async def test_pmo_s5_no_involved_repos_falls_through_without_gh_call(monkeypatch):
    """PMO-S5: empty involved_repos → merge check returns False, NO GH HTTP request, original flow."""
    from orchestrator.state import ReqState

    patch_httpx, http_calls = _make_httpx_patcher({})
    patch_httpx(monkeypatch)

    open_calls: list = []
    async def _record_open(*, repo, **_):
        open_calls.append(repo)
        return None

    tag_log: list = []
    result, caps = await _invoke_escalate(
        monkeypatch,
        ctx={"intent_issue_id": "iid-s5", "escalated_reason": "intake-fail"},
        body=_FakeWebhookBody(event="session.completed"),
        bkd_factory=_tag_collector_factory(tag_log),
        open_incident_mock=_record_open,
        initial_state="intaking",
    )

    # Contract: NO /pulls HTTP call when no repos resolved
    pulls_calls = [u for u in http_calls if "/pulls" in u]
    assert not pulls_calls, \
        f"PMO-S5: NO GH /pulls call MUST be made when involved_repos is empty. Got: {pulls_calls}"

    # Contract: MUST NOT CAS to DONE
    done_cas = [c for c in caps["cas_calls"] if c["target"] == ReqState.DONE]
    assert not done_cas, \
        f"PMO-S5: MUST NOT CAS to DONE when no repos. Got: {done_cas}"

    # Contract: original escalate flow runs (escalated tag)
    flat = [t for ts in tag_log for t in ts]
    assert "escalated" in flat, \
        f"PMO-S5: 'escalated' MUST be in BKD tags on fall-through. Got: {tag_log}"

    # Contract: result.escalated=True
    assert isinstance(result, dict) and result.get("escalated") is True, \
        f"PMO-S5: result['escalated'] MUST be True. Got: {result!r}"


# ─── PMO-S6 ──────────────────────────────────────────────────────────────────


async def test_pmo_s6_gh_api_failure_falls_through_no_exception_leak(monkeypatch):
    """PMO-S6: GH /pulls raises HTTPError → log warning, helper returns False, original flow, no exception."""
    from orchestrator.state import ReqState

    patch_httpx, _ = _make_httpx_patcher(
        {},
        raise_for={"phona/sisyphus": httpx.ConnectError("GH 503")},
    )
    patch_httpx(monkeypatch)

    open_calls: list = []
    async def _record_open(*, repo, **_):
        open_calls.append(repo)
        return f"https://github.com/{repo}/issues/1"

    tag_log: list = []
    caught_exc: Exception | None = None
    result = caps = None
    try:
        result, caps = await _invoke_escalate(
            monkeypatch,
            ctx={"involved_repos": ["phona/sisyphus"], "intent_issue_id": "iid-s6",
                 "escalated_reason": "verifier-decision-escalate"},
            body=_FakeWebhookBody(event="session.completed"),
            bkd_factory=_tag_collector_factory(tag_log),
            open_incident_mock=_record_open,
            initial_state="accept-running",
        )
    except Exception as exc:
        caught_exc = exc

    # Contract: no exception must propagate
    assert caught_exc is None, \
        f"PMO-S6: escalate MUST NOT raise on GH probe failure. Got: {type(caught_exc)}: {caught_exc}"

    # Contract: MUST NOT CAS to DONE
    done_cas = [c for c in caps["cas_calls"] if c["target"] == ReqState.DONE]  # type: ignore[index]
    assert not done_cas, \
        f"PMO-S6: MUST NOT CAS to DONE when GH probe fails. Got: {done_cas}"

    # Contract: original escalate flow runs (escalated tag added)
    flat = [t for ts in tag_log for t in ts]
    assert "escalated" in flat, \
        f"PMO-S6: 'escalated' MUST be in BKD tags on GH probe failure. Got: {tag_log}"

    # Contract: result.escalated=True
    assert isinstance(result, dict) and result.get("escalated") is True, \
        f"PMO-S6: result['escalated'] MUST be True. Got: {result!r}"


# ─── PMO-S7 ──────────────────────────────────────────────────────────────────


async def test_pmo_s7_override_schedules_cleanup_with_retain_pvc_false(monkeypatch):
    """PMO-S7: override path MUST fire cleanup_runner(retain_pvc=False) — fire-and-forget."""
    patch_httpx, _ = _make_httpx_patcher({"phona/sisyphus": _merged_pr()})
    patch_httpx(monkeypatch)

    tag_log: list = []
    result, caps = await _invoke_escalate(
        monkeypatch,
        ctx={"involved_repos": ["phona/sisyphus"], "intent_issue_id": "iid-s7"},
        body=_FakeWebhookBody(event="session.completed"),
        bkd_factory=_tag_collector_factory(tag_log),
        open_incident_mock=AsyncMock(return_value=None),
        initial_state="archiving",
    )

    # Yield to allow fire-and-forget asyncio.Task to execute
    await asyncio.sleep(0)

    # Contract: cleanup_runner called with retain_pvc=False
    assert caps["cleanup_calls"], \
        f"PMO-S7: cleanup_runner MUST be invoked on override. Got: {caps['cleanup_calls']}"
    assert caps["cleanup_calls"][-1]["retain_pvc"] is False, \
        f"PMO-S7: retain_pvc MUST be False (mirror admin/complete). Got: {caps['cleanup_calls'][-1]}"

    # Sanity: this IS the override path
    assert isinstance(result, dict) and result.get("completed_via") == "pr-merge", \
        f"PMO-S7: result MUST indicate pr-merge override. Got: {result!r}"


# ─── PMO-S8 ──────────────────────────────────────────────────────────────────


async def test_pmo_s8_override_bkd_tags_done_and_via_pr_merge_not_escalated(monkeypatch):
    """PMO-S8: BKD merge_tags_and_update.add MUST have {done, via:pr-merge}; MUST NOT have {escalated, reason:*, github-incident}."""
    patch_httpx, _ = _make_httpx_patcher({"phona/sisyphus": _merged_pr()})
    patch_httpx(monkeypatch)

    tag_log: list = []
    status_log: list = []
    await _invoke_escalate(
        monkeypatch,
        ctx={"involved_repos": ["phona/sisyphus"], "intent_issue_id": "iid-s8"},
        body=_FakeWebhookBody(event="session.completed"),
        bkd_factory=_tag_collector_factory(tag_log, status_log),
        open_incident_mock=AsyncMock(return_value=None),
        initial_state="archiving",
    )

    assert tag_log, "PMO-S8: bkd.merge_tags_and_update MUST be called at least once"
    flat = [t for ts in tag_log for t in ts]

    assert "done" in flat, f"PMO-S8: 'done' MUST be in BKD add tags. Got: {tag_log}"
    assert "via:pr-merge" in flat, f"PMO-S8: 'via:pr-merge' MUST be in BKD add tags. Got: {tag_log}"
    assert "escalated" not in flat, \
        f"PMO-S8: 'escalated' MUST NOT be in BKD add tags. Got: {tag_log}"
    reason_tags = [t for t in flat if t.startswith("reason:")]
    assert not reason_tags, \
        f"PMO-S8: reason:* MUST NOT be in BKD add tags. Got: {reason_tags}"
    assert "github-incident" not in flat, \
        f"PMO-S8: 'github-incident' MUST NOT be in BKD add tags. Got: {tag_log}"

    # BKD statusId SHOULD be 'done' when patched
    if status_log:
        assert any(s == "done" for s in status_log), \
            f"PMO-S8: statusId MUST be 'done' when BKD issue is patched. Got: {status_log}"
