"""pr_links 单测（REQ-issue-link-pr-quality-base-1777218242）。

覆盖 LP-S1..S7：
- LP-S1 cache hit returns cached without GH call
- LP-S2 cache miss runs discovery and stashes ctx
- LP-S3 runner exec error returns empty without raising
- LP-S4 one repo errors, another succeeds (per-repo best-effort)
- LP-S5 first discovery backfills analyze issue
- LP-S6 pr_link_tags formats correctly
- LP-S7 from_ctx tolerates malformed entries
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from orchestrator import pr_links

# ─── 测试 fixture / helper ─────────────────────────────────────────────────


@dataclass
class FakeExec:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


def _patch_runner(monkeypatch, *, exec_return: FakeExec | Exception | None = None):
    """patch pr_links.k8s_runner.get_controller()。

    exec_return:
      - FakeExec: exec_in_runner 返回该 stdout
      - Exception 实例: exec_in_runner 抛该异常
      - None: get_controller 抛 RuntimeError（无 controller）
    """
    if exec_return is None:
        def raise_no_controller():
            raise RuntimeError("RunnerController 未初始化")
        monkeypatch.setattr(
            pr_links.k8s_runner, "get_controller", raise_no_controller,
        )
        return None

    if isinstance(exec_return, Exception):
        exec_fn = AsyncMock(side_effect=exec_return)
    else:
        exec_fn = AsyncMock(return_value=exec_return)

    class FakeRC:
        exec_in_runner = exec_fn

    monkeypatch.setattr(pr_links.k8s_runner, "get_controller", lambda: FakeRC())
    return exec_fn


def _patch_db(monkeypatch, *, update_ctx: AsyncMock | None = None):
    """patch pr_links.db.get_pool + req_state.update_context。"""
    if update_ctx is None:
        update_ctx = AsyncMock()
    monkeypatch.setattr(pr_links.db, "get_pool", lambda: object())
    monkeypatch.setattr(pr_links.req_state, "update_context", update_ctx)
    return update_ctx


def _patch_bkd(monkeypatch, *, merge_tags_fn: AsyncMock | None = None):
    """patch BKDClient (lazy imported in pr_links._backfill_known_issues)。"""
    if merge_tags_fn is None:
        merge_tags_fn = AsyncMock()
    bkd_instance = MagicMock()
    bkd_instance.merge_tags_and_update = merge_tags_fn

    @asynccontextmanager
    async def fake_client(*a, **kw):
        yield bkd_instance

    # bkd_links 内部 import: from .bkd import BKDClient
    import orchestrator.bkd as bkd_mod
    monkeypatch.setattr(bkd_mod, "BKDClient", fake_client)
    return merge_tags_fn


# ─── LP-S1: cache hit returns cached without GH call ───────────────────────


@pytest.mark.asyncio
async def test_LP_S1_cache_hit_returns_cached_without_gh(monkeypatch):
    """ctx.pr_links 已缓存 → 直接返回，**不**触发 GH HTTP / runner exec / DB write。"""
    # 任何一个 get_controller 调用都失败：cache hit 路径不该走到这
    def _boom():
        raise AssertionError("cache hit MUST NOT call get_controller")
    monkeypatch.setattr(pr_links.k8s_runner, "get_controller", _boom)

    update_ctx = _patch_db(monkeypatch)

    cached_ctx = {
        "pr_links": [
            {"repo": "phona/sisyphus", "number": 42, "url": "https://github.com/phona/sisyphus/pull/42"},
        ],
    }

    result = await pr_links.ensure_pr_links_in_ctx(
        req_id="REQ-X", branch="feat/REQ-X",
        ctx=cached_ctx, project_id="p",
    )

    assert result == [
        pr_links.PrLink(
            repo="phona/sisyphus", number=42,
            url="https://github.com/phona/sisyphus/pull/42",
        ),
    ]
    update_ctx.assert_not_awaited()


# ─── LP-S2: cache miss runs discovery and stashes ctx ──────────────────────


@pytest.mark.asyncio
async def test_LP_S2_cache_miss_discovers_and_stashes(monkeypatch, httpx_mock):
    """ctx 空 → runner discovery + GH REST → 返回 + 持久化 ctx.pr_links。"""
    monkeypatch.setattr(pr_links.settings, "github_token", "ghp_xxx")
    _patch_runner(monkeypatch, exec_return=FakeExec(
        stdout="git@github.com:phona/sisyphus.git\n",
    ))
    update_ctx = _patch_db(monkeypatch)
    _patch_bkd(monkeypatch)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/sisyphus/pulls?head=phona:feat/REQ-X&state=open",
        json=[{"number": 42, "html_url": "https://github.com/phona/sisyphus/pull/42"}],
    )

    result = await pr_links.ensure_pr_links_in_ctx(
        req_id="REQ-X", branch="feat/REQ-X",
        ctx={}, project_id="p",
    )

    assert result == [
        pr_links.PrLink(
            repo="phona/sisyphus", number=42,
            url="https://github.com/phona/sisyphus/pull/42",
        ),
    ]
    update_ctx.assert_awaited_once()
    _, args, _kwargs = update_ctx.mock_calls[0]
    # update_context(pool, req_id, patch)
    patch = args[2]
    assert patch == {
        "pr_links": [
            {"repo": "phona/sisyphus", "number": 42,
             "url": "https://github.com/phona/sisyphus/pull/42"},
        ],
    }


# ─── LP-S3: runner exec error returns empty without raising ────────────────


@pytest.mark.asyncio
async def test_LP_S3_runner_exec_error_returns_empty(monkeypatch):
    """get_controller RuntimeError → 返 []，不抛、不更新 ctx、不查 GH。"""
    _patch_runner(monkeypatch, exec_return=None)  # get_controller raises
    update_ctx = _patch_db(monkeypatch)

    result = await pr_links.ensure_pr_links_in_ctx(
        req_id="REQ-X", branch="feat/REQ-X",
        ctx={}, project_id="p",
    )

    assert result == []
    update_ctx.assert_not_awaited()


@pytest.mark.asyncio
async def test_LP_S3b_runner_exec_in_runner_raises_returns_empty(monkeypatch):
    """exec_in_runner 抛异常 → 返 []，不抛。"""
    _patch_runner(
        monkeypatch,
        exec_return=RuntimeError("pod not found"),
    )
    update_ctx = _patch_db(monkeypatch)

    result = await pr_links.ensure_pr_links_in_ctx(
        req_id="REQ-X", branch="feat/REQ-X",
        ctx={}, project_id="p",
    )

    assert result == []
    update_ctx.assert_not_awaited()


# ─── LP-S4: one repo errors, another succeeds ──────────────────────────────


@pytest.mark.asyncio
async def test_LP_S4_per_repo_best_effort_on_gh_error(monkeypatch, httpx_mock):
    """multi-repo: repo-a GH 503，repo-b 正常 → 只返 repo-b 的 link。"""
    monkeypatch.setattr(pr_links.settings, "github_token", "ghp_xxx")
    _patch_runner(monkeypatch, exec_return=FakeExec(
        stdout=(
            "git@github.com:phona/repo-a.git\n"
            "https://github.com/phona/repo-b.git\n"
        ),
    ))
    _patch_db(monkeypatch)
    _patch_bkd(monkeypatch)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/repo-a/pulls?head=phona:feat/REQ-X&state=open",
        status_code=503,
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/repo-b/pulls?head=phona:feat/REQ-X&state=open",
        json=[{"number": 7, "html_url": "https://github.com/phona/repo-b/pull/7"}],
    )

    result = await pr_links.discover_pr_links(
        req_id="REQ-X", branch="feat/REQ-X",
    )

    assert result == [
        pr_links.PrLink(
            repo="phona/repo-b", number=7,
            url="https://github.com/phona/repo-b/pull/7",
        ),
    ]


# ─── LP-S5: first discovery backfills analyze issue ────────────────────────


@pytest.mark.asyncio
async def test_LP_S5_first_discovery_backfills_analyze_issue(monkeypatch, httpx_mock):
    """ctx.analyze_issue_id 存在 → 第一次 discover 时调 bkd.merge_tags_and_update。"""
    monkeypatch.setattr(pr_links.settings, "github_token", "ghp_xxx")
    _patch_runner(monkeypatch, exec_return=FakeExec(
        stdout="git@github.com:phona/sisyphus.git\n",
    ))
    _patch_db(monkeypatch)
    merge_tags = _patch_bkd(monkeypatch)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/sisyphus/pulls?head=phona:feat/REQ-X&state=open",
        json=[{"number": 42, "html_url": "https://github.com/phona/sisyphus/pull/42"}],
    )

    result = await pr_links.ensure_pr_links_in_ctx(
        req_id="REQ-X", branch="feat/REQ-X",
        ctx={"analyze_issue_id": "abc123"},
        project_id="p",
    )

    assert len(result) == 1
    merge_tags.assert_awaited_once_with(
        "p", "abc123", add=["pr:phona/sisyphus#42"],
    )


@pytest.mark.asyncio
async def test_LP_S5b_backfill_iterates_all_known_issue_ids(monkeypatch, httpx_mock):
    """ctx 多个 *_issue_id key → 全部回填，每条独立 PATCH。"""
    monkeypatch.setattr(pr_links.settings, "github_token", "ghp_xxx")
    _patch_runner(monkeypatch, exec_return=FakeExec(
        stdout="git@github.com:phona/sisyphus.git\n",
    ))
    _patch_db(monkeypatch)
    merge_tags = _patch_bkd(monkeypatch)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/sisyphus/pulls?head=phona:feat/REQ-X&state=open",
        json=[{"number": 42, "html_url": "https://github.com/phona/sisyphus/pull/42"}],
    )

    ctx = {
        "analyze_issue_id": "a-1",
        "staging_test_issue_id": "s-1",
        "accept_issue_id": "ac-1",
        # archive_issue_id 缺：不该 panic
        "unknown_key": "x-1",  # 不在白名单：不该被 backfill
    }
    await pr_links.ensure_pr_links_in_ctx(
        req_id="REQ-X", branch="feat/REQ-X",
        ctx=ctx, project_id="p",
    )

    assert merge_tags.await_count == 3
    called_ids = {call.args[1] for call in merge_tags.await_args_list}
    assert called_ids == {"a-1", "s-1", "ac-1"}


@pytest.mark.asyncio
async def test_LP_S5c_backfill_failure_does_not_raise(monkeypatch, httpx_mock):
    """单条 backfill PATCH 抛异常 → 不冒泡到 caller，其他 id 继续 PATCH。"""
    monkeypatch.setattr(pr_links.settings, "github_token", "ghp_xxx")
    _patch_runner(monkeypatch, exec_return=FakeExec(
        stdout="git@github.com:phona/sisyphus.git\n",
    ))
    _patch_db(monkeypatch)

    call_log: list[str] = []

    async def flaky_merge(project, issue_id, *, add):
        call_log.append(issue_id)
        if issue_id == "a-1":
            raise httpx.HTTPError("boom")

    _patch_bkd(monkeypatch, merge_tags_fn=AsyncMock(side_effect=flaky_merge))

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/sisyphus/pulls?head=phona:feat/REQ-X&state=open",
        json=[{"number": 42, "html_url": "https://github.com/phona/sisyphus/pull/42"}],
    )

    ctx = {"analyze_issue_id": "a-1", "staging_test_issue_id": "s-1"}
    result = await pr_links.ensure_pr_links_in_ctx(
        req_id="REQ-X", branch="feat/REQ-X",
        ctx=ctx, project_id="p",
    )

    # caller 拿到 link list（discover 成功部分仍有效）
    assert len(result) == 1
    # 失败的 a-1 之后还有 s-1 被尝试
    assert call_log == ["a-1", "s-1"]


# ─── LP-S6: pr_link_tags formats correctly ─────────────────────────────────


def test_LP_S6_pr_link_tags_formats_correctly():
    links = [
        pr_links.PrLink(repo="phona/sisyphus", number=42, url="u1"),
        pr_links.PrLink(repo="phona/runner", number=7, url="u2"),
    ]
    assert pr_links.pr_link_tags(links) == [
        "pr:phona/sisyphus#42",
        "pr:phona/runner#7",
    ]


def test_LP_S6b_pr_link_tags_empty_returns_empty():
    assert pr_links.pr_link_tags([]) == []


def test_LP_S6c_PrLink_tag_method():
    link = pr_links.PrLink(repo="phona/sisyphus", number=42, url="...")
    assert link.tag() == "pr:phona/sisyphus#42"
    assert link.to_dict() == {
        "repo": "phona/sisyphus", "number": 42, "url": "...",
    }


# ─── LP-S7: from_ctx tolerates malformed entries ───────────────────────────


def test_LP_S7_from_ctx_skips_malformed_entries():
    ctx = {
        "pr_links": [
            {"repo": "phona/sisyphus", "number": 42, "url": "u1"},
            {"repo": "missing-num"},                   # missing number
            "not-a-dict",                              # not a dict
            {"repo": "phona/runner", "number": "7", "url": "u2"},  # str number coerces
            {"repo": "phona/bad", "number": "abc"},    # invalid number
            None,                                      # None entry
        ],
    }
    result = pr_links.from_ctx(ctx)
    assert result == [
        pr_links.PrLink(repo="phona/sisyphus", number=42, url="u1"),
        pr_links.PrLink(repo="phona/runner", number=7, url="u2"),
    ]


def test_LP_S7b_from_ctx_handles_missing_or_wrong_type():
    assert pr_links.from_ctx(None) == []
    assert pr_links.from_ctx({}) == []
    assert pr_links.from_ctx({"pr_links": None}) == []
    assert pr_links.from_ctx({"pr_links": "not-a-list"}) == []
    assert pr_links.from_ctx({"pr_links": []}) == []


# ─── 额外覆盖 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_repos_via_runner_dedupes(monkeypatch):
    """同 origin 出现两次（fs glob 重复）→ 去重。"""
    _patch_runner(monkeypatch, exec_return=FakeExec(
        stdout=(
            "git@github.com:phona/sisyphus.git\n"
            "git@github.com:phona/sisyphus.git\n"
            "https://github.com/phona/runner.git\n"
        ),
    ))
    repos = await pr_links._discover_repos_via_runner("REQ-X")
    assert repos == ["phona/sisyphus", "phona/runner"]


@pytest.mark.asyncio
async def test_get_open_pr_returns_none_when_no_open_pr(monkeypatch, httpx_mock):
    """GH 返回空 list → None（repo 没 open PR，跳过）。"""
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/repo-x/pulls?head=phona:feat/REQ-X&state=open",
        json=[],
    )
    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        result = await pr_links._get_open_pr(client, "phona/repo-x", "feat/REQ-X")
    assert result is None


@pytest.mark.asyncio
async def test_get_open_pr_skips_invalid_repo_slug(monkeypatch):
    """repo 字符串里没 '/' → None，不查 GH。"""
    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        result = await pr_links._get_open_pr(client, "no-slash-repo", "feat/REQ-X")
    assert result is None


@pytest.mark.asyncio
async def test_ensure_pr_links_no_repos_discovered_returns_empty(monkeypatch):
    """runner discovery 返空 → 返 []，不查 GH，不写 ctx。"""
    _patch_runner(monkeypatch, exec_return=FakeExec(stdout=""))
    update_ctx = _patch_db(monkeypatch)

    result = await pr_links.ensure_pr_links_in_ctx(
        req_id="REQ-X", branch="feat/REQ-X",
        ctx={}, project_id="p",
    )

    assert result == []
    update_ctx.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_pr_links_no_open_pr_returns_empty_no_ctx_write(
    monkeypatch, httpx_mock,
):
    """repo 都查到了但都没 open PR → 返 []，不写 ctx（让下次 callsite 再试）。"""
    monkeypatch.setattr(pr_links.settings, "github_token", "ghp_xxx")
    _patch_runner(monkeypatch, exec_return=FakeExec(
        stdout="git@github.com:phona/sisyphus.git\n",
    ))
    update_ctx = _patch_db(monkeypatch)
    _patch_bkd(monkeypatch)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/sisyphus/pulls?head=phona:feat/REQ-X&state=open",
        json=[],
    )

    result = await pr_links.ensure_pr_links_in_ctx(
        req_id="REQ-X", branch="feat/REQ-X",
        ctx={}, project_id="p",
    )

    assert result == []
    update_ctx.assert_not_awaited()
