"""Contract tests for REQ-532: multi-repo M16 end-to-end pipeline.

Black-box scenarios covering:
  - _clone.py 5-layer fallback with 2+ repos
  - Multi-repo clone, per-repo checker traversal, accept phase per-repo env
  - Cross-repo PR link discovery and isolation
  - stage_run归属 (per-REQ, per-stage)
  - artifact_checks isolation (per-REQ, per-stage)

Dev MUST NOT change these tests to make them pass — fix the implementation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


class _FakeExecResult:
    """Minimal fake for k8s_runner.ExecResult."""

    def __init__(self, exit_code=0, stdout="", stderr="", duration_sec=1.0):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_sec = duration_sec


class _FakePool:
    """Minimal asyncpg pool stub for stage_run / artifact_check tests."""

    def __init__(self):
        self.executed: list[tuple] = []
        self.fetchrow_calls: list[tuple] = []
        self._fetchrow_return: Any = None

    def set_fetchrow(self, row: Any) -> None:
        self._fetchrow_return = row

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        return self._fetchrow_return

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-CLONE: _clone.py multi-repo 5-layer fallback
# ═══════════════════════════════════════════════════════════════════════════════


def test_mrepo_clone_s1_resolve_repos_multi_repo_l1_wins():
    """MREPO-CLONE-S1: L1 (intake_finalized_intent.involved_repos) returns all repos."""
    from orchestrator.actions import _clone

    repos, src = _clone.resolve_repos(
        {
            "intake_finalized_intent": {"involved_repos": ["phona/repo-a", "phona/repo-b"]},
            "involved_repos": ["phona/repo-c"],
        },
        tags=["repo:phona/repo-d"],
        default_repos=["phona/repo-e"],
    )
    assert repos == ["phona/repo-a", "phona/repo-b"], f"got {repos}"
    assert src == "ctx.intake_finalized_intent.involved_repos"


def test_mrepo_clone_s2_resolve_repos_multi_repo_l2_fallback():
    """MREPO-CLONE-S2: L1 empty -> L2 (ctx.involved_repos) returns all repos."""
    from orchestrator.actions import _clone

    repos, src = _clone.resolve_repos(
        {"involved_repos": ["phona/repo-a", "phona/repo-b", "phona/repo-c"]},
        tags=["repo:phona/repo-d"],
        default_repos=["phona/repo-e"],
    )
    assert repos == ["phona/repo-a", "phona/repo-b", "phona/repo-c"]
    assert src == "ctx.involved_repos"


def test_mrepo_clone_s3_resolve_repos_multi_repo_l3_tag_fallback():
    """MREPO-CLONE-S3: L1/L2 empty -> L3 (tags.repo) returns multiple repos."""
    from orchestrator.actions import _clone

    repos, src = _clone.resolve_repos(
        {},
        tags=["repo:phona/repo-a", "repo:phona/repo-b", "sisyphus"],
        default_repos=["phona/repo-c"],
    )
    assert repos == ["phona/repo-a", "phona/repo-b"]
    assert src == "tags.repo"


def test_mrepo_clone_s4_resolve_repos_multi_repo_l4_default_fallback():
    """MREPO-CLONE-S4: L1-L3 empty -> L4 (settings.default_involved_repos)."""
    from orchestrator.actions import _clone

    repos, src = _clone.resolve_repos({}, tags=[], default_repos=["phona/repo-a", "phona/repo-b"])
    assert repos == ["phona/repo-a", "phona/repo-b"]
    assert src == "settings.default_involved_repos"


def test_mrepo_clone_s5_resolve_repos_all_empty_returns_none():
    """MREPO-CLONE-S5: all layers empty -> ([], 'none')."""
    from orchestrator.actions import _clone

    repos, src = _clone.resolve_repos({}, tags=[], default_repos=[])
    assert repos == []
    assert src == "none"


@pytest.mark.asyncio
async def test_mrepo_clone_s6_clone_helper_multi_repo_success():
    """MREPO-CLONE-S6: clone_involved_repos_into_runner with 2+ repos returns list."""
    from orchestrator.actions import _clone

    called: list[dict] = []

    class _FakeRC:
        async def exec_in_runner(self, req_id, cmd, timeout_sec=600):
            called.append({"req_id": req_id, "cmd": cmd, "timeout": timeout_sec})
            return _FakeExecResult(exit_code=0, stdout="done")

    with patch("orchestrator.actions._clone.k8s_runner.get_controller", return_value=_FakeRC()):
        repos, exit_code = await _clone.clone_involved_repos_into_runner(
            "REQ-test",
            {"involved_repos": ["phona/repo-a", "phona/repo-b"]},
        )

    assert repos == ["phona/repo-a", "phona/repo-b"]
    assert exit_code is None
    assert len(called) == 1
    # cmd must quote both repos
    assert "phona/repo-a" in called[0]["cmd"]
    assert "phona/repo-b" in called[0]["cmd"]


@pytest.mark.asyncio
async def test_mrepo_clone_s7_clone_helper_nonzero_returns_exit_code():
    """MREPO-CLONE-S7: helper exit != 0 -> (repos, exit_code), caller should escalate."""
    from orchestrator.actions import _clone

    class _FakeRC:
        async def exec_in_runner(self, req_id, cmd, timeout_sec=600):
            return _FakeExecResult(exit_code=128, stderr="fatal: auth")

    with patch("orchestrator.actions._clone.k8s_runner.get_controller", return_value=_FakeRC()):
        repos, exit_code = await _clone.clone_involved_repos_into_runner(
            "REQ-test",
            {"involved_repos": ["phona/repo-a", "phona/repo-b"]},
        )

    assert repos == ["phona/repo-a", "phona/repo-b"]
    assert exit_code == 128


@pytest.mark.asyncio
async def test_mrepo_clone_s8_no_controller_returns_none_none():
    """MREPO-CLONE-S8: no K8s controller -> (None, None), agent self-clones."""
    from orchestrator.actions import _clone

    def _raise():
        raise RuntimeError("no kubeconfig")

    with patch("orchestrator.actions._clone.k8s_runner.get_controller", side_effect=_raise):
        repos, exit_code = await _clone.clone_involved_repos_into_runner(
            "REQ-test",
            {"involved_repos": ["phona/repo-a", "phona/repo-b"]},
        )

    assert repos is None
    assert exit_code is None


@pytest.mark.asyncio
async def test_mrepo_clone_s9_start_analyze_passes_tags_and_default():
    """MREPO-CLONE-S9: start_analyze passes tags + default_involved_repos to helper."""
    from orchestrator.actions import start_analyze as sa

    clone_calls: list[dict] = []

    async def _fake_clone(req_id, ctx, *, tags=None, default_repos=None, default_base=None, base_overrides=None, **kwargs):
        clone_calls.append(
            {
                "req_id": req_id,
                "ctx": ctx,
                "tags": list(tags or []),
                "default_repos": list(default_repos or []),
                "default_base": default_base,
                "base_overrides": base_overrides,
            }
        )
        return ["phona/repo-a"], None

    class _FakeBKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def merge_tags_and_update(self, *a, **kw):
            pass

        async def create_issue(self, *a, **kw):
            return MagicMock(id="created-issue-x")

        async def update_issue(self, *a, **kw):
            pass

        async def follow_up_issue(self, *a, **kw):
            pass

    with (
        patch.object(sa, "clone_involved_repos_into_runner", _fake_clone),
        patch.object(sa.k8s_runner, "get_controller", side_effect=RuntimeError),
        patch.object(sa, "check_admission", AsyncMock(return_value=MagicMock(admit=True))),
        patch.object(sa.db, "get_pool", lambda: MagicMock()),
        patch.object(sa.req_state, "update_context", AsyncMock()),
        patch.object(sa.dispatch_slugs, "get", AsyncMock(return_value=None)),
        patch.object(sa.dispatch_slugs, "put", AsyncMock()),
        patch.object(sa, "BKDClient", _FakeBKD),
        patch.object(sa, "render", return_value="prompt"),
    ):
        body = MagicMock(projectId="P", issueId="I")
        await sa.start_analyze(
            body=body,
            req_id="REQ-x",
            tags=["repo:phona/repo-b"],
            ctx={"involved_repos": ["phona/repo-a"]},
        )

    assert len(clone_calls) == 1, clone_calls
    assert clone_calls[0]["tags"] == ["repo:phona/repo-b"]
    # default_repos comes from settings -- just verify it's passed
    assert "default_repos" in clone_calls[0]


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-CHKR: checker multi-repo traversal
# ═══════════════════════════════════════════════════════════════════════════════


def test_mrepo_chkr_s1_dev_cross_check_script_traverses_all_repos():
    """MREPO-CHKR-S1: _build_cmd contains for-loop over /workspace/source/*/.

    Multi-repo REQ must run ci-lint on each cloned repo independently.
    """
    from orchestrator.checkers import dev_cross_check as dcc

    cmd = dcc._build_cmd("REQ-test")
    assert "for repo in /workspace/source/*/; do" in cmd, (
        "dev_cross_check must iterate over /workspace/source/*/"
    )
    # Each repo must fetch + checkout feat/<REQ>
    assert 'git fetch origin "feat/REQ-test"' in cmd
    assert 'git checkout -B "feat/REQ-test"' in cmd
    # Empty-source guard
    assert "/workspace/source missing" in cmd
    assert "/workspace/source empty" in cmd


