"""Challenger contract tests for REQ-427: Dispatch Idempotency by Slug.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-427/specs/dispatch-idempotency/spec.md

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is wrong, escalate to spec_fixer to correct the spec, not the test.

Scenarios covered:
  DISP-S1  slug hit returns cached issue_id without calling bkd.create_issue
  DISP-S2  no slug hit proceeds with create_issue and stores slug
  DISP-S3  get returns None for absent slug
  DISP-S4  put stores slug and is idempotent on conflict
  DISP-S5  round-aware slug distinguishes fixer rounds

Function signatures (verified via inspect without reading source):
  invoke_verifier(*, stage, trigger, req_id, project_id, ctx) -> dict
  dispatch_slugs.get(pool, slug: str) -> str | None
  dispatch_slugs.put(pool, slug: str, issue_id: str) -> None
  trigger: Literal['success', 'fail']
  BKDClient is used as async context manager: async with BKDClient(...) as client
  create_issue returns an object with .id attribute (not a dict)

Patches used (all on orchestrator.actions._verifier.*):
  BKDClient          — class-based fake that records create_issue calls
  dispatch_slugs.get — controls slug cache hit/miss
  dispatch_slugs.put — tracks slug persistence calls
  req_state          — async stub (avoids DB)
  db.get_pool        — returns minimal fake pool
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


# ─── Minimal fake pool ────────────────────────────────────────────────────────


class _FakePool:
    """Captures fetchrow / execute calls; returns configured fetchrow values."""

    def __init__(self, fetchrow_returns=()):
        self._returns = list(fetchrow_returns)
        self._pos = 0
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args) -> dict | None:
        if self._pos < len(self._returns):
            val = self._returns[self._pos]
            self._pos += 1
            return val
        return None

    async def execute(self, sql: str, *args) -> None:
        self.execute_calls.append((sql, args))


# ─── BKD context-manager fake ────────────────────────────────────────────────


class _FakeIssue:
    """Minimal BKD issue stub with .id attribute."""

    def __init__(self, issue_id: str) -> None:
        self.id = issue_id


def _make_bkd_class(new_issue_id: str = "verifier-new-id"):
    """Return a (BKDClass, create_issue_calls) pair.

    BKDClass supports `async with BKDClient(...) as client:` and records
    all create_issue invocations in create_issue_calls list.
    """
    create_issue_calls: list[tuple] = []

    class _FakeBKD:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def create_issue(self, *args, **kwargs):
            create_issue_calls.append((args, kwargs))
            return _FakeIssue(new_issue_id)

        async def follow_up_issue(self, *args, **kwargs):
            pass  # no-op for contract testing purposes

        async def update_issue(self, *args, **kwargs):
            pass

        async def get_issue(self, *args, **kwargs):
            return None

    return _FakeBKD, create_issue_calls


def _patch_verifier(
    *,
    get_return=None,
    put_mock=None,
    bkd_cls=None,
    req_state_mock=None,
):
    """Return list of patch context managers for invoke_verifier tests."""
    pool = _FakePool()
    if put_mock is None:
        put_mock = AsyncMock()
    if req_state_mock is None:
        req_state_mock = AsyncMock()

    if bkd_cls is None:
        bkd_cls, _ = _make_bkd_class()

    return [
        patch("orchestrator.actions._verifier.BKDClient", bkd_cls),
        patch(
            "orchestrator.store.dispatch_slugs.get",
            AsyncMock(return_value=get_return),
        ),
        patch("orchestrator.store.dispatch_slugs.put", put_mock),
        patch("orchestrator.actions._verifier.req_state", req_state_mock),
        patch("orchestrator.actions._verifier.db.get_pool", return_value=pool),
        patch("orchestrator.actions._verifier.settings", MagicMock()),
    ]


# ─── DISP-S3: get returns None for absent slug ───────────────────────────────


async def test_DISP_S3_get_returns_none_for_absent_slug():
    """
    DISP-S3: dispatch_slugs.get MUST return None when no matching slug exists.

    GIVEN an empty dispatch_slugs table
    WHEN  dispatch_slugs.get(pool, slug) is called
    THEN  the function returns None
    """
    from orchestrator.store import dispatch_slugs

    pool = _FakePool(fetchrow_returns=[None])
    result = await dispatch_slugs.get(pool, "verifier|REQ-1|spec_lint|fail|r0")

    assert result is None, (
        f"dispatch_slugs.get must return None for absent slug; got {result!r}"
    )


# ─── DISP-S4: put stores slug and is idempotent on conflict ──────────────────


async def test_DISP_S4_put_issues_insert_sql():
    """
    DISP-S4 (part 1): dispatch_slugs.put must issue an INSERT SQL statement.

    GIVEN dispatch_slugs table (any state)
    WHEN  dispatch_slugs.put(pool, slug, issue_id) is called
    THEN  at least one SQL execute call is made with INSERT
    """
    from orchestrator.store import dispatch_slugs

    pool = _FakePool()
    await dispatch_slugs.put(pool, "verifier|REQ-1|spec_lint|fail|r0", "abc123")

    assert len(pool.execute_calls) >= 1, (
        "dispatch_slugs.put must issue at least one execute call"
    )
    sql = pool.execute_calls[0][0].upper()
    assert "INSERT" in sql, (
        f"dispatch_slugs.put must use INSERT SQL; SQL was: {pool.execute_calls[0][0]!r}"
    )


async def test_DISP_S4_put_uses_on_conflict_do_nothing():
    """
    DISP-S4 (part 2): dispatch_slugs.put must use ON CONFLICT DO NOTHING so that
    repeated calls with the same slug do not raise.

    GIVEN dispatch_slugs.put called once with a slug
    WHEN  the same slug + issue_id is put again
    THEN  no exception is raised, SQL contains ON CONFLICT
    """
    from orchestrator.store import dispatch_slugs

    pool = _FakePool()
    slug = "verifier|REQ-1|spec_lint|fail|r0"

    # First put — must not raise
    await dispatch_slugs.put(pool, slug, "abc123")
    # Second put (same slug) — must not raise (idempotent)
    await dispatch_slugs.put(pool, slug, "abc123")

    assert len(pool.execute_calls) >= 1
    sql = pool.execute_calls[0][0].upper()
    assert "ON CONFLICT" in sql, (
        "dispatch_slugs.put SQL must contain ON CONFLICT DO NOTHING for idempotency; "
        f"SQL was: {pool.execute_calls[0][0]!r}"
    )


# ─── DISP-S1: slug hit → no BKD call, return cached issue_id ────────────────


async def test_DISP_S1_slug_hit_returns_cached_issue_id_no_bkd_call():
    """
    DISP-S1: when slug already exists in dispatch_slugs, invoke_verifier MUST
    return the cached issue_id and MUST NOT call bkd.create_issue.

    GIVEN slug 'verifier|REQ-1|spec_lint|fail|r0' exists with issue_id='abc123'
    WHEN  invoke_verifier(req_id='REQ-1', stage='spec_lint', trigger='fail', ctx={})
    THEN  result['verifier_issue_id'] == 'abc123' AND create_issue NOT called
    """
    from orchestrator.actions._verifier import invoke_verifier

    bkd_cls, create_issue_calls = _make_bkd_class("should-not-be-created")
    put_mock = AsyncMock()
    patches = _patch_verifier(
        get_return="abc123",  # slug hit
        bkd_cls=bkd_cls,
        put_mock=put_mock,
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = await invoke_verifier(
            stage="spec_lint",
            trigger="fail",
            req_id="REQ-1",
            project_id="proj-test",
            ctx={},
        )

    assert isinstance(result, dict), f"invoke_verifier must return dict; got {type(result)}"
    assert result.get("verifier_issue_id") == "abc123", (
        f"On slug hit, verifier_issue_id must be the cached 'abc123'; got {result!r}"
    )
    assert len(create_issue_calls) == 0, (
        f"bkd.create_issue MUST NOT be called on slug hit; "
        f"was called {len(create_issue_calls)} time(s)"
    )
    put_mock.assert_not_called()  # no duplicate slug insertion on hit


# ─── DISP-S2: no slug hit → create_issue called, slug stored ─────────────────


async def test_DISP_S2_no_slug_hit_calls_create_issue_and_stores_slug():
    """
    DISP-S2: when no slug exists, invoke_verifier MUST call bkd.create_issue
    and afterward store the slug in dispatch_slugs.

    GIVEN no slug 'verifier|REQ-1|spec_lint|fail|r0' in dispatch_slugs
    WHEN  invoke_verifier(req_id='REQ-1', stage='spec_lint', trigger='fail', ctx={})
    THEN  create_issue called once AND dispatch_slugs.put called with the slug
    """
    from orchestrator.actions._verifier import invoke_verifier

    bkd_cls, create_issue_calls = _make_bkd_class("verifier-s2-new")
    put_mock = AsyncMock()
    patches = _patch_verifier(
        get_return=None,  # no slug hit
        bkd_cls=bkd_cls,
        put_mock=put_mock,
    )

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = await invoke_verifier(
            stage="spec_lint",
            trigger="fail",
            req_id="REQ-1",
            project_id="proj-test",
            ctx={},
        )

    # create_issue must have been called exactly once
    assert len(create_issue_calls) == 1, (
        f"bkd.create_issue must be called once on slug miss; "
        f"called {len(create_issue_calls)} time(s)"
    )

    # dispatch_slugs.put must have been called with slug containing right components
    put_mock.assert_called_once()
    call_args = put_mock.call_args
    all_args = list(call_args.args) + list(call_args.kwargs.values())
    str_args = [a for a in all_args if isinstance(a, str)]

    assert any("verifier" in s for s in str_args), (
        f"dispatch_slugs.put slug must contain 'verifier'; call args: {call_args}"
    )
    assert any("REQ-1" in s for s in str_args), (
        f"dispatch_slugs.put slug must contain req_id 'REQ-1'; call args: {call_args}"
    )
    assert any("spec_lint" in s for s in str_args), (
        f"dispatch_slugs.put slug must contain stage 'spec_lint'; call args: {call_args}"
    )
    assert any("r0" in s for s in str_args), (
        f"dispatch_slugs.put slug must contain round 'r0' (ctx has no fixer_round); "
        f"call args: {call_args}"
    )


# ─── DISP-S5: round-aware slug distinguishes fixer rounds ────────────────────


async def test_DISP_S5_round_aware_slug_distinguishes_fixer_rounds():
    """
    DISP-S5: invoke_verifier with fixer_round=1 MUST compute a slug with 'r1',
    not reuse the r0 slug, causing a new create_issue call.

    GIVEN slug for round 0 exists; slug for round 1 does not
    WHEN  invoke_verifier(req_id='REQ-1', stage='spec_lint', trigger='success',
                          ctx={'fixer_round': 1})
    THEN  a NEW slug containing 'r1' is computed → no slug hit → create_issue called
    """
    from orchestrator.actions._verifier import invoke_verifier

    get_slugs_seen: list[str] = []

    async def _get_spy(pool, slug: str) -> str | None:
        get_slugs_seen.append(slug)
        if slug.endswith("|r0"):
            return "old-verifier-r0-id"  # r0 slug is cached
        return None  # r1 slug not in table

    bkd_cls, create_issue_calls = _make_bkd_class("verifier-r1-new")
    put_mock = AsyncMock()
    req_state_mock = AsyncMock()

    patches = [
        patch("orchestrator.actions._verifier.BKDClient", bkd_cls),
        patch("orchestrator.store.dispatch_slugs.get", _get_spy),
        patch("orchestrator.store.dispatch_slugs.put", put_mock),
        patch("orchestrator.actions._verifier.req_state", req_state_mock),
        patch("orchestrator.actions._verifier.db.get_pool", return_value=_FakePool()),
        patch("orchestrator.actions._verifier.settings", MagicMock()),
    ]

    with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
        result = await invoke_verifier(
            stage="spec_lint",
            trigger="success",
            req_id="REQ-1",
            project_id="proj-test",
            ctx={"fixer_round": 1},
        )

    # invoke_verifier must have called dispatch_slugs.get
    assert get_slugs_seen, "invoke_verifier must call dispatch_slugs.get at least once"

    # The slug must contain 'r1' (round 1), not just 'r0'
    r1_slugs = [s for s in get_slugs_seen if "r1" in s]
    assert r1_slugs, (
        f"invoke_verifier with fixer_round=1 must compute a slug containing 'r1'; "
        f"slugs checked: {get_slugs_seen!r}"
    )

    # Since r1 slug is absent → create_issue MUST be called
    assert len(create_issue_calls) == 1, (
        f"bkd.create_issue must be called once (r1 slug not in DB); "
        f"called {len(create_issue_calls)} time(s)"
    )

    # dispatch_slugs.put must store an r1 slug
    call_args = put_mock.call_args
    if call_args is not None:
        all_args = list(call_args.args) + list(call_args.kwargs.values())
        str_args = [a for a in all_args if isinstance(a, str)]
        assert any("r1" in s for s in str_args), (
            f"dispatch_slugs.put must store a slug with 'r1' for round 1; "
            f"stored args: {str_args!r}"
        )
