"""pr_health.check_pr_drift_once 单测：mock httpx + PG pool。"""
from __future__ import annotations

import pytest

from orchestrator import pr_health

# ──────────────────────────────────────────────────────────────────────
# 共用 fixtures / helpers
# ──────────────────────────────────────────────────────────────────────

def _make_pr(
    number: int = 1,
    head_sha: str = "abc123",
    base_ref: str = "main",
    base_sha: str = "def456",
    mergeable: bool | None = True,
    mergeable_state: str = "clean",
) -> dict:
    return {
        "number": number,
        "head": {"sha": head_sha},
        "base": {"ref": base_ref, "sha": base_sha},
        "mergeable": mergeable,
        "mergeable_state": mergeable_state,
    }


class _FakePool:
    def __init__(self):
        self.executed: list[tuple] = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


@pytest.fixture
def fake_pool():
    return _FakePool()


# ──────────────────────────────────────────────────────────────────────
# _predict_conflict
# ──────────────────────────────────────────────────────────────────────

def test_predict_conflict_false_when_mergeable_true():
    pr = _make_pr(mergeable=True, mergeable_state="clean")
    assert pr_health._predict_conflict(pr) is False


def test_predict_conflict_true_when_mergeable_false():
    pr = _make_pr(mergeable=False, mergeable_state="dirty")
    assert pr_health._predict_conflict(pr) is True


def test_predict_conflict_true_when_dirty_state():
    pr = _make_pr(mergeable=None, mergeable_state="dirty")
    assert pr_health._predict_conflict(pr) is True


def test_predict_conflict_false_when_mergeable_null():
    """null = GitHub 还没算完，保守返 False（宁漏不误报）。"""
    pr = _make_pr(mergeable=None, mergeable_state="unknown")
    assert pr_health._predict_conflict(pr) is False


# ──────────────────────────────────────────────────────────────────────
# check_pr_drift_once — skip conditions
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skip_when_no_github_token(monkeypatch):
    monkeypatch.setattr("orchestrator.pr_health.settings.github_token", "")
    result = await pr_health.check_pr_drift_once(["phona/sisyphus"])
    assert result.get("skipped") == "no_github_token"


@pytest.mark.asyncio
async def test_skip_pr_within_threshold(monkeypatch, fake_pool):
    """behind_count <= threshold 时不写 pr_drift_log。"""
    monkeypatch.setattr("orchestrator.pr_health.settings.github_token", "tok")
    monkeypatch.setattr("orchestrator.pr_health.settings.pr_health_behind_threshold", 5)
    monkeypatch.setattr("orchestrator.pr_health.db.get_pool", lambda: fake_pool)

    pr = _make_pr(number=42)

    async def fake_list_open_prs(client, repo):
        return [pr]

    async def fake_behind_count(client, repo, head_sha, base_ref):
        return 3  # <= threshold=5 → should skip

    monkeypatch.setattr(pr_health, "_list_open_prs", fake_list_open_prs)
    monkeypatch.setattr(pr_health, "_behind_count", fake_behind_count)

    result = await pr_health.check_pr_drift_once(["phona/sisyphus"])

    assert result["inserted"] == 0
    assert fake_pool.executed == []


# ──────────────────────────────────────────────────────────────────────
# check_pr_drift_once — drift detection
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_detects_pure_lint_drift(monkeypatch, fake_pool):
    """behind > threshold 且 mergeable=True → pure-lint-drift 写入 DB。"""
    monkeypatch.setattr("orchestrator.pr_health.settings.github_token", "tok")
    monkeypatch.setattr("orchestrator.pr_health.settings.pr_health_behind_threshold", 5)
    monkeypatch.setattr("orchestrator.pr_health.db.get_pool", lambda: fake_pool)

    pr = _make_pr(number=10, head_sha="head1", base_sha="base1",
                  mergeable=True, mergeable_state="clean")

    async def fake_list_open_prs(client, repo):
        return [pr]

    async def fake_behind_count(client, repo, head_sha, base_ref):
        return 12

    monkeypatch.setattr(pr_health, "_list_open_prs", fake_list_open_prs)
    monkeypatch.setattr(pr_health, "_behind_count", fake_behind_count)

    result = await pr_health.check_pr_drift_once(["phona/sisyphus"])

    assert result["inserted"] == 1
    assert len(fake_pool.executed) == 1
    _, args = fake_pool.executed[0]
    pr_number, repo, base_sha, behind, has_conflict, drift_kind = args
    assert pr_number == 10
    assert repo == "phona/sisyphus"
    assert base_sha == "base1"
    assert behind == 12
    assert has_conflict is False
    assert drift_kind == "pure-lint-drift"