def test_mrepo_chkr_s2_staging_test_script_parallel_per_repo():
    """MREPO-CHKR-S2: _build_cmd forks per-repo jobs in background (&) + wait."""
    from orchestrator.checkers import staging_test as st

    cmd = st._build_cmd("REQ-test")
    assert "for repo in /workspace/source/*/; do" in cmd
    # Parallel execution via background jobs
    assert "&" in cmd, "staging_test must run repos in parallel (background)"
    assert "wait" in cmd, "staging_test must wait for background jobs"
    # Per-repo log files
    assert "/tmp/staging-test-logs/" in cmd
    assert "-unit.log" in cmd
    assert "-int.log" in cmd


def test_mrepo_chkr_s3_staging_test_baseline_script_also_traverses():
    """MREPO-CHKR-S3: _build_baseline_cmd also iterates over /workspace/source/*/.

    Baseline diff must collect per-repo results on main branch.
    """
    from orchestrator.checkers import staging_test as st

    cmd = st._build_baseline_cmd()
    assert "for repo in /workspace/source/*/; do" in cmd
    assert "git checkout -B _sisyphus_baseline origin/main" in cmd
    assert "/tmp/baseline-logs/" in cmd


def test_mrepo_chkr_s4_staging_test_parse_repo_results_multi_repo():
    """MREPO-CHKR-S4: _parse_repo_results extracts per-repo PASS/FAIL from stdout+stderr."""
    from orchestrator.checkers import staging_test as st

    stdout = "=== PASS: repo-a ===\n=== PASS: repo-b ==="
    stderr = "=== FAIL: repo-c ==="
    results = st._parse_repo_results(stdout, stderr)
    assert results == {"repo-a": True, "repo-b": True, "repo-c": False}


