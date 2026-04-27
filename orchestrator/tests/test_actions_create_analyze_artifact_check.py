"""actions/create_analyze_artifact_check.py 单测：mock checker + artifact_checks
(REQ-analyze-artifact-check-1777254586)。

跟 test_actions_smoke.py 里 create_staging_test 同结构。
"""
from __future__ import annotations

import pytest


def patch_db(monkeypatch, target_module: str):
    pool_writes: list = []

    class P:
        async def execute(self, sql, *args):
            pool_writes.append((sql.strip()[:40], args))

        async def fetchrow(self, sql, *args):
            return None

    monkeypatch.setattr(f"orchestrator.actions.{target_module}.db.get_pool", lambda: P())
    return pool_writes


def make_body(issue_id="src-1", project_id="p", event="session.completed", title="T"):
    return type("B", (), {
        "issueId": issue_id, "projectId": project_id,
        "event": event, "title": title, "tags": [], "issueNumber": None,
    })()


@pytest.mark.asyncio
async def test_pass_emits_pass_and_writes_artifact(monkeypatch):
    from orchestrator.actions import create_analyze_artifact_check as mod
    from orchestrator.checkers._types import CheckResult

    fake_result = CheckResult(
        passed=True, exit_code=0,
        stdout_tail="ok\n", stderr_tail="",
        duration_sec=1.2,
        cmd="set -o pipefail; ...",
    )

    async def fake_run(req_id, *, timeout_sec=120):
        return fake_result

    monkeypatch.setattr(mod.checker, "run_analyze_artifact_check", fake_run)

    insert_calls: list = []

    async def fake_insert(pool, req_id, stage, result):
        insert_calls.append((req_id, stage, result))

    monkeypatch.setattr(mod.artifact_checks, "insert_check", fake_insert)
    patch_db(monkeypatch, "create_analyze_artifact_check")

    out = await mod.create_analyze_artifact_check(
        body=make_body(), req_id="REQ-9", tags=[], ctx={},
    )
    assert out["emit"] == "analyze-artifact-check.pass"
    assert out["passed"] is True
    assert out["exit_code"] == 0
    assert len(insert_calls) == 1
    assert insert_calls[0] == ("REQ-9", "analyze-artifact-check", fake_result)


@pytest.mark.asyncio
async def test_fail_emits_fail_with_exit_code(monkeypatch):
    from orchestrator.actions import create_analyze_artifact_check as mod
    from orchestrator.checkers._types import CheckResult

    fake_result = CheckResult(
        passed=False, exit_code=1,
        stdout_tail="", stderr_tail="=== FAIL analyze-artifact-check: tasks.md ...\n",
        duration_sec=0.7,
        cmd="set -o pipefail; ...",
    )

    async def fake_run(req_id, *, timeout_sec=120):
        return fake_result

    monkeypatch.setattr(mod.checker, "run_analyze_artifact_check", fake_run)

    insert_calls: list = []

    async def fake_insert(pool, req_id, stage, result):
        insert_calls.append((req_id, stage, result))

    monkeypatch.setattr(mod.artifact_checks, "insert_check", fake_insert)
    patch_db(monkeypatch, "create_analyze_artifact_check")

    out = await mod.create_analyze_artifact_check(
        body=make_body(), req_id="REQ-9", tags=[], ctx={},
    )
    assert out["emit"] == "analyze-artifact-check.fail"
    assert out["passed"] is False
    assert out["exit_code"] == 1
    assert len(insert_calls) == 1


@pytest.mark.asyncio
async def test_timeout_emits_fail_and_inserts_timeout_row(monkeypatch):
    from orchestrator.actions import create_analyze_artifact_check as mod

    async def fake_run(req_id, *, timeout_sec=120):
        raise TimeoutError()

    monkeypatch.setattr(mod.checker, "run_analyze_artifact_check", fake_run)

    insert_calls: list = []

    async def fake_insert(pool, req_id, stage, result):
        insert_calls.append((req_id, stage, result))

    monkeypatch.setattr(mod.artifact_checks, "insert_check", fake_insert)
    patch_db(monkeypatch, "create_analyze_artifact_check")

    out = await mod.create_analyze_artifact_check(
        body=make_body(), req_id="REQ-9", tags=[], ctx={},
    )
    assert out["emit"] == "analyze-artifact-check.fail"
    assert out["passed"] is False
    assert out["reason"] == "timeout"
    assert out["exit_code"] == -1
    assert len(insert_calls) == 1
    assert insert_calls[0][1] == "analyze-artifact-check"
    assert insert_calls[0][2].reason == "timeout"


@pytest.mark.asyncio
async def test_unhandled_exception_emits_fail(monkeypatch):
    from orchestrator.actions import create_analyze_artifact_check as mod

    async def fake_run(req_id, *, timeout_sec=120):
        raise RuntimeError("kubectl exec channel busted")

    monkeypatch.setattr(mod.checker, "run_analyze_artifact_check", fake_run)

    insert_calls: list = []

    async def fake_insert(pool, req_id, stage, result):
        insert_calls.append((req_id, stage, result))

    monkeypatch.setattr(mod.artifact_checks, "insert_check", fake_insert)
    patch_db(monkeypatch, "create_analyze_artifact_check")

    out = await mod.create_analyze_artifact_check(
        body=make_body(), req_id="REQ-9", tags=[], ctx={},
    )
    assert out["emit"] == "analyze-artifact-check.fail"
    assert out["passed"] is False
    assert "kubectl" in out["reason"]
    # 异常分支不写 artifact_checks（与 spec_lint 同语义）
    assert insert_calls == []
