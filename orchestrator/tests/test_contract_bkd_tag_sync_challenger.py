"""Contract tests: BKD tag sync race fix + admission rejection UX.
REQ-fix-bkd-tag-sync-race-1777427340

Black-box challenger. Derived from:
  openspec/changes/REQ-fix-bkd-tag-sync-race-1777427340/specs/bkd-tag-sync/spec.md

Scenarios covered: BKD-S1 through BKD-S5.

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from orchestrator.bkd import Issue
from orchestrator.bkd_rest import BKDRestClient

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _resp(status: int, body: dict) -> httpx.Response:
    """Build a BKD-envelope HTTP response."""
    if status < 400:
        text = json.dumps({"success": True, "data": body})
    else:
        text = json.dumps({"success": False, "error": body})
    return httpx.Response(status_code=status, text=text)


def _issue_dict(issue_id: str, tags: list[str], **extra) -> dict:
    return {
        "id": issue_id,
        "projectId": "p1",
        "issueNumber": 1,
        "title": "t",
        "statusId": "working",
        "tags": tags,
        "sessionStatus": "running",
        **extra,
    }


def _make_body(*, project_id: str = "nnvxh8wj", issue_id: str = "issue-X"):
    return SimpleNamespace(projectId=project_id, issueId=issue_id, title="t")


# ═══════════════════════════════════════════════════════════════════════════════
# BKD-S1, BKD-S2: merge_tags_and_update optimistic-lock retry contract
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bkd_s1_single_caller_updates_tags_successfully():
    """BKD-S1: merge_tags_and_update on a quiescent issue succeeds on first try."""
    calls: list[dict] = []

    class FakeHttp:
        async def get(self, url, headers=None):
            calls.append({"method": "get", "url": url})
            return _resp(200, _issue_dict("i1", ["existing", "REQ-9"]))

        async def patch(self, url, headers=None, json=None):
            calls.append({"method": "patch", "url": url, "json": json})
            return _resp(200, _issue_dict("i1", json.get("tags", [])))

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]

    result = await client.merge_tags_and_update(
        "p1", "i1", add=["ci-passed"], remove=["existing"],
    )

    # Must return an Issue-like object with the merged tags.
    assert isinstance(result, Issue)
    assert set(result.tags) == {"REQ-9", "ci-passed"}

    # Exactly one get → one patch cycle (no retry needed).
    assert len([c for c in calls if c["method"] == "get"]) == 1
    assert len([c for c in calls if c["method"] == "patch"]) == 1
    patch_call = next(c for c in calls if c["method"] == "patch")
    assert set(patch_call["json"]["tags"]) == {"REQ-9", "ci-passed"}


@pytest.mark.asyncio
async def test_bkd_s2_race_detected_and_resolved_by_retry():
    """BKD-S2: concurrent overwrite detected → re-read → retry → final merged list."""
    # Simulate caller-B's point of view:
    #   1. initial get → ["a"]
    #   2. patch(add=["c"]) → server returns ["a","b"] (caller-A won)
    #   3. verify get → ["a","b"]
    #   4. retry get → ["a","b"]
    #   5. patch(add=["c"]) → server returns ["a","b","c"] ✓
    get_seq = [
        _issue_dict("i1", ["a"]),          # initial read
        _issue_dict("i1", ["a", "b"]),     # verify-get after race detected
        _issue_dict("i1", ["a", "b"]),     # re-read on retry
    ]
    get_iter = iter(get_seq)

    patch_seq = [
        _issue_dict("i1", ["a", "b"]),     # first write overwritten by caller-A
        _issue_dict("i1", ["a", "b", "c"]),  # second write succeeds
    ]
    patch_iter = iter(patch_seq)

    calls: list[dict] = []

    class FakeHttp:
        async def get(self, url, headers=None):
            calls.append({"method": "get", "url": url})
            return _resp(200, next(get_iter))

        async def patch(self, url, headers=None, json=None):
            calls.append({"method": "patch", "url": url, "json": json})
            return _resp(200, next(patch_iter))

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]

    result = await client.merge_tags_and_update("p1", "i1", add=["c"])

    assert isinstance(result, Issue)
    assert set(result.tags) == {"a", "b", "c"}

    # Must have retried: 3 GETs (initial + verify + retry) + 2 PATCHes.
    assert len([c for c in calls if c["method"] == "get"]) == 3
    assert len([c for c in calls if c["method"] == "patch"]) == 2

    # Second patch must contain the reconciled tag list.
    patch_calls = [c for c in calls if c["method"] == "patch"]
    assert set(patch_calls[1]["json"]["tags"]) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_bkd_s2_retry_cap_at_three_attempts():
    """BKD-S2 retry loop MUST be capped at 3 attempts; on exhaustion log warning and return last result."""
    # Server *always* returns a different tag list than expected → endless race.
    calls: list[dict] = []

    class FakeHttp:
        async def get(self, url, headers=None):
            calls.append({"method": "get", "url": url})
            # Always return a fixed "other" tag set.
            return _resp(200, _issue_dict("i1", ["x", "y"]))

        async def patch(self, url, headers=None, json=None):
            calls.append({"method": "patch", "url": url, "json": json})
            # Patch response also diverges from what we sent.
            return _resp(200, _issue_dict("i1", ["x", "y"]))

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]

    result = await client.merge_tags_and_update("p1", "i1", add=["z"])

    # Must not loop forever; must return the last result after exhaustion.
    assert isinstance(result, Issue)
    # Exactly 3 attempts = up to 3 gets (initial + 2 retries) + 3 patches.
    # Or 3 gets + 3 patches depending on impl (initial + verify per attempt).
    get_count = len([c for c in calls if c["method"] == "get"])
    patch_count = len([c for c in calls if c["method"] == "patch"])
    assert get_count <= 6  # 3 attempts × (get + verify-get) at worst
    assert patch_count == 3


# ═══════════════════════════════════════════════════════════════════════════════
# BKD-S3..S5: start_execute admission rejection UX contract
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_bkd_s3_inflight_cap_exceeded_triggers_visible_feedback(monkeypatch):
    """BKD-S3: admission deny inflight-cap → intent issue gets reason:rate-limit tag + follow-up."""
    from orchestrator.actions import start_execute
    from orchestrator.admission import AdmissionDecision
    from orchestrator.state import Event

    monkeypatch.setattr(
        start_execute, "check_admission",
        AsyncMock(return_value=AdmissionDecision(
            admit=False, reason="inflight-cap-exceeded:10/10",
        )),
    )
    monkeypatch.setattr(start_execute.db, "get_pool", lambda: object())
    update_ctx = AsyncMock()
    monkeypatch.setattr(start_execute.req_state, "update_context", update_ctx)

    # Patch runner so we can assert it is NOT invoked.
    exec_fn = AsyncMock(return_value=SimpleNamespace(stdout="", stderr="", exit_code=0, duration_sec=0.1))
    monkeypatch.setattr(start_execute.k8s_runner, "get_controller", lambda: SimpleNamespace(
        ensure_runner=AsyncMock(), exec_in_runner=exec_fn,
    ))

    merge_tags = AsyncMock(return_value=None)
    follow_up = AsyncMock(return_value=None)
    bkd_instance = MagicMock()
    bkd_instance.merge_tags_and_update = merge_tags
    bkd_instance.follow_up_issue = follow_up
    bkd_instance.__aenter__ = AsyncMock(return_value=bkd_instance)
    bkd_instance.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(start_execute, "BKDClient", lambda *a, **kw: bkd_instance)

    rv = await start_execute.start_execute(
        body=_make_body(), req_id="REQ-X", tags=[], ctx={},
    )

    # State machine must still escalate.
    assert rv["emit"] == Event.VERIFY_ESCALATE.value
    # BKD intent issue must receive the visible tag.
    merge_tags.assert_awaited_once()
    assert "reason:rate-limit" in merge_tags.await_args.kwargs["add"]
    # Human-readable follow-up with the full reason string.
    follow_up.assert_awaited_once()
    follow_prompt = follow_up.await_args.kwargs["prompt"]
    assert "inflight-cap-exceeded:10/10" in follow_prompt


@pytest.mark.asyncio
async def test_bkd_s4_disk_pressure_exceeded_triggers_visible_feedback(monkeypatch):
    """BKD-S4: admission deny disk-pressure → intent issue gets reason:rate-limit tag + follow-up."""
    from orchestrator.actions import start_execute
    from orchestrator.admission import AdmissionDecision
    from orchestrator.state import Event

    monkeypatch.setattr(
        start_execute, "check_admission",
        AsyncMock(return_value=AdmissionDecision(
            admit=False, reason="disk-pressure:0.85/0.75",
        )),
    )
    monkeypatch.setattr(start_execute.db, "get_pool", lambda: object())
    update_ctx = AsyncMock()
    monkeypatch.setattr(start_execute.req_state, "update_context", update_ctx)
    monkeypatch.setattr(start_execute.k8s_runner, "get_controller", lambda: SimpleNamespace(
        ensure_runner=AsyncMock(), exec_in_runner=AsyncMock(),
    ))

    merge_tags = AsyncMock(return_value=None)
    follow_up = AsyncMock(return_value=None)
    bkd_instance = MagicMock()
    bkd_instance.merge_tags_and_update = merge_tags
    bkd_instance.follow_up_issue = follow_up
    bkd_instance.__aenter__ = AsyncMock(return_value=bkd_instance)
    bkd_instance.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(start_execute, "BKDClient", lambda *a, **kw: bkd_instance)

    rv = await start_execute.start_execute(
        body=_make_body(), req_id="REQ-X", tags=[], ctx={},
    )

    assert rv["emit"] == Event.VERIFY_ESCALATE.value
    merge_tags.assert_awaited_once()
    assert "reason:rate-limit" in merge_tags.await_args.kwargs["add"]
    follow_up.assert_awaited_once()
    follow_prompt = follow_up.await_args.kwargs["prompt"]
    assert "disk-pressure:0.85/0.75" in follow_prompt


@pytest.mark.asyncio
async def test_bkd_s5_bkd_sync_failure_does_not_block_escalation(monkeypatch):
    """BKD-S5: if merge_tags_and_update raises, start_execute MUST still emit VERIFY_ESCALATE."""
    from orchestrator.actions import start_execute
    from orchestrator.admission import AdmissionDecision
    from orchestrator.state import Event

    monkeypatch.setattr(
        start_execute, "check_admission",
        AsyncMock(return_value=AdmissionDecision(
            admit=False, reason="inflight-cap-exceeded:10/10",
        )),
    )
    monkeypatch.setattr(start_execute.db, "get_pool", lambda: object())
    update_ctx = AsyncMock()
    monkeypatch.setattr(start_execute.req_state, "update_context", update_ctx)
    monkeypatch.setattr(start_execute.k8s_runner, "get_controller", lambda: SimpleNamespace(
        ensure_runner=AsyncMock(), exec_in_runner=AsyncMock(),
    ))

    # merge_tags_and_update raises → must be fail-open.
    merge_tags = AsyncMock(side_effect=RuntimeError("BKD unreachable"))
    follow_up = AsyncMock(return_value=None)
    bkd_instance = MagicMock()
    bkd_instance.merge_tags_and_update = merge_tags
    bkd_instance.follow_up_issue = follow_up
    bkd_instance.__aenter__ = AsyncMock(return_value=bkd_instance)
    bkd_instance.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(start_execute, "BKDClient", lambda *a, **kw: bkd_instance)

    rv = await start_execute.start_execute(
        body=_make_body(), req_id="REQ-X", tags=[], ctx={},
    )

    # Escalation MUST proceed regardless of BKD sync failure.
    assert rv["emit"] == Event.VERIFY_ESCALATE.value
    merge_tags.assert_awaited()  # at least attempted