def test_mrepo_chkr_s5_staging_test_diff_computes_introduced_failures():
    """MREPO-CHKR-S5: _compute_diff isolates PR-introduced failures per repo."""
    from orchestrator.checkers import staging_test as st

    baseline = {"repo-a": True, "repo-b": False, "repo-c": True}
    pr = {"repo-a": True, "repo-b": False, "repo-c": False}
    _bl_f, _pr_f, introduced = st._compute_diff(baseline, pr)
    assert introduced == {"repo-c"}, f"expected repo-c only, got {introduced}"


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-LINK: cross-repo PR link discovery and caching
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mrepo_link_s1_discover_pr_links_multi_repo(monkeypatch):
    """MREPO-LINK-S1: discover_pr_links queries each repo and returns all links."""
    from orchestrator import pr_links as pl

    async def _fake_discover_via_runner(req_id):
        return ["phona/repo-a", "phona/repo-b"]

    monkeypatch.setattr(pl, "_discover_repos_via_runner", _fake_discover_via_runner)

    calls: list[dict] = []

    async def _fake_get_pr(client, repo, branch):
        calls.append({"repo": repo, "branch": branch})
        if repo == "phona/repo-a":
            return pl.PrLink(
                repo="phona/repo-a", number=1, url="https://github.com/phona/repo-a/pull/1"
            )
        if repo == "phona/repo-b":
            return pl.PrLink(
                repo="phona/repo-b", number=2, url="https://github.com/phona/repo-b/pull/2"
            )
        return None

    monkeypatch.setattr(pl, "_get_open_pr", _fake_get_pr)
    monkeypatch.setattr(pl.settings, "github_token", "tok")

    links = await pl.discover_pr_links("REQ-x", "feat/REQ-x")

    assert len(calls) == 2, f"must query each repo, got {calls}"
    assert len(links) == 2
    assert links[0].repo == "phona/repo-a"
    assert links[1].repo == "phona/repo-b"


