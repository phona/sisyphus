"""webhook 收到 session.completed 时把上游 BKD issue 自动推到 done。

防 dev / ci-unit / ci-int / accept / done-archive issue 永远卡 review 状态。
"""
from __future__ import annotations

import pytest

from orchestrator import webhook


class _FakeBKD:
    """只 capture update_issue 调用，其他方法默认空实现。"""

    captured: list[tuple[str, str, str]] = []
    raise_on_update: bool = False

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


@pytest.mark.asyncio
async def test_pushes_done_for_session_completed(monkeypatch):
    _FakeBKD.captured = []
    _FakeBKD.raise_on_update = False
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKD)

    await webhook._push_upstream_done("proj-1", "issue-abc")

    assert _FakeBKD.captured == [("proj-1", "issue-abc", "done")]


@pytest.mark.asyncio
async def test_swallows_bkd_errors(monkeypatch):
    """BKD 挂了不能拖挎状态机。"""
    _FakeBKD.captured = []
    _FakeBKD.raise_on_update = True
    monkeypatch.setattr(webhook, "BKDClient", _FakeBKD)

    # 不抛 = 通过
    await webhook._push_upstream_done("proj-1", "issue-abc")
