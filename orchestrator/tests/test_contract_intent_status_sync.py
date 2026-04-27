"""Contract tests: escalate action's BKD intent statusId sync.

REQ-bkd-intent-statusid-sync-1777280751

Black-box contract verifying the new intent_status integration in
actions.escalate. Covers:
- BIS-S11: real-escalate path (SESSION_FAILED, retry exhausted) MUST PATCH
  the BKD intent issue with statusId='review' through intent_status helper
- BIS-S12: PR-merged-override path keeps its bundled merge_tags_and_update
  call (status_id='done' inline) and MUST NOT also call intent_status helper

Dev MUST NOT modify these tests to make them pass — fix the implementation
instead. If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest

# ─── Shared helpers (copied + trimmed from test_contract_escalate_pr_merged_override.py) ──


class _FakeBody:
    def __init__(
        self,
        event: str = "session.failed",
        issue_id: str = "issue-test",
        project_id: str = "proj-test",
    ):
        self.event = event
        self.issueId = issue_id
        self.projectId = project_id
        self.issueNumber = None


class _FakeBKD:
    """Default BKD client that records merge_tags_and_update calls."""

    last_calls: ClassVar[list] = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        type(self).last_calls = []
        return self

    async def __aexit__(self, *a):
        return False

    async def follow_up_issue(self, *a, **kw):
        pass

    async def merge_tags_and_update(self, proj, issue_id, *, add=None, remove=None, status_id=None):
        type(self).last_calls.append({
            "issue_id": issue_id,
            "add": list(add or []),
            "remove": list(remove or []),
            "status_id": status_id,
        })


def _patch_httpx_no_pr(monkeypatch):
    """Make GH /pulls always return [] so PR-merged shortcut never fires."""

    class _R:
        def __init__(self, code, payload):
            self.status_code = code
            self._p = payload

        def json(self):
            return self._p

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _R(200, [])

    import httpx as real_httpx

    from orchestrator.actions import escalate as esc_mod
    monkeypatch.setattr(esc_mod, "httpx", MagicMock(
        AsyncClient=_FakeClient,
        HTTPError=real_httpx.HTTPError,
    ))


def _patch_httpx_all_merged(monkeypatch, repo: str):
    """Make GH /pulls return one merged PR for the given repo so override fires."""

    class _R:
        def __init__(self, payload):
            self.status_code = 200
            self._p = payload

        def json(self):
            return self._p

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if f"/repos/{repo}/" in url:
                return _R([{"number": 1, "merged_at": "2026-04-26T08:00:00Z"}])
            return _R([])

    import httpx as real_httpx

    from orchestrator.actions import escalate as esc_mod
    monkeypatch.setattr(esc_mod, "httpx", MagicMock(
        AsyncClient=_FakeClient,
        HTTPError=real_httpx.HTTPError,
    ))


async def _run_escalate(
    monkeypatch,
    *,
    ctx,
    body,
    initial_state="staging-test-running",
):
    """Invoke actions.escalate with mocked deps; capture all relevant side-effects."""
    from orchestrator import gh_incident as ghi
    from orchestrator import intent_status as is_mod
    from orchestrator import k8s_runner as krm
    from orchestrator.actions import escalate as esc_mod
    from orchestrator.state import ReqState
    from orchestrator.store import db
    from orchestrator.store import req_state as rs_mod

    captures: dict = {
        "ctx_updates": [],
        "cas_calls": [],
        "cleanup_calls": [],
        "intent_status_calls": [],
        "open_incident_calls": [],
    }

    state_holder = {"state": ReqState(initial_state)}

    async def _capture_update(pool, req_id, patch):
        captures["ctx_updates"].append(dict(patch))

    async def _capture_cas(pool, req_id, expected, target, event, action, context_patch=None):
        captures["cas_calls"].append({
            "expected": expected,
            "target": target,
            "event": event,
            "action": action,
        })
        # mutate the held state so subsequent rs.get sees the new value
        if state_holder["state"] == expected:
            state_holder["state"] = target
            if context_patch:
                captures["ctx_updates"].append(dict(context_patch))
            return True
        return False

    monkeypatch.setattr(rs_mod, "update_context", _capture_update)
    monkeypatch.setattr(rs_mod, "cas_transition", _capture_cas)

    class _FakeRow:
        @property
        def state(self):
            return state_holder["state"]

        context: ClassVar = {}

    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=_FakeRow()))
    monkeypatch.setattr(db, "get_pool", lambda: MagicMock())

    monkeypatch.setattr(esc_mod, "BKDClient", _FakeBKD)

    async def _capture_open_incident(**kw):
        captures["open_incident_calls"].append(kw)
        return "https://gh/issues/1"

    monkeypatch.setattr(ghi, "open_incident", _capture_open_incident)

    class _FakeController:
        async def cleanup_runner(self, req_id, *, retain_pvc=False):
            captures["cleanup_calls"].append({
                "req_id": req_id, "retain_pvc": retain_pvc,
            })

    monkeypatch.setattr(krm, "get_controller", lambda: _FakeController())

    async def _capture_intent_status(*, project_id, intent_issue_id, terminal_state, source):
        captures["intent_status_calls"].append({
            "project_id": project_id,
            "intent_issue_id": intent_issue_id,
            "terminal_state": terminal_state,
            "source": source,
        })
        return True

    monkeypatch.setattr(is_mod, "patch_terminal_status", _capture_intent_status)
    monkeypatch.setattr(esc_mod.intent_status, "patch_terminal_status", _capture_intent_status)

    result = await esc_mod.escalate(body=body, req_id="REQ-test", tags=[], ctx=ctx)
    return result, captures


# ═══════════════════════════════════════════════════════════════════════
# BIS-S11: real-escalate path (SESSION_FAILED, retry exhausted)
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bis_s11_session_failed_retry_exhausted_patches_review(monkeypatch):
    """BIS-S11: SESSION_FAILED + auto_retry_count=2 -> intent_status.patch_terminal_status(ESCALATED).

    Conditions:
    - body.event = 'session.failed' (canonical SESSION_END signal)
    - ctx.auto_retry_count = 2 (>= _MAX_AUTO_RETRY, no auto-resume)
    - GH probe returns no PR (no PR-merged shortcut)
    -> escalate proceeds with real-escalate path, inner CAS to ESCALATED,
       awaits intent_status helper with terminal_state=ESCALATED
    """
    # Patch via escalate's own settings reference — the helm-reload contract test
    # importlib.reloads `orchestrator.config`, so a fresh `from orchestrator.config
    # import settings as cfg` here would point at a different Settings instance
    # than the one escalate.py captured at import time.
    from orchestrator.actions import escalate as esc_mod
    from orchestrator.state import ReqState

    monkeypatch.setattr(esc_mod.settings, "github_token", "")  # disable GH probe
    monkeypatch.setattr(esc_mod.settings, "default_involved_repos", [])
    monkeypatch.setattr(esc_mod.settings, "gh_incident_repo", "")

    _patch_httpx_no_pr(monkeypatch)

    ctx = {
        "intent_issue_id": "intent-bis-s11",
        "auto_retry_count": 2,  # retry exhausted
    }

    result, captures = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.failed", issue_id="failed-issue",
                       project_id="proj-bis"),
        initial_state="staging-test-running",
    )

    # Contract 1: intent_status helper invoked exactly once with ESCALATED
    assert len(captures["intent_status_calls"]) == 1, (
        f"BIS-S11: helper MUST be called exactly once, got "
        f"{len(captures['intent_status_calls'])}"
    )
    call = captures["intent_status_calls"][0]
    assert call["terminal_state"] == ReqState.ESCALATED, (
        f"BIS-S11: terminal_state MUST be ESCALATED, got {call['terminal_state']!r}"
    )
    assert call["intent_issue_id"] == "intent-bis-s11", (
        f"BIS-S11: intent_issue_id MUST be 'intent-bis-s11', got "
        f"{call['intent_issue_id']!r}"
    )
    assert call["project_id"] == "proj-bis", (
        f"BIS-S11: project_id MUST be 'proj-bis', got {call['project_id']!r}"
    )
    assert call["source"] == "escalate.session_failed", (
        f"BIS-S11: source tag identifies the call site, got {call['source']!r}"
    )

    # Contract 2: real-escalate path actually fired (cas to ESCALATED + cleanup)
    assert any(
        c["target"] == ReqState.ESCALATED for c in captures["cas_calls"]
    ), f"BIS-S11: inner CAS to ESCALATED MUST fire, got {captures['cas_calls']!r}"
    assert captures["cleanup_calls"], (
        "BIS-S11: cleanup_runner MUST be called on real-escalate path"
    )
    assert result.get("escalated") is True


# ═══════════════════════════════════════════════════════════════════════
# BIS-S12: PR-merged-override path keeps existing bundled PATCH unchanged
# ═══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bis_s12_pr_merged_override_does_not_call_intent_status(monkeypatch):
    """BIS-S12: PR-merged shortcut keeps single merge_tags_and_update PATCH.

    Override path:
    - bundles add=['done', 'via:pr-merge'] AND status_id='done' in one BKD call
    - MUST NOT invoke the new intent_status helper for that override CAS
    """
    from orchestrator.actions import escalate as esc_mod

    # Patch via escalate's settings reference (see BIS-S11 comment).
    monkeypatch.setattr(esc_mod.settings, "github_token", "real-token")
    monkeypatch.setattr(esc_mod.settings, "default_involved_repos", [])
    monkeypatch.setattr(esc_mod.settings, "gh_incident_repo", "")

    _patch_httpx_all_merged(monkeypatch, repo="phona/sisyphus")

    ctx = {
        "involved_repos": ["phona/sisyphus"],
        "intent_issue_id": "intent-bis-s12",
        "escalated_reason": "verifier-decision-escalate",
    }

    result, captures = await _run_escalate(
        monkeypatch,
        ctx=ctx,
        body=_FakeBody(event="session.completed", issue_id="issue-bis-s12",
                       project_id="proj-bis"),
        initial_state="accept-running",
    )

    # Contract 1: override fired (result indicates pr-merge)
    assert result.get("completed_via") == "pr-merge", (
        f"BIS-S12: override MUST fire when all PRs merged, got {result!r}"
    )

    # Contract 2: BKD merge_tags_and_update bundles status + tags in one call
    bkd_calls = _FakeBKD.last_calls
    assert len(bkd_calls) >= 1, "override path MUST issue at least one BKD PATCH"
    matched = [
        c for c in bkd_calls
        if "done" in c["add"] and "via:pr-merge" in c["add"]
        and c["status_id"] == "done"
    ]
    assert matched, (
        f"BIS-S12: override MUST issue merge_tags_and_update with "
        f"add=['done', 'via:pr-merge'] AND status_id='done'. Got: {bkd_calls!r}"
    )

    # Contract 3: intent_status helper NOT called on override path
    assert captures["intent_status_calls"] == [], (
        f"BIS-S12: PR-merged override MUST NOT call intent_status helper "
        f"(it bundles status_id into merge_tags_and_update). Got: "
        f"{captures['intent_status_calls']!r}"
    )