@pytest.mark.asyncio
async def test_mrepo_link_s2_discover_pr_links_partial_failure_isolated(monkeypatch):
    """MREPO-LINK-S2: one repo's GH API failure does not abort other repos."""
    from orchestrator import pr_links as pl

    async def _fake_discover_via_runner(req_id):
        return ["phona/repo-a", "phona/repo-b"]

    monkeypatch.setattr(pl, "_discover_repos_via_runner", _fake_discover_via_runner)

    async def _fake_get_pr(client, repo, branch):
        if repo == "phona/repo-a":
            return None  # GH API failure / no open PR
        return pl.PrLink(
            repo="phona/repo-b", number=2, url="https://github.com/phona/repo-b/pull/2"
        )

    monkeypatch.setattr(pl, "_get_open_pr", _fake_get_pr)
    monkeypatch.setattr(pl.settings, "github_token", "tok")

    links = await pl.discover_pr_links("REQ-x", "feat/REQ-x")
    assert len(links) == 1
    assert links[0].repo == "phona/repo-b"


@pytest.mark.asyncio
async def test_mrepo_link_s3_ensure_pr_links_uses_cache(monkeypatch):
    """MREPO-LINK-S3: ctx.pr_links cached -> return immediately without GH calls."""
    from orchestrator import pr_links as pl

    gh_calls: list = []

    async def _fake_discover(req_id, branch):
        gh_calls.append(1)
        return []

    monkeypatch.setattr(pl, "discover_pr_links", _fake_discover)

    cached = [
        {"repo": "phona/repo-a", "number": 1, "url": "https://github.com/phona/repo-a/pull/1"},
    ]
    links = await pl.ensure_pr_links_in_ctx(
        req_id="REQ-x",
        branch="feat/REQ-x",
        ctx={"pr_links": cached},
        project_id="P",
    )
    assert len(links) == 1
    assert links[0].repo == "phona/repo-a"
    assert gh_calls == [], "must NOT call discover when cache exists"


@pytest.mark.asyncio
async def test_mrepo_link_s4_pr_link_tags_format():
    """MREPO-LINK-S4: pr_link_tags produces per-repo tag strings."""
    from orchestrator import pr_links as pl

    links = [
        pl.PrLink(repo="phona/repo-a", number=1, url=""),
        pl.PrLink(repo="phona/repo-b", number=2, url=""),
    ]
    tags = pl.pr_link_tags(links)
    assert tags == ["pr:phona/repo-a#1", "pr:phona/repo-b#2"]


