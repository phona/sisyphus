"""
Contract tests for REQ-accept-m1-lite-1777344451:
feat(accept): minimal accept stage (descope thanatos MCP)

Black-box behavioral contracts derived exclusively from:
  openspec/changes/REQ-accept-m1-lite-1777344451/specs/accept-stage-lite/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is truly wrong, escalate to spec_fixer to correct the spec, not the test.

Function signatures (verified via inspect without reading source):
  create_accept(*, body, req_id, tags, ctx) -> dict
  teardown_accept_env(*, body, req_id, tags, ctx) -> dict

Scenarios covered:
  AML-S1  all repos pass → accept.pass + ctx.accept_result="pass"
  AML-S2  any repo fails → accept.fail + fail_repos in ctx + ctx.accept_result="fail"
  AML-S3  repo without accept-env-up target skipped (not failed) → overall PASS
  AML-S4  empty cloned_repos → accept.pass immediately, exec_in_runner NOT called
  AML-S5  ctx.accept_result="pass" (new path) → teardown emits teardown-done.pass
  AML-S6  ctx.accept_result absent (legacy path) + tags result:pass → teardown emits teardown-done.pass
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.k8s_runner import ExecResult

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_REQ_ID = "REQ-accept-m1-lite-1777344451"
_PROJECT = "test-proj"


def _make_body(project_id: str = _PROJECT) -> MagicMock:
    b = MagicMock()
    b.projectId = project_id
    b.issueId = "test-bkd-issue"
    return b


class _FakeRC:
    """Fake runner controller. Records exec_in_runner calls and returns pre-configured results."""

    def __init__(self, results: list[ExecResult]):
        self._results = list(results)
        self._idx = 0
        self.calls: list[str] = []

    async def exec_in_runner(self, req_id: str, command: str, **kw) -> ExecResult:
        self.calls.append(command)
        if self._idx < len(self._results):
            r = self._results[self._idx]
            self._idx += 1
            return r
        raise AssertionError(
            f"exec_in_runner called more times than expected ({self._idx + 1} calls); "
            f"call command: {command!r}"
        )


def _make_fake_rc(exit_code: int, stdout: str, stderr: str = "", count: int = 10) -> _FakeRC:
    results = [ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=1.0)] * count
    return _FakeRC(results)


def _make_mock_pool() -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    pool.execute.return_value = None
    return pool


def _capture_ctx_fn(written_ctx: dict):
    """Return side_effect for req_state.update_context.

    Handles both call forms:
      update_context(pool, req_id, {"key": "val"})   ← actual form
      update_context(pool, req_id, key="val")         ← kwargs form
    """
    async def _fn(*args, **updates):
        # args may be (pool, req_id, dict) or (pool, req_id) with kwargs
        for a in args:
            if isinstance(a, dict):
                written_ctx.update(a)
        written_ctx.update(updates)
    return _fn


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: create_accept — AML-S1 through AML-S4
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateAcceptAMLS1:
    """Spec: all repos pass → accept.pass + ctx.accept_result="pass"."""

    @pytest.mark.asyncio
    async def test_aml_s1_all_repos_pass_emits_accept_pass(self, monkeypatch):
        """
        AML-S1: cloned_repos=["repo-a"], script exits 0 with stdout ending in PASS
        → create_accept MUST:
          - return a dict with emit="accept.pass"
          - write ctx.accept_result="pass" via req_state.update_context
        """
        import orchestrator.actions.create_accept as ca_mod
        from orchestrator.actions.create_accept import create_accept

        written_ctx: dict = {}
        mock_req_state = AsyncMock()
        mock_req_state.update_context.side_effect = _capture_ctx_fn(written_ctx)

        fake_rc = _make_fake_rc(exit_code=0, stdout="phase complete\nPASS\n")
        monkeypatch.setattr(ca_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr(
            "orchestrator.actions.create_accept.k8s_runner.get_controller",
            lambda: fake_rc,
        )
        monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: _make_mock_pool())

        ctx = {"cloned_repos": ["repo-a"]}
        result = await create_accept(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=[_REQ_ID],
            ctx=ctx,
        )

        assert isinstance(result, dict), (
            f"create_accept must return a dict; got {type(result)}"
        )
        assert result.get("emit") == "accept.pass", (
            f"AML-S1: emit must be 'accept.pass' when all repos pass; got {result!r}"
        )
        assert written_ctx.get("accept_result") == "pass", (
            f"AML-S1: ctx.accept_result must be set to 'pass'; "
            f"update_context received: {written_ctx!r}"
        )

    @pytest.mark.asyncio
    async def test_aml_s1_multiple_repos_all_pass(self, monkeypatch):
        """
        AML-S1 (multi-repo): cloned_repos=["repo-a", "repo-b"], all scripts exit 0 → accept.pass
        """
        import orchestrator.actions.create_accept as ca_mod
        from orchestrator.actions.create_accept import create_accept

        written_ctx: dict = {}
        mock_req_state = AsyncMock()
        mock_req_state.update_context.side_effect = _capture_ctx_fn(written_ctx)

        fake_rc = _make_fake_rc(exit_code=0, stdout="env-up ok\nsmoke ok\nPASS\n")
        monkeypatch.setattr(ca_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr(
            "orchestrator.actions.create_accept.k8s_runner.get_controller",
            lambda: fake_rc,
        )
        monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: _make_mock_pool())

        ctx = {"cloned_repos": ["repo-a", "repo-b"]}
        result = await create_accept(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=[_REQ_ID],
            ctx=ctx,
        )

        assert result.get("emit") == "accept.pass", (
            f"AML-S1 (multi-repo): all repos pass → must emit 'accept.pass'; got {result!r}"
        )
        assert written_ctx.get("accept_result") == "pass", (
            f"AML-S1 (multi-repo): ctx.accept_result must be 'pass'; got {written_ctx!r}"
        )


class TestCreateAcceptAMLS2:
    """Spec: any repo env-up fail → accept.fail + fail_repos in ctx."""

    @pytest.mark.asyncio
    async def test_aml_s2_repo_fail_emits_accept_fail_with_fail_repos(self, monkeypatch):
        """
        AML-S2: cloned_repos=["repo-a"], script exits 1 with stdout ending in FAIL:repo-a
        → create_accept MUST:
          - return a dict with emit="accept.fail"
          - return value contains fail_repos=["repo-a"]
          - write ctx.accept_result="fail" via req_state.update_context
          - write ctx.accept_fail_repos containing "repo-a"
        """
        import orchestrator.actions.create_accept as ca_mod
        from orchestrator.actions.create_accept import create_accept

        written_ctx: dict = {}
        mock_req_state = AsyncMock()
        mock_req_state.update_context.side_effect = _capture_ctx_fn(written_ctx)

        fake_rc = _make_fake_rc(exit_code=1, stdout="env-up failed\nFAIL:repo-a\n")
        monkeypatch.setattr(ca_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr(
            "orchestrator.actions.create_accept.k8s_runner.get_controller",
            lambda: fake_rc,
        )
        monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: _make_mock_pool())

        ctx = {"cloned_repos": ["repo-a"]}
        result = await create_accept(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=[_REQ_ID],
            ctx=ctx,
        )

        assert isinstance(result, dict), (
            f"create_accept must return a dict on failure; got {type(result)}"
        )
        assert result.get("emit") == "accept.fail", (
            f"AML-S2: emit must be 'accept.fail' when a repo fails; got {result!r}"
        )
        fail_repos = result.get("fail_repos", [])
        assert "repo-a" in fail_repos, (
            f"AML-S2: return value must contain fail_repos=['repo-a']; got fail_repos={fail_repos!r}"
        )
        assert written_ctx.get("accept_result") == "fail", (
            f"AML-S2: ctx.accept_result must be 'fail'; update_context received: {written_ctx!r}"
        )
        ctx_fail_repos = written_ctx.get("accept_fail_repos", [])
        assert "repo-a" in ctx_fail_repos, (
            f"AML-S2: ctx.accept_fail_repos must contain 'repo-a'; "
            f"update_context received: {written_ctx!r}"
        )


class TestCreateAcceptAMLS3:
    """Spec: repo without accept-env-up target is skipped not failed."""

    @pytest.mark.asyncio
    async def test_aml_s3_missing_target_skipped_not_failed(self, monkeypatch):
        """
        AML-S3: repo has no accept-env-up Makefile target
        → the shell script skips with a warning (stderr) and does NOT set fail=1
        → overall result is PASS (stdout ends in PASS, exit 0)

        From create_accept perspective: if exec exits 0 with PASS, emit accept.pass.
        The spec says the skip MUST NOT propagate as a failure.
        """
        import orchestrator.actions.create_accept as ca_mod
        from orchestrator.actions.create_accept import create_accept

        written_ctx: dict = {}
        mock_req_state = AsyncMock()
        mock_req_state.update_context.side_effect = _capture_ctx_fn(written_ctx)

        # Script logs warning to stderr but exits 0 with PASS (target-missing = skip not fail)
        results = [
            ExecResult(
                exit_code=0,
                stdout="WARN: no accept-env-up target, skipping\nPASS\n",
                stderr="WARNING: accept-env-up not found in Makefile\n",
                duration_sec=0.5,
            )
        ] * 5
        fake_rc = _FakeRC(results)

        monkeypatch.setattr(ca_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr(
            "orchestrator.actions.create_accept.k8s_runner.get_controller",
            lambda: fake_rc,
        )
        monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: _make_mock_pool())

        ctx = {"cloned_repos": ["repo-without-target"]}
        result = await create_accept(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=[_REQ_ID],
            ctx=ctx,
        )

        assert result.get("emit") == "accept.pass", (
            f"AML-S3: repo without accept-env-up target must be skipped (not failed); "
            f"overall result must be accept.pass; got {result!r}"
        )
        assert written_ctx.get("accept_result") == "pass", (
            f"AML-S3: ctx.accept_result must be 'pass' when target is absent; "
            f"got {written_ctx!r}"
        )


class TestCreateAcceptAMLS4:
    """Spec: empty cloned_repos → accept.pass immediately, exec_in_runner NOT called."""

    @pytest.mark.asyncio
    async def test_aml_s4_empty_cloned_repos_vacuous_pass_no_exec(self, monkeypatch):
        """
        AML-S4: ctx.cloned_repos=[] → create_accept MUST:
          - return {"emit": "accept.pass"} immediately
          - NOT call exec_in_runner at all
        """
        import orchestrator.actions.create_accept as ca_mod
        from orchestrator.actions.create_accept import create_accept

        written_ctx: dict = {}
        mock_req_state = AsyncMock()
        mock_req_state.update_context.side_effect = _capture_ctx_fn(written_ctx)

        fake_rc = _FakeRC([])  # no results — any call is unexpected
        monkeypatch.setattr(ca_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr(
            "orchestrator.actions.create_accept.k8s_runner.get_controller",
            lambda: fake_rc,
        )

        ctx: dict = {"cloned_repos": []}
        result = await create_accept(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=[_REQ_ID],
            ctx=ctx,
        )

        assert result.get("emit") == "accept.pass", (
            f"AML-S4: empty cloned_repos must produce accept.pass; got {result!r}"
        )
        assert len(fake_rc.calls) == 0, (
            f"AML-S4: exec_in_runner MUST NOT be called for empty cloned_repos; "
            f"was called {len(fake_rc.calls)} time(s) with: {fake_rc.calls!r}"
        )

    @pytest.mark.asyncio
    async def test_aml_s4_absent_cloned_repos_vacuous_pass(self, monkeypatch):
        """
        AML-S4 (absent key): ctx has no cloned_repos key → same vacuous pass behavior.
        Spec: "empty or absent" → vacuous true.
        """
        import orchestrator.actions.create_accept as ca_mod
        from orchestrator.actions.create_accept import create_accept

        mock_req_state = AsyncMock()
        fake_rc = _FakeRC([])

        monkeypatch.setattr(ca_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr(
            "orchestrator.actions.create_accept.k8s_runner.get_controller",
            lambda: fake_rc,
        )

        ctx: dict = {}  # no cloned_repos key
        result = await create_accept(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=[_REQ_ID],
            ctx=ctx,
        )

        assert result.get("emit") == "accept.pass", (
            f"AML-S4: absent cloned_repos key must produce accept.pass; got {result!r}"
        )
        assert len(fake_rc.calls) == 0, (
            f"AML-S4: exec_in_runner MUST NOT be called when cloned_repos is absent; "
            f"was called {len(fake_rc.calls)} time(s)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: teardown_accept_env — AML-S5 and AML-S6
# ─────────────────────────────────────────────────────────────────────────────


class TestTeardownAcceptEnvAMLS5:
    """Spec: ctx.accept_result="pass" takes precedence → teardown emits teardown-done.pass."""

    @pytest.mark.asyncio
    async def test_aml_s5_ctx_accept_result_pass_emits_teardown_done_pass(self, monkeypatch):
        """
        AML-S5: ctx.accept_result="pass" (new mechanical path), tags do NOT contain result:pass
        → teardown_accept_env MUST emit "teardown-done.pass"

        Spec contract: ctx.accept_result MUST take precedence over tags when present.
        """
        import orchestrator.actions.teardown_accept_env as ta_mod
        from orchestrator.actions.teardown_accept_env import teardown_accept_env

        mock_req_state = AsyncMock()
        monkeypatch.setattr(ta_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: _make_mock_pool())

        ctx = {"accept_result": "pass"}
        tags = [_REQ_ID, "accept"]  # no result:pass tag

        result = await teardown_accept_env(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=tags,
            ctx=ctx,
        )

        assert isinstance(result, dict), (
            f"teardown_accept_env must return a dict; got {type(result)}"
        )
        assert result.get("emit") == "teardown-done.pass", (
            f"AML-S5: ctx.accept_result='pass' → must emit 'teardown-done.pass'; "
            f"got {result!r}"
        )

    @pytest.mark.asyncio
    async def test_aml_s5_ctx_accept_result_takes_precedence_over_fail_tag(self, monkeypatch):
        """
        AML-S5 (precedence test): ctx.accept_result="pass", tags has result:fail
        → ctx wins → must emit teardown-done.pass

        Spec: "ctx.accept_result MUST take precedence" — conflicting tags demonstrate this.
        """
        import orchestrator.actions.teardown_accept_env as ta_mod
        from orchestrator.actions.teardown_accept_env import teardown_accept_env

        mock_req_state = AsyncMock()
        monkeypatch.setattr(ta_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: _make_mock_pool())

        ctx = {"accept_result": "pass"}
        tags = [_REQ_ID, "result:fail"]  # tags say fail, but ctx says pass

        result = await teardown_accept_env(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=tags,
            ctx=ctx,
        )

        assert result.get("emit") == "teardown-done.pass", (
            f"AML-S5 (precedence): ctx.accept_result='pass' must override tags result:fail; "
            f"got {result!r}"
        )


class TestTeardownAcceptEnvAMLS6:
    """Spec: ctx.accept_result absent → fall back to result:pass/fail tag."""

    @pytest.mark.asyncio
    async def test_aml_s6_legacy_tags_fallback_result_pass(self, monkeypatch):
        """
        AML-S6: ctx has no accept_result (legacy BKD-agent path), tags contain result:pass
        → teardown_accept_env MUST fall back to tags and emit "teardown-done.pass"

        Spec contract: "when ctx.accept_result is absent, MUST fall back to result:pass tag".
        """
        import orchestrator.actions.teardown_accept_env as ta_mod
        from orchestrator.actions.teardown_accept_env import teardown_accept_env

        mock_req_state = AsyncMock()
        monkeypatch.setattr(ta_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: _make_mock_pool())

        ctx: dict = {}  # no accept_result — legacy path
        tags = [_REQ_ID, "accept", "result:pass"]

        result = await teardown_accept_env(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=tags,
            ctx=ctx,
        )

        assert isinstance(result, dict), (
            f"teardown_accept_env must return a dict; got {type(result)}"
        )
        assert result.get("emit") == "teardown-done.pass", (
            f"AML-S6: tags result:pass fallback → must emit 'teardown-done.pass'; "
            f"got {result!r}"
        )

    @pytest.mark.asyncio
    async def test_aml_s6_legacy_tags_fallback_result_fail(self, monkeypatch):
        """
        AML-S6 (fail branch): ctx has no accept_result, tags contain result:fail
        → teardown_accept_env MUST fall back to tags and emit "teardown-done.fail"

        Spec: the fallback reads whatever is in tags, preserving correct routing.
        """
        import orchestrator.actions.teardown_accept_env as ta_mod
        from orchestrator.actions.teardown_accept_env import teardown_accept_env

        mock_req_state = AsyncMock()
        monkeypatch.setattr(ta_mod, "req_state", mock_req_state, raising=False)
        monkeypatch.setattr("orchestrator.store.db.get_pool", lambda: _make_mock_pool())

        ctx: dict = {}  # no accept_result — legacy path
        tags = [_REQ_ID, "accept", "result:fail"]

        result = await teardown_accept_env(
            body=_make_body(),
            req_id=_REQ_ID,
            tags=tags,
            ctx=ctx,
        )

        assert isinstance(result, dict), (
            f"teardown_accept_env must return a dict; got {type(result)}"
        )
        assert result.get("emit") == "teardown-done.fail", (
            f"AML-S6 (fail): tags result:fail fallback → must emit 'teardown-done.fail'; "
            f"got {result!r}"
        )