@pytest.mark.asyncio
async def test_detects_semantic_drift(monkeypatch, fake_pool):
    """behind > threshold 且 mergeable=False → semantic-drift 写入 DB。"""
    monkeypatch.setattr("orchestrator.pr_health.settings.github_token", "tok")
    monkeypatch.setattr("orchestrator.pr_health.settings.pr_health_behind_threshold", 5)
    monkeypatch.setattr("orchestrator.pr_health.db.get_pool", lambda: fake_pool)

    pr = _make_pr(number=7, mergeable=False, mergeable_state="dirty")

    async def fake_list_open_prs(client, repo):
        return [pr]

    async def fake_behind_count(client, repo, head_sha, base_ref):
        return 20

    monkeypatch.setattr(pr_health, "_list_open_prs", fake_list_open_prs)
    monkeypatch.setattr(pr_health, "_behind_count", fake_behind_count)

    result = await pr_health.check_pr_drift_once(["phona/sisyphus"])

    assert result["inserted"] == 1
    _, args = fake_pool.executed[0]
    has_conflict, drift_kind = args[4], args[5]
    assert has_conflict is True
    assert drift_kind == "semantic-drift"


@pytest.mark.asyncio
async def test_multiple_prs_only_drifted_inserted(monkeypatch, fake_pool):
    """多 PR 中只有 behind > threshold 的被记录。"""
    monkeypatch.setattr("orchestrator.pr_health.settings.github_token", "tok")
    monkeypatch.setattr("orchestrator.pr_health.settings.pr_health_behind_threshold", 5)
    monkeypatch.setattr("orchestrator.pr_health.db.get_pool", lambda: fake_pool)

    prs = [
        _make_pr(number=1, head_sha="sha1"),
        _make_pr(number=2, head_sha="sha2"),
        _make_pr(number=3, head_sha="sha3"),
    ]
    behinds = {"sha1": 3, "sha2": 10, "sha3": 1}

    async def fake_list_open_prs(client, repo):
        return prs

    async def fake_behind_count(client, repo, head_sha, base_ref):
        return behinds[head_sha]

    monkeypatch.setattr(pr_health, "_list_open_prs", fake_list_open_prs)
    monkeypatch.setattr(pr_health, "_behind_count", fake_behind_count)

    result = await pr_health.check_pr_drift_once(["phona/sisyphus"])

    assert result["inserted"] == 1  # only PR#2 (behind=10)
    _, args = fake_pool.executed[0]
    assert args[0] == 2  # pr_number


@pytest.mark.asyncio
async def test_compare_error_skips_pr_continues(monkeypatch, fake_pool):
    """compare API 失败 → 该 PR 跳过，后续 PR 继续处理。"""
    monkeypatch.setattr("orchestrator.pr_health.settings.github_token", "tok")
    monkeypatch.setattr("orchestrator.pr_health.settings.pr_health_behind_threshold", 5)
    monkeypatch.setattr("orchestrator.pr_health.db.get_pool", lambda: fake_pool)

    prs = [_make_pr(number=1, head_sha="sha1"), _make_pr(number=2, head_sha="sha2")]
    call_count = 0

    async def fake_list_open_prs(client, repo):
        return prs

    async def fake_behind_count(client, repo, head_sha, base_ref):
        nonlocal call_count
        call_count += 1
        if head_sha == "sha1":
            raise RuntimeError("network error")
        return 15

    monkeypatch.setattr(pr_health, "_list_open_prs", fake_list_open_prs)
    monkeypatch.setattr(pr_health, "_behind_count", fake_behind_count)

    result = await pr_health.check_pr_drift_once(["phona/sisyphus"])

    assert result["inserted"] == 1  # PR#2 succeeds despite PR#1 failing
    assert call_count == 2


@pytest.mark.asyncio
async def test_repo_error_continues_other_repos(monkeypatch, fake_pool):
    """整个 repo 调用失败 → 跳过该 repo，继续其他 repo。"""
    monkeypatch.setattr("orchestrator.pr_health.settings.github_token", "tok")
    monkeypatch.setattr("orchestrator.pr_health.settings.pr_health_behind_threshold", 5)
    monkeypatch.setattr("orchestrator.pr_health.db.get_pool", lambda: fake_pool)

    call_order: list[str] = []

    async def fake_list_open_prs(client, repo):
        call_order.append(repo)
        if repo == "owner/bad-repo":
            raise RuntimeError("403 Forbidden")
        return [_make_pr(number=1)]

    async def fake_behind_count(client, repo, head_sha, base_ref):
        return 10

    monkeypatch.setattr(pr_health, "_list_open_prs", fake_list_open_prs)
    monkeypatch.setattr(pr_health, "_behind_count", fake_behind_count)

    result = await pr_health.check_pr_drift_once(["owner/bad-repo", "owner/good-repo"])

    assert "owner/bad-repo" in call_order
    assert "owner/good-repo" in call_order
    assert result["inserted"] == 1  # good-repo の PR が記録される