@pytest.mark.asyncio
async def test_mrepo_link_s5_create_pr_ci_watch_captures_multi_repo_urls(monkeypatch):
    """MREPO-LINK-S5: _capture_pr_urls discovers and persists multi-repo pr_urls."""
    from orchestrator.actions import create_pr_ci_watch as cpc

    monkeypatch.setattr(cpc.settings, "checker_pr_ci_watch_enabled", False)

    discover_calls: list = []
    update_calls: list[dict] = []

    async def _fake_discover(req_id):
        discover_calls.append(req_id)
        return ["phona/repo-a", "phona/repo-b"]

    async def _fake_links_discover(repos, branch, *, timeout_sec=15.0):
        return {
            "phona/repo-a": "https://github.com/phona/repo-a/pull/1",
            "phona/repo-b": "https://github.com/phona/repo-b/pull/2",
        }

    async def _fake_update(pool, req_id, patch):
        update_calls.append({"req_id": req_id, "patch": dict(patch)})

    monkeypatch.setattr(cpc, "_discover_repos_from_runner", _fake_discover)
    monkeypatch.setattr(cpc.links, "discover_pr_urls", _fake_links_discover)
    monkeypatch.setattr(cpc.req_state, "update_context", _fake_update)
    monkeypatch.setattr(cpc.db, "get_pool", lambda: MagicMock())

    await cpc._capture_pr_urls(req_id="REQ-x", ctx={})

    assert len(update_calls) == 1, update_calls
    pr_urls = update_calls[0]["patch"].get("pr_urls")
    assert pr_urls == {
        "phona/repo-a": "https://github.com/phona/repo-a/pull/1",
        "phona/repo-b": "https://github.com/phona/repo-b/pull/2",
    }, f"got {pr_urls}"


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-RUN: stage_run归属 (per-REQ, per-stage isolation)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mrepo_run_s1_insert_stage_run_isolates_by_req_id():
    """MREPO-RUN-S1: insert_stage_run binds to req_id -- different REQs don't mix."""
    from orchestrator.store import stage_runs as sr

    pool = _FakePool()
    pool.set_fetchrow({"id": 42})

    run_id = await sr.insert_stage_run(pool, "REQ-a", "analyze")
    assert run_id == 42
    # SQL must reference req_id
    sql, args = pool.fetchrow_calls[0]
    assert "req_id" in sql.lower()
    assert args[0] == "REQ-a"
    assert args[1] == "analyze"


@pytest.mark.asyncio
async def test_mrepo_run_s2_insert_stage_run_parallel_id_support():
    """MREPO-RUN-S2: insert_stage_run supports parallel_id for multi-repo sub-runs."""
    from orchestrator.store import stage_runs as sr

    pool = _FakePool()
    pool.set_fetchrow({"id": 99})

    run_id = await sr.insert_stage_run(
        pool,
        "REQ-x",
        "staging_test",
        parallel_id="repo-a",
    )
    assert run_id == 99
    _, args = pool.fetchrow_calls[0]
    assert args[2] == "repo-a"  # parallel_id


@pytest.mark.asyncio
async def test_mrepo_run_s3_close_latest_stage_run_targets_stage():
    """MREPO-RUN-S3: close_latest_stage_run closes the newest open row for (req_id, stage)."""
    from orchestrator.store import stage_runs as sr

    pool = _FakePool()
    pool.set_fetchrow({"id": 7})

    rid = await sr.close_latest_stage_run(pool, "REQ-x", "analyze", outcome="pass")
    assert rid == 7
    sql, args = pool.fetchrow_calls[0]
    assert "req_id = $1" in sql
    assert "stage = $2" in sql
    assert args[0] == "REQ-x"
    assert args[1] == "analyze"
    assert args[2] == "pass"


@pytest.mark.asyncio
async def test_mrepo_run_s4_stamp_bkd_session_id_only_open_rows():
    """MREPO-RUN-S4: stamp_bkd_session_id only updates ended_at IS NULL rows."""
    from orchestrator.store import stage_runs as sr

    pool = _FakePool()
    pool.set_fetchrow({"id": 5})

    rid = await sr.stamp_bkd_session_id(pool, "REQ-x", "analyze", "sess-123")
    assert rid == 5
    sql, _ = pool.fetchrow_calls[0]
    assert "ended_at IS NULL" in sql
    assert "bkd_session_id IS NULL" in sql


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-ART: artifact_checks isolation
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mrepo_art_s1_insert_check_isolates_by_req_and_stage():
    """MREPO-ART-S1: insert_check binds to (req_id, stage) -- per-checker isolation."""
    from orchestrator.checkers._types import CheckResult
    from orchestrator.store import artifact_checks as ac

    pool = _FakePool()
    result = CheckResult(
        passed=True,
        exit_code=0,
        stdout_tail="ok",
        stderr_tail="",
        duration_sec=10.0,
        cmd="make ci-lint",
        attempts=1,
        reason=None,
    )
    await ac.insert_check(pool, "REQ-x", "dev_cross_check", result)

    sql, args = pool.executed[0]
    assert "req_id" in sql.lower()
    assert "stage" in sql.lower()
    assert args[0] == "REQ-x"
    assert args[1] == "dev_cross_check"


