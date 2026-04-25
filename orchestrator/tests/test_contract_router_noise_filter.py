"""Contract tests for early webhook noise filter (REQ-router-noise-filter-1777109307).

Black-box behavioral contracts derived from:
  openspec/changes/REQ-router-noise-filter-1777109307/specs/router-noise-filter/spec.md

Scenarios covered:
  RNF-S1  issue.updated 无 REQ tag 无 intent tag → skip + mark_processed + 不 obs/derive/engine
  RNF-S2  issue.updated 含 REQ tag → 走下游（engine.step 被调）
  RNF-S3  issue.updated 仅 intent:intake → 走下游（INTENT_INTAKE fire）
  RNF-S4  issue.updated 仅 intent:analyze → 走下游（INTENT_ANALYZE fire）
  RNF-S5  session.completed 无 REQ tag → 旧 filter 仍生效
"""
from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock

import pytest


class _FakePool:
    """模拟 asyncpg pool 的最小实现：fetchrow / execute 不报错。"""

    def __init__(self, fetchrow_returns=()):
        self._returns = list(fetchrow_returns)
        self._pos = 0
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        if self._pos < len(self._returns):
            val = self._returns[self._pos]
            self._pos += 1
            return val
        return None

    async def execute(self, sql: str, *args):
        self.execute_calls.append((sql, args))


def _make_request(*, event: str, tags: list[str], issue_id: str = "issue-x",
                  project_id: str = "proj-x", execution_id: str = "exec-x"):
    class _Req:
        headers: ClassVar = {"authorization": "Bearer test-webhook-token"}

        async def json(self):
            return {
                "event": event,
                "issueId": issue_id,
                "projectId": project_id,
                "executionId": execution_id,
                "tags": tags,
            }

    return _Req()


def _wire_common(monkeypatch):
    """Wire the minimum mocks shared by all scenarios."""
    import orchestrator.observability as obs
    from orchestrator import engine, webhook
    from orchestrator import router as router_lib
    from orchestrator.state import ReqState
    from orchestrator.store import db, dedup
    from orchestrator.store import req_state as rs_mod

    obs_calls: list = []
    derive_calls: list = []
    engine_calls: list = []
    mark_calls: list = []

    monkeypatch.setattr(dedup, "check_and_record", AsyncMock(return_value="new"))
    monkeypatch.setattr(dedup, "mark_processed",
                        AsyncMock(side_effect=lambda *a, **kw: mark_calls.append(a)))
    monkeypatch.setattr(db, "get_pool", lambda: _FakePool())
    monkeypatch.setattr(obs, "record_event",
                        AsyncMock(side_effect=lambda *a, **kw: obs_calls.append((a, kw))))

    # BKDClient: never expected to be hit in the issue.updated branches because the
    # body always carries tags. Make it raise loud if accidentally instantiated.
    class _BKD:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get_issue(self, *a, **kw):
            class R:
                tags: ClassVar = []
            return R()
        async def update_issue(self, *a, **kw): pass
    monkeypatch.setattr(webhook, "BKDClient", _BKD)

    # Wrap derive_event so we can assert it was/wasn't called without breaking real logic.
    real_derive = router_lib.derive_event

    def _wrapped_derive(evt, tags, *a, **kw):
        derive_calls.append((evt, list(tags)))
        return real_derive(evt, tags, *a, **kw)
    monkeypatch.setattr(router_lib, "derive_event", _wrapped_derive)
    monkeypatch.setattr(webhook.router_lib, "derive_event", _wrapped_derive)

    class _Row:
        state = ReqState.INIT
        context: ClassVar = {}

    monkeypatch.setattr(rs_mod, "get", AsyncMock(return_value=_Row()))
    monkeypatch.setattr(rs_mod, "insert_init", AsyncMock())
    monkeypatch.setattr(rs_mod, "update_context", AsyncMock())
    monkeypatch.setattr(engine, "step",
                        AsyncMock(side_effect=lambda *a, **kw:
                                  engine_calls.append((a, kw)) or {"action": "ok"}))

    return {
        "obs_calls": obs_calls,
        "derive_calls": derive_calls,
        "engine_calls": engine_calls,
        "mark_calls": mark_calls,
    }


# ─── RNF-S1: issue.updated 无 REQ tag 无 intent tag → skip ─────────────────

@pytest.mark.asyncio
async def test_rnf_s1_issue_updated_no_req_no_intent_skipped(monkeypatch):
    """
    RNF-S1: body.event="issue.updated" + tags 不含 REQ-* 也不含 intent:intake/analyze
    → 早期 noise filter 命中：return skip + mark_processed 调过 + obs.record_event 没调过
    + derive_event 没调过 + engine.step 没调过。
    """
    from orchestrator import webhook

    spy = _wire_common(monkeypatch)

    req = _make_request(event="issue.updated", tags=["bug", "frontend"])
    result = await webhook.webhook(req)

    assert result == {
        "action": "skip",
        "reason": "issue.updated without REQ or intent tag",
    }, f"Expected noise-skip dict, got {result!r}"

    assert len(spy["mark_calls"]) == 1, (
        f"dedup.mark_processed must be called exactly once on noise-skip, "
        f"got {len(spy['mark_calls'])}"
    )

    assert spy["obs_calls"] == [], (
        f"obs.record_event must NOT be called on noise events; "
        f"got calls: {spy['obs_calls']!r}"
    )

    assert spy["derive_calls"] == [], (
        f"router.derive_event must NOT be called on noise-skip path; "
        f"got calls: {spy['derive_calls']!r}"
    )

    assert spy["engine_calls"] == [], (
        f"engine.step must NOT be called on noise-skip path; "
        f"got calls: {spy['engine_calls']!r}"
    )


