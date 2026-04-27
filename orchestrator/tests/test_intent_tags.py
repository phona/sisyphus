"""Unit tests for orchestrator.intent_tags (REQ-ux-tags-injection-1777257283).

Pure-function module — no fixtures needed.
"""
from __future__ import annotations

import pytest

from orchestrator.intent_tags import (
    SISYPHUS_MANAGED_EXACT,
    SISYPHUS_MANAGED_PREFIXES,
    filter_propagatable_intent_tags,
    is_sisyphus_managed_tag,
)

# ─── is_sisyphus_managed_tag ───────────────────────────────────────────────


@pytest.mark.parametrize("tag", sorted(SISYPHUS_MANAGED_EXACT))
def test_is_managed_exact_matches(tag):
    """每个 exact tag 都被识别为 sisyphus-managed。"""
    assert is_sisyphus_managed_tag(tag) is True


@pytest.mark.parametrize("prefix", SISYPHUS_MANAGED_PREFIXES)
def test_is_managed_prefix_matches(prefix):
    """每个前缀 + 任意后缀都被识别为 managed。"""
    assert is_sisyphus_managed_tag(f"{prefix}some-value") is True


@pytest.mark.parametrize("tag", [
    "REQ-foo",
    "REQ-foo-bar-1234567",
    "REQ-ux-tags-injection-1777257283",
])
def test_is_managed_req_id_pattern(tag):
    """REQ-* identifier 被识别为 managed。"""
    assert is_sisyphus_managed_tag(tag) is True


@pytest.mark.parametrize("tag", [
    "repo:phona/foo",
    "spec_home_repo:phona/sisyphus",
    "ux:fast-track",
    "priority:high",
    "team:platform",
    "owner:weifashi",
    "label-without-prefix",
])
def test_is_managed_user_hint_passes_through(tag):
    """未列入 sisyphus-managed 的全是 user hint，返回 False。"""
    assert is_sisyphus_managed_tag(tag) is False


@pytest.mark.parametrize("tag", [
    None, 42, "", "   ", "\t", b"bytes-not-str",
])
def test_is_managed_treats_invalid_inputs_as_managed(tag):
    """非字符串 / 空串 / 空白 → 视作 managed（非 hint），方便 filter 一次性屏蔽。"""
    assert is_sisyphus_managed_tag(tag) is True


def test_is_managed_does_not_match_almost_req_id():
    """`REQ-` 前缀但不符合 `^REQ-[\\w-]+$` 的字符串不算 REQ-id（也不在前缀列表里）→ False。"""
    # 含空格 / 含 / 等非 word/hyphen 字符 → regex miss，且不在 prefix 列表 → 当 hint
    assert is_sisyphus_managed_tag("REQ X") is False
    assert is_sisyphus_managed_tag("REQ-") is False  # 空 slug，regex 要求至少 1 个 \w


def test_is_managed_does_not_match_substring_in_middle():
    """前缀必须从字符串开头匹配，子串不算。"""
    assert is_sisyphus_managed_tag("foo-result:pass") is False
    assert is_sisyphus_managed_tag("not-sisyphus") is False


# ─── filter_propagatable_intent_tags ───────────────────────────────────────


def test_filter_strips_all_managed_exact():
    """UTI-S1: 输入全是 sisyphus-managed exact tag → 空列表。"""
    tags = list(SISYPHUS_MANAGED_EXACT)
    assert filter_propagatable_intent_tags(tags) == []


def test_filter_strips_all_managed_prefixes():
    """UTI-S2: 各前缀 + 后缀 → 空列表。"""
    tags = [
        "intent:analyze", "result:pass", "pr-ci:pass",
        "verify:dev_cross_check", "trigger:fail",
        "decision:eyJhY3Rpb24iOiJwYXNzIn0=",
        "fixer:dev", "parent:analyze", "parent-id:abc123",
        "parent-stage:spec_lint", "target:phona/foo",
        "round-3", "pr:phona/foo#42",
    ]
    assert filter_propagatable_intent_tags(tags) == []


def test_filter_strips_req_id_pattern():
    """UTI-S3: REQ-* tag 全过滤。"""
    tags = [
        "REQ-ux-tags-injection-1777257283",
        "REQ-foo",
        "REQ-bar-baz-1234567",
    ]
    assert filter_propagatable_intent_tags(tags) == []


def test_filter_keeps_user_hints_in_order():
    """UTI-S4: 全是 user hint → 原序保留 + 全部传出。"""
    tags = [
        "repo:phona/sisyphus",
        "ux:fast-track",
        "priority:high",
        "team:platform",
        "spec_home_repo:phona/sisyphus",
    ]
    assert filter_propagatable_intent_tags(tags) == tags


def test_filter_de_duplicates_survivors():
    """UTI-S5: 重复 hint 去重，保留第一次出现位置。"""
    tags = ["repo:foo/bar", "repo:foo/bar", "ux:fast-track", "ux:fast-track"]
    assert filter_propagatable_intent_tags(tags) == ["repo:foo/bar", "ux:fast-track"]


def test_filter_mixed_managed_and_hint():
    """UTI-S6: 混合输入 → 只剩 hint。"""
    tags = [
        "intent:analyze", "REQ-foo-1234", "analyze",
        "repo:phona/foo", "ux:fast-track",
        "result:pass", "pr:phona/foo#1",
    ]
    assert filter_propagatable_intent_tags(tags) == [
        "repo:phona/foo", "ux:fast-track",
    ]


def test_filter_handles_none():
    """UTI-S7a: None 输入 → 空列表。"""
    assert filter_propagatable_intent_tags(None) == []


def test_filter_handles_empty_list():
    """UTI-S7b: 空列表 → 空列表。"""
    assert filter_propagatable_intent_tags([]) == []


def test_filter_skips_invalid_entries():
    """UTI-S7c: 非字符串 / 空串 / 空白 跳过，不抛异常。"""
    tags = [None, 42, "", "   ", "\t", "ux:ok"]
    assert filter_propagatable_intent_tags(tags) == ["ux:ok"]


def test_filter_is_idempotent():
    """UTI-S8: filter(filter(x)) == filter(x)。"""
    tags = [
        "intent:analyze", "REQ-foo", "analyze", "repo:foo/bar",
        "ux:fast-track", "repo:foo/bar",
    ]
    once = filter_propagatable_intent_tags(tags)
    twice = filter_propagatable_intent_tags(once)
    assert once == twice
    assert once == ["repo:foo/bar", "ux:fast-track"]


def test_filter_strips_leading_trailing_whitespace_then_filters():
    """前后 whitespace 不应让 hint tag 漏检 / 保留 stripped 形式。"""
    # 注意：is_sisyphus_managed_tag 内部 strip 后比对，但 filter 自己用 strip 后值入 out
    tags = ["  ux:fast-track  ", "  intent:analyze  "]
    assert filter_propagatable_intent_tags(tags) == ["ux:fast-track"]