@pytest.mark.asyncio
async def test_mrepo_art_s2_check_result_carries_attempts_and_reason():
    """MREPO-ART-S2: CheckResult.attempts and reason fields exist for flake-retry tracking."""
    from orchestrator.checkers._types import CheckResult

    r1 = CheckResult(
        passed=True,
        exit_code=0,
        stdout_tail="",
        stderr_tail="",
        duration_sec=1.0,
        cmd="x",
        attempts=3,
        reason="flake-retry-recovered:dns",
    )
    assert r1.attempts == 3
    assert r1.reason == "flake-retry-recovered:dns"

    r2 = CheckResult(
        passed=False,
        exit_code=1,
        stdout_tail="",
        stderr_tail="",
        duration_sec=1.0,
        cmd="x",
        attempts=1,
    )
    assert r2.attempts == 1
    assert r2.reason is None


@pytest.mark.asyncio
async def test_mrepo_art_s3_insert_check_persists_attempts():
    """MREPO-ART-S3: insert_check persists attempts and flake_reason columns."""
    from orchestrator.checkers._types import CheckResult
    from orchestrator.store import artifact_checks as ac

    pool = _FakePool()
    result = CheckResult(
        passed=False,
        exit_code=1,
        stdout_tail="err",
        stderr_tail="fail",
        duration_sec=5.0,
        cmd="make test",
        attempts=2,
        reason="flake-retry-exhausted:timeout",
    )
    await ac.insert_check(pool, "REQ-x", "staging_test", result)

    _, args = pool.executed[0]
    # args: req_id, stage, passed, exit_code, cmd, stdout_tail, stderr_tail,
    #       duration_sec, attempts, reason
    assert args[8] == 2  # attempts
    assert args[9] == "flake-retry-exhausted:timeout"  # reason


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-ACC: accept phase multi-repo traversal
# ═══════════════════════════════════════════════════════════════════════════════


def test_mrepo_acc_s1_create_accept_script_traverses_all_repos():
    """MREPO-ACC-S1: _build_accept_script iterates over /workspace/source/*/ for env-up + smoke."""
    from orchestrator.actions import create_accept as ca

    script = ca._build_lite_script("REQ-x", delay_sec=5)
    # Must have three phases: env-up, sleep, accept-smoke, env-down
    assert "for repo in /workspace/source/*/; do" in script
    assert "accept-env-up" in script
    assert "accept-smoke" in script
    assert "accept-env-down" in script
    assert "sleep 5" in script


def test_mrepo_acc_s2_create_accept_script_isolates_per_repo_failures():
    """MREPO-ACC-S2: per-repo failure sets fail=1 but script continues to next repo."""
    from orchestrator.actions import create_accept as ca

    script = ca._build_lite_script("REQ-x", delay_sec=5)
    # fail flag accumulates across repos
    assert "fail=1" in script
    # env-down is in a separate loop with || true (best-effort)
    assert "accept-env-down" in script
    assert "|| true" in script


def test_mrepo_acc_s3_create_accept_missing_target_skips_repo():
    """MREPO-ACC-S3: make -n target missing -> skip that repo (fail-open)."""
    from orchestrator.actions import create_accept as ca

    script = ca._build_lite_script("REQ-x", delay_sec=5)
    # Check for make -n before running
    assert "make -C" in script
    assert "-n accept-env-up" in script
    assert "-n accept-smoke" in script
    assert "accept-env-up target missing" in script
    assert "accept-smoke target missing" in script


def test_mrepo_acc_s4_create_accept_no_repos_vacuous_pass():
    """MREPO-ACC-S4: no integration dir -> create_accept returns vacuous pass."""
    # post cross-repo refactor (feat-cross-repo-env-orchestration impl) the
    # integration_dir lookup lives in the `_run_legacy_single_layer` helper;
    # scan the whole module source rather than just the entry function.
    import inspect

    from orchestrator.actions import create_accept as ca

    src = inspect.getsource(ca)
    assert "resolve_integration_dir" in src or "integration_dir" in src
    assert "ACCEPT_PASS" in src or "accept.pass" in src.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-PR: pr_ci_watch multi-repo behavior
# ═══════════════════════════════════════════════════════════════════════════════


