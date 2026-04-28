"""Contract tests for REQ-pr-ready-for-review-notify.

When a REQ transitions into REVIEW_RUNNING and ctx.pr_urls is non-empty,
the BKD intent issue MUST receive a `pr-ready` tag (+ `pr:owner/repo#N`
for each PR URL) via merge_tags_and_update. If pr_urls is empty or
intent_issue_id is missing, no PATCH is made. BKD failures log a warning
but do not block the transition.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator import engine, k8s_runner
from orchestrator.actions import ACTION_META, REGISTRY
from orchestrator.state import Event, ReqState

# ─── Fake pool (same minimal pattern as test_intent_status_sync.py) ───────────

@dataclass
class _FakeReq:
    state: str
    history: list[dict] = field(default_factory=list)
    context: dict = field(default_factory=dict)


class _FakePool:
    def __init__(self, initial: dict[str, _FakeReq]):
        self.rows = initial
        self._next_id = 1

    async def fetchrow(self, sql: str, *args):
        s = sql.strip()
        if s.startswith("SELECT"):
            rid = args[0]
            r = self.rows.get(rid)
            if r is None:
                return None
            return {
                "req_id": rid, "project_id": "proj-x", "state": r.state,
                "history": json.dumps(r.history),
                "context": json.dumps(r.context),
                "created_at": None, "updated_at": None,
            }
        if s.startswith("UPDATE req_state"):
            rid, expected, target, history_json, *rest = args
            r = self.rows.get(rid)
            if r is None or r.state != expected:
                return None
            r.state = target
            r.history.extend(json.loads(history_json))
            if rest:
                try:
                    patch_d = json.loads(rest[0])
                    if isinstance(patch_d, dict):
                        r.context.update(patch_d)
                except (json.JSONDecodeError, TypeError):
                    pass
            return {"req_id": rid}
        if s.startswith("INSERT INTO stage_runs") or s.startswith("UPDATE stage_runs"):
            rid = self._next_id
            self._next_id += 1
            return {"id": rid}
        raise NotImplementedError(s[:60])

    async def execute(self, sql: str, *args):
        s = sql.strip()
        if s.startswith("UPDATE req_state SET context"):
            rid, patch_json = args
            try:
                p = json.loads(patch_json)
            except (json.JSONDecodeError, TypeError):
                return
            r = self.rows.get(rid)
            if r and isinstance(p, dict):
                r.context.update(p)
            return
        if s.startswith("UPDATE stage_runs"):
            return
        raise NotImplementedError(s[:60])


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def _isolated_actions(monkeypatch):
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
def _mock_runner_controller():
    fake = MagicMock()
    fake.cleanup_runner = AsyncMock(return_value=None)
    k8s_runner.set_controller(fake)
    yield fake
    k8s_runner.set_controller(None)


def _make_bkd_mock(merge_tags_and_update: AsyncMock | None = None):
    m = merge_tags_and_update or AsyncMock(return_value=None)
    inst = MagicMock()
    inst.merge_tags_and_update = m
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=None)
    return inst, m


async def _drain_tasks() -> None:
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


# ═══════════════════════════════════════════════════════════════════════════════
# PRN-S1: REVIEW_RUNNING + pr_urls 非空 → pr-ready + pr:owner/repo#N tag 추가
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_prn_s1_review_running_with_pr_urls_adds_pr_ready_tag(
    _isolated_actions, _mock_runner_controller,
):
    inst, merge_mock = _make_bkd_mock()

    async def _stub_invoke_verifier(*, body, req_id, tags, ctx):
        return {"verifier_issue_id": "ver-1"}

    REGISTRY["invoke_verifier_for_pr_ci_fail"] = _stub_invoke_verifier

    pr_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/42"}
    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.PR_CI_RUNNING.value,
            context={
                "intent_issue_id": "intent-abc",
                "pr_urls": pr_urls,
            },
        ),
    })
    body = type("B", (), {
        "issueId": "pr-ci-1", "projectId": "proj-x",
        "event": "check.failed",
    })()

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine.step(
            pool, body=body, req_id="REQ-1", project_id="proj-x", tags=[],
            cur_state=ReqState.PR_CI_RUNNING,
            ctx={"intent_issue_id": "intent-abc", "pr_urls": pr_urls},
            event=Event.PR_CI_FAIL,
        )
        await _drain_tasks()

    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value
    merge_mock.assert_awaited_once()
    added = merge_mock.await_args.kwargs.get("add") or []
    assert "pr-ready" in added
    assert "pr:phona/sisyphus#42" in added


# ═══════════════════════════════════════════════════════════════════════════════
# PRN-S2: pr_urls 빈 dict → BKD PATCH 안함
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_prn_s2_empty_pr_urls_no_patch(
    _isolated_actions, _mock_runner_controller,
):
    inst, merge_mock = _make_bkd_mock()

    async def _stub_invoke_verifier(*, body, req_id, tags, ctx):
        return {"verifier_issue_id": "ver-1"}

    REGISTRY["invoke_verifier_for_staging_test_fail"] = _stub_invoke_verifier

    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.STAGING_TEST_RUNNING.value,
            context={
                "intent_issue_id": "intent-abc",
                "pr_urls": {},
            },
        ),
    })
    body = type("B", (), {
        "issueId": "staging-1", "projectId": "proj-x",
        "event": "test.failed",
    })()

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine.step(
            pool, body=body, req_id="REQ-1", project_id="proj-x", tags=[],
            cur_state=ReqState.STAGING_TEST_RUNNING,
            ctx={"intent_issue_id": "intent-abc", "pr_urls": {}},
            event=Event.STAGING_TEST_FAIL,
        )
        await _drain_tasks()

    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value
    merge_mock.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════════
# PRN-S3: pr_urls 없음(None) → BKD PATCH 안함
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_prn_s3_no_pr_urls_no_patch(
    _isolated_actions, _mock_runner_controller,
):
    inst, merge_mock = _make_bkd_mock()

    async def _stub_invoke_verifier(*, body, req_id, tags, ctx):
        return {"verifier_issue_id": "ver-1"}

    REGISTRY["invoke_verifier_for_spec_lint_fail"] = _stub_invoke_verifier

    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.SPEC_LINT_RUNNING.value,
            context={"intent_issue_id": "intent-abc"},
        ),
    })
    body = type("B", (), {
        "issueId": "lint-1", "projectId": "proj-x",
        "event": "lint.failed",
    })()

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine.step(
            pool, body=body, req_id="REQ-1", project_id="proj-x", tags=[],
            cur_state=ReqState.SPEC_LINT_RUNNING,
            ctx={"intent_issue_id": "intent-abc"},
            event=Event.SPEC_LINT_FAIL,
        )
        await _drain_tasks()

    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value
    merge_mock.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════════
# PRN-S4: BKD PATCH 5xx → log warning + transition 不阻塞
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_prn_s4_bkd_patch_failure_logs_warning_no_rollback(
    _isolated_actions, _mock_runner_controller,
):
    merge_mock = AsyncMock(side_effect=RuntimeError("bkd 503"))
    inst, _ = _make_bkd_mock(merge_tags_and_update=merge_mock)

    async def _stub_invoke_verifier(*, body, req_id, tags, ctx):
        return {"verifier_issue_id": "ver-1"}

    REGISTRY["invoke_verifier_for_pr_ci_fail"] = _stub_invoke_verifier

    pr_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/7"}
    pool = _FakePool({
        "REQ-1": _FakeReq(
            state=ReqState.PR_CI_RUNNING.value,
            context={"intent_issue_id": "intent-abc", "pr_urls": pr_urls},
        ),
    })
    body = type("B", (), {
        "issueId": "pr-ci-1", "projectId": "proj-x",
        "event": "check.failed",
    })()

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        result = await engine.step(
            pool, body=body, req_id="REQ-1", project_id="proj-x", tags=[],
            cur_state=ReqState.PR_CI_RUNNING,
            ctx={"intent_issue_id": "intent-abc", "pr_urls": pr_urls},
            event=Event.PR_CI_FAIL,
        )
        await _drain_tasks()

    # 상태 전이는 성공해야 함
    assert pool.rows["REQ-1"].state == ReqState.REVIEW_RUNNING.value
    assert result["next_state"] == ReqState.REVIEW_RUNNING.value
    # PATCH는 시도됐어야 함 (BKD 실패가 전이를 막으면 안됨)
    merge_mock.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════════
# PRN-S5: 헬퍼 직접 호출 — intent_issue_id 없으면 no-op
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_prn_s5_helper_no_op_missing_intent_issue_id():
    inst, merge_mock = _make_bkd_mock()
    pr_urls = {"phona/sisyphus": "https://github.com/phona/sisyphus/pull/1"}

    with patch("orchestrator.engine.BKDClient", return_value=inst):
        await engine._tag_intent_pr_ready(
            project_id="proj-x",
            intent_issue_id=None,
            pr_urls=pr_urls,
            req_id="REQ-1",
        )

    merge_mock.assert_not_awaited()
