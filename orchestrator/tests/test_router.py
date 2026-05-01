"""router.derive_event 表驱动测试。"""
from __future__ import annotations

import pytest

from orchestrator.router import (
    derive_event,
    extract_base_branches,
    extract_req_id,
    get_parent_id,
    get_round,
    resolve_base_branch,
)
from orchestrator.state import Event

CASES: list[tuple[str, list[str], Event | None]] = [
    # intent:intake → INTAKING（用户打 intent:intake tag，还没 intake 过）
    ("issue.updated",     ["intent:intake"],                                 Event.INTENT_INTAKE),
    # intent:intake 已被 intake 接管 → 不再发
    ("issue.updated",     ["intent:intake", "intake", "REQ-1"],              None),
    # intent
    ("issue.updated",     ["intent:analyze"],                                Event.INTENT_ANALYZE),
    # intent 已被 analyze 接管 → 不再发
    ("issue.updated",     ["intent:analyze", "analyze", "REQ-1"],            None),
    # M12：resume:analyze 路径已删（砍 M6 admission），不再映射任何 event
    ("issue.updated",     ["resume:analyze", "analyze", "REQ-1"],            None),
    # 普通 issue.updated 一律忽略（避免自指）
    ("issue.updated",     ["dev", "REQ-1"],                                  None),

    # session.failed
    ("session.failed",    ["dev", "REQ-1"],                                  Event.SESSION_FAILED),

    # intake agent session.completed
    ("session.completed", ["intake", "REQ-1", "result:pass"],                Event.INTAKE_PASS),
    ("session.completed", ["intake", "REQ-1", "result:fail"],                Event.INTAKE_FAIL),
    # 中间轮（仅 intake tag，无 result）→ None（不推进状态机）
    ("session.completed", ["intake", "REQ-1"],                               None),

    # session.completed dispatch
    ("session.completed", ["analyze", "REQ-1"],                              Event.ANALYZE_DONE),
    # M15：spec / dev agent 的 session.completed 不再 router 映射 event
    # sisyphus 内部 emit SPEC_LINT_RUNNING / DEV_CROSS_CHECK_RUNNING
    ("session.completed", ["spec", "REQ-1"],                                 None),
    ("session.completed", ["dev", "REQ-1"],                                  None),
    # v0.2：staging-test + pr-ci 新 agent role
    ("session.completed", ["staging-test", "REQ-1", "result:pass"],          Event.STAGING_TEST_PASS),
    ("session.completed", ["staging-test", "REQ-1", "result:fail"],          Event.STAGING_TEST_FAIL),
    ("session.completed", ["pr-ci", "REQ-1", "pr-ci:pass"],                  Event.PR_CI_PASS),
    ("session.completed", ["pr-ci", "REQ-1", "pr-ci:fail"],                  Event.PR_CI_FAIL),
    ("session.completed", ["pr-ci", "REQ-1", "pr-ci:timeout"],               Event.PR_CI_TIMEOUT),
    ("session.completed", ["accept", "REQ-1", "result:pass"],                Event.ACCEPT_PASS),
    ("session.completed", ["accept", "REQ-1", "result:fail"],                Event.ACCEPT_FAIL),
    # M14c：bugfix / diagnose tag 已删除映射（router 不再认这两个 agent role）
    ("session.completed", ["bugfix", "REQ-1", "round-1"],                    None),
    ("session.completed", ["diagnose", "REQ-1", "diagnosis:code-bug"],       None),

    # M14b verifier-agent：由 router 主动返 None，交 webhook.derive_verifier_event 解 JSON
    ("session.completed", ["verifier", "REQ-1", "verify:dev", "trigger:success"], None),
    # M14b fixer-agent：完成后 FIXER_DONE
    ("session.completed", ["fixer", "REQ-1", "fixer:dev"],                    Event.FIXER_DONE),

    # 没结果 tag → None（agent 没正常完成）
    ("session.completed", ["staging-test", "REQ-1"],                         None),
    ("session.completed", ["pr-ci", "REQ-1"],                                None),
    ("session.completed", ["accept", "REQ-1"],                               None),

    # REQ-router-session-completed-audit: session.completed without result coverage
    # challenger without result → None (intermediate round, not SESSION_FAILED)
    ("session.completed", ["challenger", "REQ-1"],                           None),
    # fixer without extra tags → FIXER_DONE (fixer never uses result:* tags)
    ("session.completed", ["fixer", "REQ-1"],                                Event.FIXER_DONE),
    # no stage tag at all → None (orphan / unclassified session, skip silently)
    ("session.completed", ["REQ-1"],                                         None),
    # known stage tag + unrecognized result variant → None (not SESSION_FAILED)
    ("session.completed", ["challenger", "REQ-1", "result:weird"],           None),
    ("session.completed", ["staging-test", "REQ-1", "result:weird"],         None),

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


# ─── REQ-base-branch-override-1777480690 ──────────────────────────────────

EXTRACT_BASE_CASES = [
    # 无 base tag
    ([], None, {}),
    (["intent:analyze"], None, {}),
    # 单仓默认 base
    (["base:develop"], "develop", {}),
    (["base:feat/develop-hwt"], "feat/develop-hwt", {}),
    # per-repo override
    (["base:ttpos-flutter:feat/develop-hwt"], None, {"ttpos-flutter": "feat/develop-hwt"}),
    (["base:my-repo:release"], None, {"my-repo": "release"}),
    # 默认 + per-repo 同时存在
    (["base:develop", "base:ttpos-flutter:feat/develop-hwt"],
     "develop", {"ttpos-flutter": "feat/develop-hwt"}),
    # 多个 per-repo
    (["base:ttpos-flutter:feat/develop-hwt", "base:ttpos-server-go:release"],
     None, {"ttpos-flutter": "feat/develop-hwt", "ttpos-server-go": "release"}),
    # 空 base tag（忽略）
    (["base:"], None, {}),
    # 非 base tag 不干扰
    (["base:develop", "intent:analyze", "repo:phona/sisyphus"],
     "develop", {}),
]


@pytest.mark.parametrize("tags,expected_default,expected_overrides", EXTRACT_BASE_CASES)
def test_extract_base_branches(tags, expected_default, expected_overrides):
    default, overrides = extract_base_branches(tags)
    assert default == expected_default
    assert overrides == expected_overrides


def test_resolve_base_branch():
    # 无显式指定 → None
    assert resolve_base_branch("phona/ttpos-flutter", None, {}) is None
    # 全局默认
    assert resolve_base_branch("phona/ttpos-flutter", "develop", {}) == "develop"
    # per-repo override 优先于全局默认
    assert resolve_base_branch(
        "phona/ttpos-flutter", "develop", {"ttpos-flutter": "feat/develop-hwt"}
    ) == "feat/develop-hwt"
    # 其他 repo 不受 override 影响，仍走全局默认
    assert resolve_base_branch(
        "phona/ttpos-server-go", "develop", {"ttpos-flutter": "feat/develop-hwt"}
    ) == "develop"
    # basename 不含 owner
    assert resolve_base_branch("ttpos-flutter", "develop", {"ttpos-flutter": "feat/x"}) == "feat/x"