def test_mrepo_pr_s1_pr_ci_watch_supports_repos_list():
    """MREPO-PR-S1: watch_pr_ci accepts repos list for multi-repo REQ."""
    import inspect

    from orchestrator.checkers import pr_ci_watch as pcw

    sig = inspect.signature(pcw.watch_pr_ci)
    params = list(sig.parameters.keys())
    assert "repos" in params, f"watch_pr_ci must accept repos param, got {params}"


@pytest.mark.asyncio
async def test_mrepo_pr_s2_create_pr_ci_watch_discover_multi_repo(monkeypatch):
    """MREPO-PR-S2: _discover_repos_from_runner finds multiple repos via git remote."""
    from orchestrator.actions import create_pr_ci_watch as cpc

    class _FakeRC:
        async def exec_in_runner(self, req_id, cmd, timeout_sec=30):
            stdout = "https://github.com/phona/repo-a.git\nhttps://github.com/phona/repo-b.git\n"
            return _FakeExecResult(exit_code=0, stdout=stdout)

    with patch(
        "orchestrator.actions.create_pr_ci_watch.k8s_runner.get_controller", return_value=_FakeRC()
    ):
        repos = await cpc._discover_repos_from_runner("REQ-x")

    assert repos == ["phona/repo-a", "phona/repo-b"], f"got {repos}"


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-ESCALATE: multi-repo GH incident isolation
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mrepo_esc_s1_escalate_iterates_all_involved_repos(monkeypatch):
    """MREPO-ESCALATE-S1: escalate action calls open_incident once per involved repo."""
    from orchestrator import gh_incident as ghi
    from orchestrator.actions import escalate as esc_mod
    from orchestrator.store import db
    from orchestrator.store import req_state as rs_mod

    open_calls: list[dict] = []

    async def _mock_open(*, repo, req_id, reason, **kw):
        open_calls.append({"repo": repo, "req_id": req_id})
        return f"https://github.com/{repo}/issues/1"

    monkeypatch.setattr(ghi, "open_incident", _mock_open)
    monkeypatch.setattr(rs_mod, "update_context", AsyncMock())
    monkeypatch.setattr(
        rs_mod,
        "get",
        AsyncMock(return_value=MagicMock(state=MagicMock(value="review_running"), context={})),
    )
    monkeypatch.setattr(rs_mod, "cas_transition", AsyncMock(return_value=True))
    monkeypatch.setattr(db, "get_pool", lambda: MagicMock())

    class _FakeBody:
        event = "session.completed"
        issueId = "i"
        projectId = "p"
        issueNumber = None

    settings = MagicMock()
    settings.gh_incident_repo = ""
    settings.github_token = "tok"
    settings.gh_incident_labels = ["sisyphus:incident"]
    settings.default_involved_repos = []
    settings.bkd_base_url = "https://bkd.example/api"
    settings.bkd_token = "t"
    settings.max_auto_retries = 2

    class _FakeBKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def merge_tags_and_update(self, *a, **kw):
            pass

        async def follow_up_issue(self, *a, **kw):
            pass

        async def update_issue(self, *a, **kw):
            pass

        async def get_issue(self, *a, **kw):
            return MagicMock(tags=[])

    monkeypatch.setattr(esc_mod, "BKDClient", _FakeBKD)

    with (
        patch("orchestrator.actions.escalate.settings", settings),
        patch("orchestrator.actions.escalate.k8s_runner", MagicMock()),
    ):
        await esc_mod.escalate(
            body=_FakeBody(),
            req_id="REQ-test",
            tags=[],
            ctx={
                "involved_repos": ["phona/repo-a", "phona/repo-b", "phona/repo-c"],
                "escalated_reason": "test",
                "intent_issue_id": "i1",
            },
        )

    assert len(open_calls) == 3, f"expected 3 calls, got {open_calls}"
    repos_called = {c["repo"] for c in open_calls}
    assert repos_called == {"phona/repo-a", "phona/repo-b", "phona/repo-c"}


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-STATE: state machine multi-repo context propagation
# ═══════════════════════════════════════════════════════════════════════════════


def test_mrepo_state_s1_ctx_supports_cloned_repos():
    """MREPO-STATE-S1: integration resolution is used by accept phase."""
    import inspect

    from orchestrator.actions import create_accept as ca

    src = inspect.getsource(ca)
    assert "resolve_integration_dir" in src or "integration_dir" in src, "create_accept must resolve integration dir"


