"""checkers/pr_ci_watch.py 单测：mock manifest + GitHub API，验全绿/任一失败/全失败/超时。

M11：watch_pr_ci 签名改 req_id-first，repo / pr_number 从 manifest.yaml 读。
"""
from __future__ import annotations

import pytest

from orchestrator.checkers import manifest_io, pr_ci_watch
from orchestrator.checkers._types import CheckResult


def _pr_response(sha: str = "deadbeef" * 5):
    return {"head": {"sha": sha}, "number": 42}


def _runs_payload(*runs: dict) -> dict:
    return {"total_count": len(runs), "check_runs": list(runs)}


def _run(name: str, status: str = "completed", conclusion: str | None = "success") -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


def patch_manifest(monkeypatch, pr: dict):
    """让 manifest_io.read_manifest 返 {"pr": pr}。"""
    async def fake_read(req_id, timeout_sec=30):
        return {"pr": pr}
    monkeypatch.setattr(
        "orchestrator.checkers.pr_ci_watch.manifest_io.read_manifest",
        fake_read,
    )


# ── 单轮直绿 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_all_green(httpx_mock, monkeypatch):
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_manifest(monkeypatch, {"repo": "phona/ubox-crosser", "number": 42})

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/pulls/42",
        json=_pr_response(),
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(_run("lint"), _run("unit"), _run("integration")),
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0
    assert "lint=success" in result.stdout_tail
    assert "integration=success" in result.stdout_tail
    assert result.cmd.startswith("watch-pr-ci phona/ubox-crosser#42@deadbeef")


# ── 单轮失败 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_any_failed(httpx_mock, monkeypatch):
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_manifest(monkeypatch, {"repo": "phona/ubox-crosser", "number": 42})

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/pulls/42",
        json=_pr_response(),
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(
            _run("lint"),
            _run("unit", conclusion="failure"),
            _run("integration"),
        ),
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert result.passed is False
    assert result.exit_code == 1
    # failed_only 模式：只列失败的
    assert "unit=failure" in result.stdout_tail
    assert "lint=success" not in result.stdout_tail


# ── 全失败 ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_all_failed(httpx_mock, monkeypatch):
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_manifest(monkeypatch, {"repo": "phona/ubox-crosser", "number": 42})

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/pulls/42",
        json=_pr_response(),
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(
            _run("lint", conclusion="failure"),
            _run("unit", conclusion="cancelled"),
        ),
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert result.passed is False
    assert result.exit_code == 1
    assert "lint=failure" in result.stdout_tail
    assert "unit=cancelled" in result.stdout_tail


# ── pending → 超时 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_timeout(httpx_mock, monkeypatch):
    """所有 check-run 都还 in_progress，到 timeout 返 124。"""
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_manifest(monkeypatch, {"repo": "phona/ubox-crosser", "number": 42})

    # 让 sleep 立即返回，避免真等
    async def fast_sleep(_):
        return None
    monkeypatch.setattr(pr_ci_watch.asyncio, "sleep", fast_sleep)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/pulls/42",
        json=_pr_response(),
    )
    # check-runs 永远 pending：让 mock 复用同一个响应
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(_run("integration", status="in_progress", conclusion=None)),
        is_reusable=True,
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", poll_interval_sec=0, timeout_sec=0)

    assert result.passed is False
    assert result.exit_code == 124
    assert "timeout" in result.stderr_tail.lower()
    assert "integration=in_progress" in result.stdout_tail


# ── pending → 中途变绿 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_pending_then_pass(httpx_mock, monkeypatch):
    """前一轮 pending，后一轮全绿。"""
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_manifest(monkeypatch, {"repo": "phona/ubox-crosser", "number": 42})

    async def fast_sleep(_):
        return None
    monkeypatch.setattr(pr_ci_watch.asyncio, "sleep", fast_sleep)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/pulls/42",
        json=_pr_response(),
    )
    # 第一次：pending
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(_run("lint", status="in_progress", conclusion=None)),
    )
    # 第二次：成功
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(_run("lint")),
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert result.passed is True
    assert result.exit_code == 0


# ── PR 不存在（404）→ fail ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_pr_not_found(httpx_mock, monkeypatch):
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_manifest(monkeypatch, {"repo": "phona/ubox-crosser", "number": 999})

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/pulls/999",
        status_code=404,
        json={"message": "Not Found"},
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", poll_interval_sec=1, timeout_sec=60)

    assert result.passed is False
    assert result.exit_code == 1
    assert "PR lookup failed" in result.stderr_tail


# ── 空 check-runs → pending → 超时 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_empty_runs_times_out(httpx_mock, monkeypatch):
    """PR 刚开 GHA 还没触发，check-runs 为空 → pending → 超时。"""
    monkeypatch.setattr(pr_ci_watch.settings, "github_token", "ghp_xxx")
    patch_manifest(monkeypatch, {"repo": "phona/ubox-crosser", "number": 42})

    async def fast_sleep(_):
        return None
    monkeypatch.setattr(pr_ci_watch.asyncio, "sleep", fast_sleep)

    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/pulls/42",
        json=_pr_response(),
    )
    httpx_mock.add_response(
        url="https://api.github.com/repos/phona/ubox-crosser/commits/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef/check-runs?per_page=100",
        json=_runs_payload(),
        is_reusable=True,
    )

    result = await pr_ci_watch.watch_pr_ci("REQ-9", poll_interval_sec=0, timeout_sec=0)
    assert result.exit_code == 124


# ── manifest 缺 pr 段 / 缺字段 → 抛 ManifestReadError ───────────────────

@pytest.mark.asyncio
async def test_watch_pr_ci_raises_when_manifest_missing_pr(monkeypatch):
    async def fake_read(req_id, timeout_sec=30):
        return {"schema_version": 1}
    monkeypatch.setattr(
        "orchestrator.checkers.pr_ci_watch.manifest_io.read_manifest",
        fake_read,
    )
    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await pr_ci_watch.watch_pr_ci("REQ-9")
    assert "pr" in str(exc.value)


@pytest.mark.asyncio
async def test_watch_pr_ci_raises_when_pr_missing_number(monkeypatch):
    patch_manifest(monkeypatch, {"repo": "phona/ubox-crosser"})   # 缺 number
    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await pr_ci_watch.watch_pr_ci("REQ-9")
    assert "number" in str(exc.value)


@pytest.mark.asyncio
async def test_watch_pr_ci_raises_when_pr_missing_repo(monkeypatch):
    patch_manifest(monkeypatch, {"number": 42})
    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await pr_ci_watch.watch_pr_ci("REQ-9")
    assert "repo" in str(exc.value)


# ── _classify 单测 ───────────────────────────────────────────────────────

def test_classify_empty_is_pending():
    assert pr_ci_watch._classify([]) == "pending"


def test_classify_all_green():
    assert pr_ci_watch._classify([
        _run("a"), _run("b", conclusion="neutral"), _run("c", conclusion="skipped"),
    ]) == "pass"


def test_classify_any_fail_wins_over_pending():
    """fail 优先，即使还有 pending 没跑完。"""
    assert pr_ci_watch._classify([
        _run("a", conclusion="failure"),
        _run("b", status="in_progress", conclusion=None),
    ]) == "fail"


def test_classify_pending_when_any_in_progress():
    assert pr_ci_watch._classify([
        _run("a"),
        _run("b", status="in_progress", conclusion=None),
    ]) == "pending"


def test_classify_recognizes_all_fail_conclusions():
    for c in ["failure", "cancelled", "timed_out", "action_required", "stale"]:
        assert pr_ci_watch._classify([_run("x", conclusion=c)]) == "fail", c
