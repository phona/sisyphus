"""actions/start_challenger.py 单测 — 主要覆盖 REQ-ux-tags-injection-1777257283
hint tag 转发到新建 challenger issue 的 tags 数组。

不测 BKD REST 主体（在 test_bkd_rest.py），不测 pr_links discover 主体
（在 test_pr_links.py），只测 start_challenger 把 4 段 tag（role / REQ id /
parent-id / pr-link / hint）拼对了。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.actions import start_challenger


@pytest.fixture(autouse=True)
def _mock_dispatch_slugs(monkeypatch):
    """Prevent real DB calls introduced by REQ-427 slug dedup."""
    monkeypatch.setattr(start_challenger.db, "get_pool", MagicMock(return_value=object()))
    monkeypatch.setattr(start_challenger.dispatch_slugs, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(start_challenger.dispatch_slugs, "put", AsyncMock())


@dataclass
class FakeIssue:
    id: str
    tags: list = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


def _make_body(*, project_id="p", issue_id="src-1"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": "session.completed", "title": "T",
        "tags": [], "issueNumber": None,
    })()


def _patch_bkd(monkeypatch, *, create_issue=None):
    fake = AsyncMock()
    fake.create_issue = create_issue or AsyncMock(return_value=FakeIssue(id="ch-new-1"))
    fake.follow_up_issue = AsyncMock(return_value={})
    fake.update_issue = AsyncMock(return_value=FakeIssue(id="ch-new-1"))

    @asynccontextmanager
    async def _ctx(*a, **kw):
        yield fake

    monkeypatch.setattr("orchestrator.actions.start_challenger.BKDClient", _ctx)
    return fake


def _patch_pr_links_empty(monkeypatch):
    """让 ensure_pr_links_in_ctx 返回 [] —— 默认 case 没 PR-link tag 干扰断言。"""
    monkeypatch.setattr(
        start_challenger.pr_links, "ensure_pr_links_in_ctx",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        start_challenger.pr_links, "pr_link_tags",
        lambda links: [],
    )


# ─── REQ-ux-tags-injection: forward hint tags ────────────────────────────────


@pytest.mark.asyncio
async def test_start_challenger_forwards_user_hint_tags(monkeypatch):
    """tags 含 repo: + ux: → create_issue 的 tags kwarg 把它们追加到末尾。"""
    fake = _patch_bkd(monkeypatch)
    _patch_pr_links_empty(monkeypatch)

    out = await start_challenger.start_challenger(
        body=_make_body(issue_id="analyze-1"),
        req_id="REQ-X",
        tags=["analyze", "REQ-X", "repo:phona/foo", "ux:fast-track"],
        ctx={"branch": "feat/REQ-X"},
    )
    assert out["challenger_issue_id"] == "ch-new-1"

    fake.create_issue.assert_awaited_once()
    _, kwargs = fake.create_issue.await_args
    tags = kwargs["tags"]
    # role / REQ id / parent-id 在前
    assert tags[0] == "challenger"
    assert tags[1] == "REQ-X"
    assert tags[2] == "parent-id:analyze-1"
    # 后面是 hint（无 pr-link）
    assert tags[3:] == ["repo:phona/foo", "ux:fast-track"]


@pytest.mark.asyncio
async def test_start_challenger_strips_managed_from_forwarded(monkeypatch):
    """body.tags 含 stale role / result / pr / intent / decision → 只剩 hint 转发。"""
    fake = _patch_bkd(monkeypatch)
    _patch_pr_links_empty(monkeypatch)

    await start_challenger.start_challenger(
        body=_make_body(issue_id="analyze-1"),
        req_id="REQ-X",
        tags=[
            "analyze", "REQ-X", "result:pass", "challenger",
            "intent:analyze", "decision:eyJ...", "verify:foo",
            "pr:phona/foo#1", "repo:phona/foo",
        ],
        ctx={},
    )
    _, kwargs = fake.create_issue.await_args
    tags = kwargs["tags"]
    # 基础 3 段 + 仅 repo hint
    assert tags == [
        "challenger", "REQ-X", "parent-id:analyze-1",
        "repo:phona/foo",
    ]
    assert tags.count("challenger") == 1
    assert tags.count("REQ-X") == 1
    assert "result:pass" not in tags
    assert "intent:analyze" not in tags
    assert "pr:phona/foo#1" not in tags


@pytest.mark.asyncio
async def test_start_challenger_keeps_pr_link_then_hints(monkeypatch):
    """pr-link tag 来自 pr_links 模块；hint 跟在它后面。"""
    fake = _patch_bkd(monkeypatch)
    monkeypatch.setattr(
        start_challenger.pr_links, "ensure_pr_links_in_ctx",
        AsyncMock(return_value=["fake-prlink"]),  # 占位，下面 pr_link_tags 直接产 tag
    )
    monkeypatch.setattr(
        start_challenger.pr_links, "pr_link_tags",
        lambda links: ["pr:phona/foo#42"],
    )

    await start_challenger.start_challenger(
        body=_make_body(issue_id="analyze-1"),
        req_id="REQ-X",
        tags=["analyze", "REQ-X", "repo:phona/foo", "ux:fast-track"],
        ctx={},
    )
    _, kwargs = fake.create_issue.await_args
    assert kwargs["tags"] == [
        "challenger", "REQ-X", "parent-id:analyze-1",
        "pr:phona/foo#42",
        "repo:phona/foo", "ux:fast-track",
    ]


@pytest.mark.asyncio
async def test_start_challenger_no_hint_keeps_base_tags(monkeypatch):
    """body.tags 全是 sisyphus-managed → tags 数组不带 hint，向后兼容。"""
    fake = _patch_bkd(monkeypatch)
    _patch_pr_links_empty(monkeypatch)

    await start_challenger.start_challenger(
        body=_make_body(issue_id="analyze-1"),
        req_id="REQ-X",
        tags=["analyze", "REQ-X"],
        ctx={},
    )
    _, kwargs = fake.create_issue.await_args
    assert kwargs["tags"] == ["challenger", "REQ-X", "parent-id:analyze-1"]