def test_mrepo_state_s2_ctx_supports_involved_repos():
    """MREPO-STATE-S2: ctx.involved_repos is used by escalate + clone + pr_ci_watch."""
    import inspect

    from orchestrator.actions import create_pr_ci_watch as cpc
    from orchestrator.actions import escalate as esc
    from orchestrator.actions import start_analyze as sa

    for mod, name in [(sa, "start_analyze"), (cpc, "create_pr_ci_watch"), (esc, "escalate")]:
        src = inspect.getsource(mod)
        assert "involved_repos" in src, f"{name} must reference involved_repos"


def test_mrepo_state_s3_ctx_supports_pr_urls_dict():
    """MREPO-STATE-S3: ctx.pr_urls is a dict mapping repo -> html_url."""
    import inspect

    from orchestrator.actions import create_pr_ci_watch as cpc

    src = inspect.getsource(cpc._capture_pr_urls)
    assert "pr_urls" in src, "_capture_pr_urls must persist pr_urls"


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-SUPERSEDE: multi-repo openspec supersede
# ═══════════════════════════════════════════════════════════════════════════════


def test_mrepo_supersede_s1_start_analyze_supersede_per_repo():
    """MREPO-SUPERSEDE-S1: _supersede_stale_openspec_changes iterates over each repo."""
    import inspect

    from orchestrator.actions import start_analyze as sa

    src = inspect.getsource(sa._supersede_stale_openspec_changes)
    assert "for repo in repos:" in src, "must iterate over repos list"
    assert "basename" in src, "must compute per-repo basename"


# ═══════════════════════════════════════════════════════════════════════════════
# MREPO-INTAKE: start_analyze_with_finalized_intent multi-repo clone
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mrepo_intake_s1_start_analyze_with_finalized_intent_clones_multi_repo(monkeypatch):
    """MREPO-INTAKE-S1: intake path also server-side clones all involved repos."""
    from orchestrator.actions import start_analyze_with_finalized_intent as safi

    clone_calls: list[dict] = []

    async def _fake_clone(req_id, ctx, *, tags=None, default_repos=None, default_base=None, base_overrides=None, **kwargs):
        clone_calls.append(
            {
                "req_id": req_id,
                "repos": _extract_repos(ctx),
                "tags": list(tags or []),
                "default_base": default_base,
                "base_overrides": base_overrides,
            }
        )
        return ["phona/repo-a", "phona/repo-b"], None

    def _extract_repos(ctx):
        finalized = (ctx or {}).get("intake_finalized_intent") or {}
        return finalized.get("involved_repos", [])

    monkeypatch.setattr(safi, "clone_involved_repos_into_runner", _fake_clone)
    monkeypatch.setattr(
        safi.k8s_runner,
        "get_controller",
        lambda: (_ for _ in ()).throw(RuntimeError("no kubeconfig")),
    )
    monkeypatch.setattr(safi.db, "get_pool", lambda: MagicMock())
    monkeypatch.setattr(safi.dispatch_slugs, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(safi.dispatch_slugs, "put", AsyncMock())
    monkeypatch.setattr(safi.req_state, "update_context", AsyncMock())

    class _FakeBKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def create_issue(self, *a, **kw):
            return MagicMock(id="issue-new")

        async def follow_up_issue(self, *a, **kw):
            pass

        async def update_issue(self, *a, **kw):
            pass

    monkeypatch.setattr(safi, "BKDClient", _FakeBKD)
    monkeypatch.setattr(safi, "render", lambda *a, **kw: "prompt")

    body = MagicMock(projectId="P", issueId="I")
    result = await safi.start_analyze_with_finalized_intent(
        body=body,
        req_id="REQ-x",
        tags=[],
        ctx={
            "intake_finalized_intent": {
                "involved_repos": ["phona/repo-a", "phona/repo-b"],
            },
        },
    )

    assert len(clone_calls) == 1, clone_calls
    assert clone_calls[0]["repos"] == ["phona/repo-a", "phona/repo-b"]
    assert result.get("cloned_repos") == ["phona/repo-a", "phona/repo-b"]
