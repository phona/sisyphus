"""Unit tests for orchestrator.links (REQ-pr-issue-traceability-1777218612).

Covers spec.md scenarios XLINK-S1 .. XLINK-S6 plus a couple of
discover_pr_urls behaviours not in spec but worth pinning.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from orchestrator import links

# ─── bkd_issue_url ──────────────────────────────────────────────────────────


def _patch_settings(monkeypatch, *, bkd_base_url: str, bkd_frontend_url: str = "") -> None:
    monkeypatch.setattr(links.settings, "bkd_base_url", bkd_base_url, raising=False)
    monkeypatch.setattr(links.settings, "bkd_frontend_url", bkd_frontend_url, raising=False)


def test_xlink_s1_base_url_with_api_suffix(monkeypatch):
    _patch_settings(monkeypatch, bkd_base_url="https://bkd.example/api")
    assert links.bkd_issue_url("p", "i") == "https://bkd.example/projects/p/issues/i"


def test_xlink_s2_explicit_frontend_override_beats_base(monkeypatch):
    _patch_settings(
        monkeypatch,
        bkd_base_url="https://api.bkd.example/api",
        bkd_frontend_url="https://bkd.example/",
    )
    assert links.bkd_issue_url("p", "i") == "https://bkd.example/projects/p/issues/i"


@pytest.mark.parametrize("project_id, issue_id", [
    ("", "x"),
    ("p", ""),
    (None, "x"),
    ("p", None),
])
def test_xlink_s3_missing_identifiers_return_none(monkeypatch, project_id, issue_id):
    _patch_settings(monkeypatch, bkd_base_url="https://bkd.example/api")
    assert links.bkd_issue_url(project_id, issue_id) is None


def test_xlink_s4_unparseable_base_returns_none(monkeypatch):
    _patch_settings(monkeypatch, bkd_base_url="not-a-url")
    assert links.bkd_issue_url("p", "i") is None


def test_xlink_s4b_empty_base_and_override_returns_none(monkeypatch):
    _patch_settings(monkeypatch, bkd_base_url="", bkd_frontend_url="")
    assert links.bkd_issue_url("p", "i") is None


def test_base_url_without_api_suffix_kept_intact(monkeypatch):
    """Deployments may set bkd_base_url to the bare frontend URL — keep it."""
    _patch_settings(monkeypatch, bkd_base_url="https://bkd.example/")
    assert links.bkd_issue_url("p", "i") == "https://bkd.example/projects/p/issues/i"


# ─── format_pr_links_md ────────────────────────────────────────────────────


def test_xlink_s5_multi_repo_dict_sorted_bullets():
    pr_urls = {
        "foo/bar": "https://github.com/foo/bar/pull/9",
        "baz/qux": "https://github.com/baz/qux/pull/3",
    }
    assert links.format_pr_links_md(pr_urls) == [
        "- [baz/qux#3](https://github.com/baz/qux/pull/3)",
        "- [foo/bar#9](https://github.com/foo/bar/pull/9)",
    ]


@pytest.mark.parametrize("bad", [None, {}, "not-a-dict", 0, []])
def test_xlink_s6_non_dict_or_empty_returns_empty_list(bad):
    assert links.format_pr_links_md(bad) == []


def test_format_pr_links_md_falls_back_when_no_pull_segment():
    pr_urls = {"foo/bar": "https://github.com/foo/bar/issues/9"}  # not /pull/
    assert links.format_pr_links_md(pr_urls) == [
        "- [foo/bar](https://github.com/foo/bar/issues/9)",
    ]


def test_format_pr_links_md_skips_non_string_values():
    pr_urls = {"foo/bar": None, "baz/qux": "https://github.com/baz/qux/pull/1"}
    assert links.format_pr_links_md(pr_urls) == [
        "- [baz/qux#1](https://github.com/baz/qux/pull/1)",
    ]


def test_format_pr_links_inline_joins_with_comma():
    pr_urls = {
        "foo/bar": "https://github.com/foo/bar/pull/9",
        "baz/qux": "https://github.com/baz/qux/pull/3",
    }
    inline = links.format_pr_links_inline(pr_urls)
    assert inline == (
        "[baz/qux#3](https://github.com/baz/qux/pull/3), "
        "[foo/bar#9](https://github.com/foo/bar/pull/9)"
    )


def test_format_pr_links_inline_empty_returns_empty_string():
    assert links.format_pr_links_inline({}) == ""
    assert links.format_pr_links_inline(None) == ""


# ─── discover_pr_urls ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_pr_urls_empty_repos_skips_http():
    """No repos → empty dict, no HTTP."""
    with patch.object(httpx, "AsyncClient") as ac:
        out = await links.discover_pr_urls([], "feat/REQ-x")
    assert out == {}
    ac.assert_not_called()


@pytest.mark.asyncio
async def test_discover_pr_urls_returns_html_url_for_open_pr(httpx_mock, monkeypatch):
    monkeypatch.setattr(links.settings, "github_token", "ghp_x", raising=False)
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/foo/bar/pulls?head=foo%3Afeat%2FREQ-x&state=open&per_page=5",
        json=[{"html_url": "https://github.com/foo/bar/pull/9"}],
    )
    out = await links.discover_pr_urls(["foo/bar"], "feat/REQ-x")
    assert out == {"foo/bar": "https://github.com/foo/bar/pull/9"}


@pytest.mark.asyncio
async def test_discover_pr_urls_falls_back_to_all_state_when_no_open(httpx_mock, monkeypatch):
    monkeypatch.setattr(links.settings, "github_token", "ghp_x", raising=False)
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/foo/bar/pulls?head=foo%3Afeat%2FREQ-x&state=open&per_page=5",
        json=[],
    )
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/foo/bar/pulls?head=foo%3Afeat%2FREQ-x&state=all&per_page=5",
        json=[{"html_url": "https://github.com/foo/bar/pull/42"}],
    )
    out = await links.discover_pr_urls(["foo/bar"], "feat/REQ-x")
    assert out == {"foo/bar": "https://github.com/foo/bar/pull/42"}


@pytest.mark.asyncio
async def test_discover_pr_urls_swallows_http_error(httpx_mock, monkeypatch):
    monkeypatch.setattr(links.settings, "github_token", "ghp_x", raising=False)
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/foo/bar/pulls?head=foo%3Afeat%2FREQ-x&state=open&per_page=5",
        status_code=500,
    )
    out = await links.discover_pr_urls(["foo/bar"], "feat/REQ-x")
    assert out == {}


@pytest.mark.asyncio
async def test_discover_pr_urls_skips_invalid_repo_slugs(httpx_mock, monkeypatch):
    """Repo strings without `/` are dropped before any HTTP call."""
    monkeypatch.setattr(links.settings, "github_token", "ghp_x", raising=False)
    httpx_mock.add_response(
        method="GET",
        url="https://api.github.com/repos/foo/bar/pulls?head=foo%3Afeat%2FREQ-x&state=open&per_page=5",
        json=[{"html_url": "https://github.com/foo/bar/pull/1"}],
    )
    out = await links.discover_pr_urls(["bare-repo", "foo/bar", ""], "feat/REQ-x")
    assert out == {"foo/bar": "https://github.com/foo/bar/pull/1"}
