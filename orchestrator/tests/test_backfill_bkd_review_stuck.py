"""单测：orchestrator.maintenance.backfill_bkd_review_stuck

覆盖 spec.md 的 BBR-S1..S6（决策 + dry-run + apply + partial failure）。
"""
from __future__ import annotations

import io
import json

import httpx
import pytest

from orchestrator.maintenance.backfill_bkd_review_stuck import (
    is_safe_target,
    run,
    select_targets,
)


def _issue(
    *,
    id: str = "i1",
    status: str = "review",
    tags: list[str] | None = None,
    session: str = "completed",
) -> dict:
    return {
        "id": id,
        "statusId": status,
        "tags": tags if tags is not None else [],
        "sessionStatus": session,
    }


# ─── BBR-S1 ─────────────────────────────────────────────────────────────────


def test_bbr_s1_verifier_review_completed_is_selected():
    issue = _issue(
        tags=[
            "verifier",
            "REQ-foo-1234",
            "verify:staging_test",
            "decision:escalate",
        ],
        session="completed",
    )
    ok, reason = is_safe_target(issue)
    assert ok is True
    assert reason.startswith("role=verifier;session=completed")

    selected = select_targets([issue])
    assert len(selected) == 1
    assert selected[0][0]["id"] == "i1"


# ─── BBR-S2 ─────────────────────────────────────────────────────────────────


def test_bbr_s2_intent_issue_without_role_tag_rejected():
    issue = _issue(tags=["REQ-foo-1234"], session="completed")
    ok, reason = is_safe_target(issue)
    assert ok is False
    assert reason == "no-role-tag"


# ─── BBR-S3 ─────────────────────────────────────────────────────────────────


def test_bbr_s3_running_session_rejected():
    issue = _issue(
        tags=["fixer", "REQ-foo-1234"],
        session="running",
    )
    ok, reason = is_safe_target(issue)
    assert ok is False
    assert reason == "session-running"


# ─── BBR-S2 兄弟：no-req-tag ────────────────────────────────────────────────


def test_no_req_tag_rejected():
    """role 有但 REQ-* tag 没 → 跳（防误伤孤儿 issue）。"""
    issue = _issue(tags=["verifier"], session="completed")
    ok, reason = is_safe_target(issue)
    assert ok is False
    assert reason == "no-req-tag"


def test_status_not_review_rejected():
    issue = _issue(
        status="working",
        tags=["verifier", "REQ-x-1"],
        session="completed",
    )
    ok, reason = is_safe_target(issue)
    assert ok is False
    assert reason == "not-review"


# ─── 通用 fixture：模拟 BKD list 返回 + capture PATCH 调用 ────────────────────


