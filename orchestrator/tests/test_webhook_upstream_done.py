"""webhook 收到 session.completed 时把上游 BKD issue 推目标 statusId。

默认 done（防 dev / ci-unit / ci-int / accept / done-archive issue 永远卡 review）。
verifier 判 escalate 例外 → review（resume 路径：用户可在 BKD 看板"待审查"列定位 follow-up）。
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from orchestrator import webhook


class _FakeBKD:
    """只 capture update_issue 调用，其他方法默认空实现。"""

    captured: ClassVar[list[tuple[str, str, str]]] = []
    raise_on_update: ClassVar[bool] = False

    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def update_issue(self, *, project_id, issue_id, status_id):
        if self.raise_on_update:
            raise RuntimeError("BKD down")
        self.captured.append((project_id, issue_id, status_id))


class _FakeBKDFlaky:
    """前 N 次 update_issue 抛异常，第 N+1 次成功。"""

    captured: ClassVar[list[tuple[str, str, str]]] = []
    fail_count: ClassVar[int] = 0

    def __init__(self, *_a, **_kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def update_issue(self, *, project_id, issue_id, status_id):
        if len(self.captured) < self.fail_count:
            self.captured.append((project_id, issue_id, f"fail-{status_id}"))
            raise RuntimeError(f"BKD down (attempt {len(self.captured)})")
        self.captured.append((project_id, issue_id, status_id))


@pytest.mark.asyncio
async def test_pushes_done_for_session_completed(monkeypatch):
    _FakeBKD.captured = []
    _FakeBKD.raise_on_update = False
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKD)

    await webhook._push_upstream_status("proj-1", "issue-abc", "done")

    assert _FakeBKD.captured == [("proj-1", "issue-abc", "done")]


@pytest.mark.asyncio
async def test_pushes_review_for_verifier_escalate(monkeypatch):
    """verifier-decision=escalate → 推 review 让用户在"待审查"列 follow-up 续作业。"""
    _FakeBKD.captured = []
    _FakeBKD.raise_on_update = False
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKD)

    await webhook._push_upstream_status("proj-1", "verifier-issue", "review")

    assert _FakeBKD.captured == [("proj-1", "verifier-issue", "review")]


@pytest.mark.asyncio
async def test_swallows_bkd_errors(monkeypatch):
    """BKD 挂了不能拖挎状态机。"""
    _FakeBKD.captured = []
    _FakeBKD.raise_on_update = True
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKD)

    # 不抛 = 通过
    await webhook._push_upstream_status("proj-1", "issue-abc", "done")


@pytest.mark.asyncio
async def test_retries_on_failure_then_succeeds(monkeypatch):
    """REQ-fix-bkd-sub-issue-status-sync-1777426309：失败时重试，成功则停。"""
    _FakeBKDFlaky.captured = []
    _FakeBKDFlaky.fail_count = 2  # 前 2 次失败，第 3 次成功
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKDFlaky)

    await webhook._push_upstream_status("proj-1", "issue-abc", "done")

    # 总共调了 3 次（2 次失败 + 1 次成功）
    assert len(_FakeBKDFlaky.captured) == 3
    assert _FakeBKDFlaky.captured[0] == ("proj-1", "issue-abc", "fail-done")
    assert _FakeBKDFlaky.captured[1] == ("proj-1", "issue-abc", "fail-done")
    assert _FakeBKDFlaky.captured[2] == ("proj-1", "issue-abc", "done")


@pytest.mark.asyncio
async def test_retries_exhausted_then_swallows(monkeypatch):
    """REQ-fix-bkd-sub-issue-status-sync-1777426309：3 次都失败，最终 swallow 不阻塞状态机。"""
    _FakeBKDFlaky.captured = []
    _FakeBKDFlaky.fail_count = 10  # 永远失败
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKDFlaky)

    # 不抛 = 通过
    await webhook._push_upstream_status("proj-1", "issue-abc", "done")

    # 最多 3 次尝试
    assert len(_FakeBKDFlaky.captured) == 3
