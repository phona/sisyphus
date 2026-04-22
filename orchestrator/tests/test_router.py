"""router.derive_event 表驱动测试。"""
from __future__ import annotations

import pytest

from orchestrator.router import derive_event, extract_req_id, get_parent_id, get_round
from orchestrator.state import Event

CASES: list[tuple[str, list[str], Event | None]] = [
    # intent
    ("issue.updated",     ["intent:analyze"],                                Event.INTENT_ANALYZE),
    # intent 已被 analyze 接管 → 不再发
    ("issue.updated",     ["intent:analyze", "analyze", "REQ-1"],            None),
    # 普通 issue.updated 一律忽略（避免自指）
    ("issue.updated",     ["dev", "REQ-1"],                                  None),

    # session.failed
    ("session.failed",    ["dev", "REQ-1"],                                  Event.SESSION_FAILED),

    # session.completed dispatch
    ("session.completed", ["analyze", "REQ-1"],                              Event.ANALYZE_DONE),
    ("session.completed", ["contract-spec", "REQ-1"],                        Event.SPEC_DONE),
    ("session.completed", ["acceptance-spec", "REQ-1"],                          Event.SPEC_DONE),
    ("session.completed", ["dev", "REQ-1"],                                  Event.DEV_DONE),
    # v0.2：staging-test + pr-ci 新 agent role
    ("session.completed", ["staging-test", "REQ-1", "result:pass"],          Event.STAGING_TEST_PASS),
    ("session.completed", ["staging-test", "REQ-1", "result:fail"],          Event.STAGING_TEST_FAIL),
    ("session.completed", ["pr-ci", "REQ-1", "pr-ci:pass"],                  Event.PR_CI_PASS),
    ("session.completed", ["pr-ci", "REQ-1", "pr-ci:fail"],                  Event.PR_CI_FAIL),
    ("session.completed", ["pr-ci", "REQ-1", "pr-ci:timeout"],               Event.PR_CI_TIMEOUT),
    ("session.completed", ["accept", "REQ-1", "result:pass"],                Event.ACCEPT_PASS),
    ("session.completed", ["accept", "REQ-1", "result:fail"],                Event.ACCEPT_FAIL),
    ("session.completed", ["bugfix", "REQ-1", "round-1"],                    Event.BUGFIX_DONE),
    ("session.completed", ["bugfix", "REQ-1", "diagnosis:spec-bug"],         Event.BUGFIX_SPEC_BUG),
    ("session.completed", ["bugfix", "REQ-1", "diagnosis:env-bug"],          Event.BUGFIX_ENV_BUG),
    ("session.completed", ["test-fix", "REQ-1", "round-1"],                  Event.TEST_FIX_DONE),
    ("session.completed", ["reviewer", "REQ-1", "result:pass"],              Event.REVIEWER_PASS),
    ("session.completed", ["reviewer", "REQ-1", "result:fail"],              Event.REVIEWER_FAIL),
    ("session.completed", ["done-archive", "REQ-1"],                         Event.ARCHIVE_DONE),

    # 没结果 tag → None（agent 没正常完成）
    ("session.completed", ["staging-test", "REQ-1"],                         None),
    ("session.completed", ["pr-ci", "REQ-1"],                                None),
    ("session.completed", ["accept", "REQ-1"],                               None),
    ("session.completed", ["reviewer", "REQ-1"],                             None),

    # 未知 event_type
    ("session.unknown",   ["dev", "REQ-1"],                                  None),
]


@pytest.mark.parametrize("event_type,tags,expected", CASES)
def test_derive(event_type, tags, expected):
    assert derive_event(event_type, tags) == expected


def test_extract_req_id_from_tags():
    assert extract_req_id(["dev", "REQ-722"]) == "REQ-722"
    assert extract_req_id(["dev"]) is None
    assert extract_req_id(["dev"], issue_number=42) == "REQ-42"


def test_get_round_and_parent_id():
    assert get_round(["round-3", "x"]) == 3
    assert get_round(["x"]) == 0
    assert get_parent_id(["parent-id:abc-123"]) == "abc-123"
    assert get_parent_id(["x"]) is None