class _FakeTransport(httpx.AsyncBaseTransport):
    """模拟 BKD HTTP server。注入 list 结果 + 每条 PATCH 的预设 status code。"""

    def __init__(
        self,
        *,
        list_issues: list[dict],
        patch_outcomes: dict[str, int] | None = None,
    ):
        self.list_issues = list_issues
        # patch_outcomes: {issue_id -> status_code}; 默认全 200
        self.patch_outcomes = patch_outcomes or {}
        self.patch_calls: list[tuple[str, dict]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        if method == "GET" and path.endswith("/issues"):
            envelope = {"success": True, "data": self.list_issues}
            return httpx.Response(200, text=json.dumps(envelope))
        if method == "PATCH" and "/issues/" in path:
            issue_id = path.rsplit("/", 1)[-1]
            body = json.loads(request.content.decode())
            self.patch_calls.append((issue_id, body))
            sc = self.patch_outcomes.get(issue_id, 200)
            envelope = (
                {"success": True, "data": {"id": issue_id, "statusId": "done"}}
                if sc < 400
                else {"success": False, "error": f"HTTP {sc}"}
            )
            return httpx.Response(sc, text=json.dumps(envelope))
        return httpx.Response(
            404,
            text=json.dumps({"success": False, "error": f"unexpected {method} {path}"}),
        )


async def _run_with_transport(
    *,
    transport: _FakeTransport,
    apply: bool,
    monkeypatch,
) -> tuple[int, str, str]:
    """跑 run()，把 httpx.AsyncClient 换成走 transport 的 client。返 (exit, stdout, stderr)。"""
    out = io.StringIO()
    err = io.StringIO()

    real_client = httpx.AsyncClient

    def _factory(*a, **kw):
        kw.pop("timeout", None)
        return real_client(transport=transport)

    monkeypatch.setattr(
        "orchestrator.maintenance.backfill_bkd_review_stuck.httpx.AsyncClient",
        _factory,
    )

    rc = await run(
        project_id="p",
        bkd_base_url="http://test",
        apply=apply,
        out=out,
        err=err,
    )
    return rc, out.getvalue(), err.getvalue()


# ─── BBR-S4 dry-run ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bbr_s4_dry_run_zero_patches(monkeypatch):
    issues = [
        _issue(id="a", tags=["verifier", "REQ-x-1"], session="completed"),
        _issue(id="b", tags=["fixer", "REQ-y-2"], session="failed"),
    ]
    t = _FakeTransport(list_issues=issues)

    rc, stdout, _ = await _run_with_transport(
        transport=t, apply=False, monkeypatch=monkeypatch,
    )
    assert rc == 0
    assert t.patch_calls == []  # 关键：没发任何 PATCH

    lines = [json.loads(line) for line in stdout.strip().splitlines()]
    assert len(lines) == 2
    for line in lines:
        assert line["action"] == "skipped"
        assert line["reason"]  # 非空


# ─── BBR-S5 apply 主路径 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bbr_s5_apply_patches_each_with_statusid_only(monkeypatch):
    issues = [
        _issue(id="a", tags=["verifier", "REQ-x-1"], session="completed"),
        _issue(id="b", tags=["fixer", "REQ-y-2"], session="failed"),
        _issue(id="c", tags=["analyze", "REQ-z-3"], session="failed"),
    ]
    t = _FakeTransport(list_issues=issues)

    rc, stdout, _ = await _run_with_transport(
        transport=t, apply=True, monkeypatch=monkeypatch,
    )
    assert rc == 0
    # 三条都被 PATCH 一次
    assert {iid for iid, _ in t.patch_calls} == {"a", "b", "c"}
    assert len(t.patch_calls) == 3
    # body 必须只有 statusId，没有 tags
    for _, body in t.patch_calls:
        assert body == {"statusId": "done"}
        assert "tags" not in body

    lines = [json.loads(line) for line in stdout.strip().splitlines()]
    assert all(line["action"] == "patched" for line in lines)
    assert len(lines) == 3


# ─── BBR-S6 partial failure ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bbr_s6_partial_failure_continues_exit_zero(monkeypatch):
    issues = [
        _issue(id="a", tags=["verifier", "REQ-x-1"], session="completed"),
        _issue(id="b", tags=["fixer", "REQ-y-2"], session="failed"),  # 故障
        _issue(id="c", tags=["analyze", "REQ-z-3"], session="failed"),
    ]
    t = _FakeTransport(
        list_issues=issues,
        patch_outcomes={"b": 503},  # 第二条 503
    )

    rc, stdout, _ = await _run_with_transport(
        transport=t, apply=True, monkeypatch=monkeypatch,
    )
    # ≥1 成功 → exit 0
    assert rc == 0
    # 三次 PATCH 都被尝试（loop 不中断）
    assert {iid for iid, _ in t.patch_calls} == {"a", "b", "c"}

    lines = [json.loads(line) for line in stdout.strip().splitlines()]
    by_id = {line["issue_id"]: line for line in lines}
    assert by_id["a"]["action"] == "patched"
    assert by_id["c"]["action"] == "patched"
    assert by_id["b"]["action"] == "failed"
    assert by_id["b"]["reason"]


@pytest.mark.asyncio
async def test_all_failures_exits_nonzero(monkeypatch):
    """全部 PATCH 都失败 → exit 1（caller 能感知）"""
    issues = [
        _issue(id="a", tags=["verifier", "REQ-x-1"], session="completed"),
        _issue(id="b", tags=["fixer", "REQ-y-2"], session="failed"),
    ]
    t = _FakeTransport(
        list_issues=issues,
        patch_outcomes={"a": 503, "b": 503},
    )
    rc, _, _ = await _run_with_transport(
        transport=t, apply=True, monkeypatch=monkeypatch,
    )
    assert rc == 1


@pytest.mark.asyncio
async def test_intent_and_running_filtered_in_full_pipeline(monkeypatch):
    """跨 filter：list 里同时有 intent / running / 合法候选；仅合法候选被 PATCH。"""
    issues = [
        _issue(id="intent", tags=["REQ-x-1"], session="completed"),  # no role
        _issue(id="live",
               tags=["analyze", "REQ-x-1"], session="running"),  # session running
        _issue(id="ok",
               tags=["verifier", "REQ-x-1"], session="completed"),  # 候选
        _issue(id="orphan", tags=["verifier"], session="completed"),  # no REQ
        _issue(id="working",
               status="working", tags=["fixer", "REQ-x-1"],
               session="completed"),  # 不是 review
    ]
    t = _FakeTransport(list_issues=issues)
    rc, _, _ = await _run_with_transport(
        transport=t, apply=True, monkeypatch=monkeypatch,
    )
    assert rc == 0
    # 只有 "ok" 被 PATCH
    assert [iid for iid, _ in t.patch_calls] == ["ok"]


@pytest.mark.asyncio
async def test_list_failure_exits_two(monkeypatch):
    """list-issues 失败 → exit 2，0 个 PATCH 尝试。"""

    class _Failing(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                503, text=json.dumps({"success": False, "error": "down"}),
            )

    real_client = httpx.AsyncClient

    def _factory(*a, **kw):
        kw.pop("timeout", None)
        return real_client(transport=_Failing())

    monkeypatch.setattr(
        "orchestrator.maintenance.backfill_bkd_review_stuck.httpx.AsyncClient",
        _factory,
    )
    out, err = io.StringIO(), io.StringIO()
    rc = await run(
        project_id="p",
        bkd_base_url="http://test",
        apply=True,
        out=out,
        err=err,
    )
    assert rc == 2
    assert out.getvalue() == ""  # 没有 audit line 输出