# ─── RNF-S2: issue.updated 含 REQ tag → 走下游 ────────────────────────────

@pytest.mark.asyncio
async def test_rnf_s2_issue_updated_with_req_tag_passes(monkeypatch):
    """
    RNF-S2: body.event="issue.updated" + tags 含 REQ-* → noise filter 不命中，
    handler 继续走下游：engine.step 至少被调用一次。
    """
    from orchestrator import webhook

    spy = _wire_common(monkeypatch)

    # tags 含 REQ-* + result tag → router.derive_event 会返回主链事件
    # （staging-test 走 race fallback 路径），engine.step 必定被调用
    req = _make_request(
        event="issue.updated",
        tags=["REQ-rnf-s2", "staging-test", "result:pass"],
    )
    result = await webhook.webhook(req)

    assert result != {"action": "skip", "reason": "issue.updated without REQ or intent tag"}, (
        f"REQ-tagged issue.updated must NOT hit noise filter; got {result!r}"
    )

    assert len(spy["engine_calls"]) >= 1, (
        "engine.step must be called when issue.updated carries a REQ-* tag "
        "(noise filter must let it through)"
    )


# ─── RNF-S3: issue.updated 仅 intent:intake → 走下游 ──────────────────────

@pytest.mark.asyncio
async def test_rnf_s3_issue_updated_with_intent_intake_passes(monkeypatch):
    """
    RNF-S3: body.event="issue.updated" + tags=["intent:intake"]（无 REQ-*）
    → noise filter 不命中（intent 入口必须能 fire INTENT_INTAKE）。
    """
    from orchestrator import webhook

    spy = _wire_common(monkeypatch)

    req = _make_request(event="issue.updated", tags=["intent:intake"])
    result = await webhook.webhook(req)

    assert result != {"action": "skip", "reason": "issue.updated without REQ or intent tag"}, (
        f"intent:intake-tagged issue.updated must NOT hit noise filter; got {result!r}"
    )

    # derive_event must run to map intent:intake → INTENT_INTAKE
    assert spy["derive_calls"], (
        "router.derive_event must be called when intent:intake tag is present "
        "(noise filter must let it through)"
    )


# ─── RNF-S4: issue.updated 仅 intent:analyze → 走下游 ─────────────────────

@pytest.mark.asyncio
async def test_rnf_s4_issue_updated_with_intent_analyze_passes(monkeypatch):
    """
    RNF-S4: body.event="issue.updated" + tags=["intent:analyze"]（无 REQ-*）
    → noise filter 不命中，derive_event 应映射出 INTENT_ANALYZE。
    """
    from orchestrator import webhook

    spy = _wire_common(monkeypatch)

    req = _make_request(event="issue.updated", tags=["intent:analyze"])
    result = await webhook.webhook(req)

    assert result != {"action": "skip", "reason": "issue.updated without REQ or intent tag"}, (
        f"intent:analyze-tagged issue.updated must NOT hit noise filter; got {result!r}"
    )

    assert spy["derive_calls"], (
        "router.derive_event must be called when intent:analyze tag is present"
    )


# ─── RNF-S5: session.completed 无 REQ tag → 旧 filter 仍生效 ──────────────

@pytest.mark.asyncio
async def test_rnf_s5_session_completed_no_req_still_skipped(monkeypatch):
    """
    RNF-S5: 既有 session.completed-without-REQ-tag filter 行为回归不变。
    body.event="session.completed" + tags 不含 REQ-* → return skip + mark_processed 调过
    + engine.step 没调过。
    """
    from orchestrator import webhook

    spy = _wire_common(monkeypatch)

    # session.completed: webhook 会调 BKDClient.get_issue 重拉 tags（line 132）。
    # _wire_common 的 _BKD.get_issue 默认返空 tags，所以 effective tags = []，
    # noise filter 命中。
    req = _make_request(event="session.completed", tags=[])
    result = await webhook.webhook(req)

    assert result == {
        "action": "skip",
        "reason": "session event without REQ tag",
    }, f"Expected legacy session-skip dict, got {result!r}"

    assert len(spy["mark_calls"]) == 1, (
        f"dedup.mark_processed must be called exactly once on session noise-skip, "
        f"got {len(spy['mark_calls'])}"
    )

    assert spy["engine_calls"] == [], (
        "engine.step must NOT be called on session.completed noise-skip path"
    )
