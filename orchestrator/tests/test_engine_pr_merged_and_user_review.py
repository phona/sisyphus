"""Engine step tests for PR_MERGED + USER_REVIEW_FIX transitions.

Closes the remaining 4 transitions that had sweep-only coverage in ERT-S9:
- PENDING_USER_REVIEW + PR_MERGED  → ARCHIVING
- REVIEW_RUNNING    + PR_MERGED  → ARCHIVING
- PR_CI_RUNNING     + PR_MERGED  → ARCHIVING
- PENDING_USER_REVIEW + USER_REVIEW_FIX → ESCALATED
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from test_engine import FakePool, FakeReq, _drain_tasks  # type: ignore[import-not-found]

from orchestrator import engine, k8s_runner
from orchestrator.actions import ACTION_META, REGISTRY
from orchestrator.state import Event, ReqState


@pytest.fixture
def stub_actions():
    saved_reg = dict(REGISTRY)
    saved_meta = dict(ACTION_META)
    REGISTRY.clear()
    ACTION_META.clear()
    yield REGISTRY
    REGISTRY.clear()
    ACTION_META.clear()
    REGISTRY.update(saved_reg)
    ACTION_META.update(saved_meta)


@pytest.fixture
def mock_runner_controller():
    fake = MagicMock()
    fake.cleanup_runner = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


def _body(**attrs):
    return type("B", (), attrs)()


def _make_recorder(name: str, calls: list):
    async def _rec(*, body, req_id, tags, ctx):
        calls.append({"action": name, "tags": list(tags or []), "ctx": dict(ctx or {})})
        return {"ok": True}
    return _rec


# ─── PR_MERGED transitions ────────────────────────────────────────────────


_PR_MERGED_CASES = [
    pytest.param(
        ReqState.PENDING_USER_REVIEW, "pmh-s1-pending-user-review",
        id="PMH-S1-pending-user-review",
    ),
    pytest.param(
        ReqState.REVIEW_RUNNING, "pmh-s2-review-running",
        id="PMH-S2-review-running",
    ),
    pytest.param(
        ReqState.PR_CI_RUNNING, "pmh-s3-pr-ci-running",
        id="PMH-S3-pr-ci-running",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("cur_state,issue_id", _PR_MERGED_CASES)
async def test_pr_merged_advances_to_archiving(
    stub_actions, mock_runner_controller, cur_state, issue_id,
):
    """PR merged by reviewer → skip remaining gates → archive."""
    calls: list = []
    stub_actions["done_archive"] = _make_recorder("done_archive", calls)

    pool = FakePool({"REQ-1": FakeReq(state=cur_state.value)})
    body = _body(issueId=issue_id, projectId="p", event="pr.merged")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["pr-merged", "REQ-1"],
        cur_state=cur_state, ctx={}, event=Event.PR_MERGED,
    )
    await _drain_tasks()

    assert result["action"] == "done_archive"
    assert result["next_state"] == ReqState.ARCHIVING.value
    assert pool.rows["REQ-1"].state == ReqState.ARCHIVING.value
    assert len(calls) == 1
    # ARCHIVING is non-terminal → no cleanup yet
    mock_runner_controller.cleanup_runner.assert_not_awaited()


# ─── USER_REVIEW_FIX ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_review_fix_escalates(stub_actions, mock_runner_controller):
    """PENDING_USER_REVIEW + USER_REVIEW_FIX → ESCALATED + cleanup."""
    calls: list = []
    stub_actions["escalate"] = _make_recorder("escalate", calls)

    pool = FakePool({"REQ-1": FakeReq(state=ReqState.PENDING_USER_REVIEW.value)})
    body = _body(issueId="usr-1", projectId="p", event="issue.updated")

    result = await engine.step(
        pool, body=body, req_id="REQ-1", project_id="p",
        tags=["user-review", "REQ-1"],
        cur_state=ReqState.PENDING_USER_REVIEW, ctx={}, event=Event.USER_REVIEW_FIX,
    )
    await _drain_tasks()

    assert result["action"] == "escalate"
    assert result["next_state"] == ReqState.ESCALATED.value
    assert pool.rows["REQ-1"].state == ReqState.ESCALATED.value
    assert len(calls) == 1
    mock_runner_controller.cleanup_runner.assert_awaited_once_with(
        "REQ-1", retain_pvc=True,
    )
