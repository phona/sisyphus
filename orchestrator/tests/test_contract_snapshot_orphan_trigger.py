"""Contract tests for snapshot orphan-recovery (REQ-snapshot-loop-init-orphan-trigger-1777220172).

Black-box behavioral contracts derived from:
  openspec/changes/REQ-snapshot-loop-init-orphan-trigger-1777220172/
  specs/snapshot-orphan-trigger/spec.md

Scenarios covered:
  SNAP-ORPHAN-S1  orphan intent:analyze triggers INTENT_ANALYZE
  SNAP-ORPHAN-S2  issue already tracked in req_state is skipped
  SNAP-ORPHAN-S3  issue already past entry (analyze tag) is skipped
  SNAP-ORPHAN-S4  issue in BKD status done is skipped
  SNAP-ORPHAN-S5  orphan recovery runs when obs pool is absent, sync_once returns 0
"""
from __future__ import annotations

from orchestrator import snapshot
from orchestrator.bkd import Issue
from orchestrator.state import Event, ReqState
from orchestrator.store import db


# ─── Shared helpers ───────────────────────────────────────────────────────────


class _FakePool:
    """asyncpg pool stub: returns preset project_id rows via fetch()."""

    def __init__(self, project_ids=()):
        self._rows = [{"project_id": p} for p in project_ids]

    async def fetch(self, sql, *args):
        return self._rows

    async def execute(self, sql, *args):
        pass

    async def fetchrow(self, sql, *args):
        return None

    async def executemany(self, sql, data, *args):
        pass


class _FakeSettings:
    def __init__(self, exclude=()):
        self.snapshot_exclude_project_ids = list(exclude)
        self.bkd_base_url = "https://bkd.example.test/api"
        self.bkd_token = "test-token"
        self.snapshot_interval_sec = 300


def _make_issue(**kw) -> Issue:
    base = dict(
        id="i-9",
        project_id="proj-a",
        issue_number=9,
        title="Some orphan intent issue",
        status_id="working",
        tags=["intent:analyze"],
        session_status=None,
        description=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    base.update(kw)
    return Issue(**base)


class _ReqRow:
    """Minimal req_state row stub."""

    def __init__(self, state: ReqState = ReqState.INIT, ctx: dict | None = None):
        self.state = state
        self.context = ctx or {}


def _make_fake_bkd_class(issues: list[Issue]):
    """Factory that returns a BKDClient-shaped class yielding the given issues."""

    class _FakeBKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def list_issues(self, project_id, **kw):
            return list(issues)

    return _FakeBKD


class _FakeReqState:
    """Replaces the req_state module in snapshot's namespace."""

    def __init__(self, get_side_effect: list):
        self._get_side_effect = list(get_side_effect)
        self._get_call_count = 0
        self.insert_init_calls: list[dict] = []
        self.get_calls: list[str] = []

    async def get(self, pool, req_id):
        self.get_calls.append(req_id)
        idx = self._get_call_count
        self._get_call_count += 1
        if idx < len(self._get_side_effect):
            return self._get_side_effect[idx]
        return None

    async def insert_init(self, pool, req_id, project_id, context=None):
        self.insert_init_calls.append(
            {"req_id": req_id, "project_id": project_id, "context": context or {}}
        )


class _FakeEngine:
    """Replaces the engine module in snapshot's namespace."""

    def __init__(self):
        self.step_calls: list[dict] = []

    async def step(self, pool, *, body, req_id, project_id, tags, cur_state, ctx, event):
        self.step_calls.append(
            {
                "body": body,
                "req_id": req_id,
                "project_id": project_id,
                "tags": tags,
                "cur_state": cur_state,
                "ctx": ctx,
                "event": event,
            }
        )


# ─── S1: orphan intent:analyze triggers INTENT_ANALYZE ───────────────────────


async def test_snap_orphan_s1_triggers_intent_analyze(monkeypatch):
    """SNAP-ORPHAN-S1: issue with intent:analyze, no req_state row → engine.step fired.

    GIVEN  BKD list returns one issue: id="i-9", issueNumber=9, statusId="working",
           tags=["intent:analyze"], no matching row in req_state for REQ-9
    WHEN   snapshot.sync_once() runs for that project
    THEN   req_state.insert_init is called once with req_id="REQ-9",
           context containing intent_issue_id="i-9" and snapshot_recovered=True
    AND    engine.step is called once with event=INTENT_ANALYZE, cur_state=INIT,
           body exposing issueId="i-9", projectId, tags containing "intent:analyze"
    """
    PROJECT = "proj-a"
    issue = _make_issue(
        id="i-9",
        project_id=PROJECT,
        issue_number=9,
        tags=["intent:analyze"],
        status_id="working",
    )

    # After insert_init, re-get returns a fresh INIT row (simulating concurrent-safe insert)
    post_insert_row = _ReqRow(ReqState.INIT)
    fake_req_state = _FakeReqState(get_side_effect=[None, post_insert_row])
    fake_engine = _FakeEngine()

    monkeypatch.setattr(db, "get_pool", lambda: _FakePool([PROJECT]))
    monkeypatch.setattr(db, "get_obs_pool", lambda: _FakePool())
    monkeypatch.setattr(snapshot, "BKDClient", _make_fake_bkd_class([issue]))
    monkeypatch.setattr(snapshot, "settings", _FakeSettings())
    monkeypatch.setattr(snapshot, "req_state", fake_req_state)
    monkeypatch.setattr(snapshot, "engine", fake_engine)

    await snapshot.sync_once()

    # insert_init must be called exactly once
    assert len(fake_req_state.insert_init_calls) == 1, (
        f"SNAP-ORPHAN-S1: expected insert_init called once, got {fake_req_state.insert_init_calls}"
    )
    ic = fake_req_state.insert_init_calls[0]
    assert ic["req_id"] == "REQ-9", (
        f"SNAP-ORPHAN-S1: insert_init req_id must be 'REQ-9', got {ic['req_id']!r}"
    )
    assert ic["project_id"] == PROJECT, (
        f"SNAP-ORPHAN-S1: insert_init project_id must be {PROJECT!r}, got {ic['project_id']!r}"
    )
    ctx = ic["context"]
    assert ctx.get("intent_issue_id") == "i-9", (
        f"SNAP-ORPHAN-S1: context.intent_issue_id must be 'i-9', got {ctx!r}"
    )
    assert ctx.get("snapshot_recovered") is True, (
        f"SNAP-ORPHAN-S1: context.snapshot_recovered must be True, got {ctx!r}"
    )

    # engine.step must be called exactly once
    assert len(fake_engine.step_calls) == 1, (
        f"SNAP-ORPHAN-S1: expected engine.step called once, got {fake_engine.step_calls}"
    )
    sc = fake_engine.step_calls[0]
    assert sc["event"] == Event.INTENT_ANALYZE, (
        f"SNAP-ORPHAN-S1: event must be INTENT_ANALYZE, got {sc['event']!r}"
    )
    assert sc["cur_state"] == ReqState.INIT, (
        f"SNAP-ORPHAN-S1: cur_state must be INIT, got {sc['cur_state']!r}"
    )
    body = sc["body"]
    assert body is not None, "SNAP-ORPHAN-S1: engine.step body must not be None"
    assert getattr(body, "issueId", None) == "i-9", (
        f"SNAP-ORPHAN-S1: body.issueId must be 'i-9', got {getattr(body, 'issueId', None)!r}"
    )
    assert getattr(body, "projectId", None) == PROJECT, (
        f"SNAP-ORPHAN-S1: body.projectId must be {PROJECT!r}, got {getattr(body, 'projectId', None)!r}"
    )
    body_tags = getattr(body, "tags", [])
    assert "intent:analyze" in body_tags, (
        f"SNAP-ORPHAN-S1: body.tags must contain 'intent:analyze', got {body_tags!r}"
    )


# ─── S2: already tracked in req_state → skip ─────────────────────────────────


async def test_snap_orphan_s2_skips_when_already_in_req_state(monkeypatch):
    """SNAP-ORPHAN-S2: issue has REQ-42 tag, req_state.get returns a row → no trigger.

    GIVEN  a BKD issue with tags=["intent:analyze", "REQ-42"] and
           req_state.get(pool, "REQ-42") returns a non-None row
    WHEN   snapshot.sync_once() runs
    THEN   req_state.insert_init MUST NOT be called for REQ-42
    AND    engine.step MUST NOT be called
    """
    PROJECT = "proj-b"
    issue = _make_issue(
        id="i-42",
        project_id=PROJECT,
        issue_number=42,
        tags=["intent:analyze", "REQ-42"],
        status_id="working",
    )

    existing_row = _ReqRow(ReqState.INIT)
    fake_req_state = _FakeReqState(get_side_effect=[existing_row])
    fake_engine = _FakeEngine()

    monkeypatch.setattr(db, "get_pool", lambda: _FakePool([PROJECT]))
    monkeypatch.setattr(db, "get_obs_pool", lambda: _FakePool())
    monkeypatch.setattr(snapshot, "BKDClient", _make_fake_bkd_class([issue]))
    monkeypatch.setattr(snapshot, "settings", _FakeSettings())
    monkeypatch.setattr(snapshot, "req_state", fake_req_state)
    monkeypatch.setattr(snapshot, "engine", fake_engine)

    await snapshot.sync_once()

    assert len(fake_req_state.insert_init_calls) == 0, (
        "SNAP-ORPHAN-S2: insert_init MUST NOT be called when req_state row exists; "
        f"got {fake_req_state.insert_init_calls}"
    )
    assert len(fake_engine.step_calls) == 0, (
        "SNAP-ORPHAN-S2: engine.step MUST NOT be called when req_state row exists; "
        f"got {fake_engine.step_calls}"
    )


# ─── S3: analyze tag already present → skip ──────────────────────────────────


async def test_snap_orphan_s3_skips_when_analyze_tag_present(monkeypatch):
    """SNAP-ORPHAN-S3: tags include 'analyze' → loop must not trigger engine.step.

    GIVEN  a BKD issue with tags=["intent:analyze", "analyze", "REQ-7"]
           (the entry action has already rebranded the issue) and
           req_state.get returns None
    WHEN   snapshot.sync_once() runs
    THEN   engine.step MUST NOT be called
    """
    PROJECT = "proj-c"
    issue = _make_issue(
        id="i-7",
        project_id=PROJECT,
        issue_number=7,
        tags=["intent:analyze", "analyze", "REQ-7"],
        status_id="working",
    )

    fake_req_state = _FakeReqState(get_side_effect=[None])
    fake_engine = _FakeEngine()

    monkeypatch.setattr(db, "get_pool", lambda: _FakePool([PROJECT]))
    monkeypatch.setattr(db, "get_obs_pool", lambda: _FakePool())
    monkeypatch.setattr(snapshot, "BKDClient", _make_fake_bkd_class([issue]))
    monkeypatch.setattr(snapshot, "settings", _FakeSettings())
    monkeypatch.setattr(snapshot, "req_state", fake_req_state)
    monkeypatch.setattr(snapshot, "engine", fake_engine)

    await snapshot.sync_once()

    assert len(fake_engine.step_calls) == 0, (
        "SNAP-ORPHAN-S3: engine.step MUST NOT be called when 'analyze' tag is present; "
        f"got {fake_engine.step_calls}"
    )


# ─── S4: issue in BKD status done → skip ─────────────────────────────────────


async def test_snap_orphan_s4_skips_when_status_done(monkeypatch):
    """SNAP-ORPHAN-S4: issue statusId='done' → loop must not trigger engine.step.

    GIVEN  a BKD issue with tags=["intent:analyze"] and statusId="done"
           (user explicitly closed it before the webhook could be processed)
    WHEN   snapshot.sync_once() runs
    THEN   engine.step MUST NOT be called
    """
    PROJECT = "proj-d"
    issue = _make_issue(
        id="i-99",
        project_id=PROJECT,
        issue_number=99,
        tags=["intent:analyze"],
        status_id="done",
    )

    fake_req_state = _FakeReqState(get_side_effect=[None])
    fake_engine = _FakeEngine()

    monkeypatch.setattr(db, "get_pool", lambda: _FakePool([PROJECT]))
    monkeypatch.setattr(db, "get_obs_pool", lambda: _FakePool())
    monkeypatch.setattr(snapshot, "BKDClient", _make_fake_bkd_class([issue]))
    monkeypatch.setattr(snapshot, "settings", _FakeSettings())
    monkeypatch.setattr(snapshot, "req_state", fake_req_state)
    monkeypatch.setattr(snapshot, "engine", fake_engine)

    await snapshot.sync_once()

    assert len(fake_engine.step_calls) == 0, (
        "SNAP-ORPHAN-S4: engine.step MUST NOT be called when issue statusId='done'; "
        f"got {fake_engine.step_calls}"
    )


# ─── S5: obs pool absent → orphan recovery still runs, sync_once returns 0 ───


async def test_snap_orphan_s5_recovery_runs_without_obs_pool(monkeypatch):
    """SNAP-ORPHAN-S5: obs_pool=None → orphan recovery still fires, sync_once returns 0.

    GIVEN  db.get_obs_pool() returns None (observability database not configured)
           but db.get_pool() returns a working main pool with at least one project_id row
    WHEN   snapshot.sync_once() runs and the project's BKD list contains exactly one
           orphan issue (as in SNAP-ORPHAN-S1)
    THEN   engine.step is still called once
    AND    sync_once() returns 0 (no bkd_snapshot UPSERTs because obs pool is absent)
    """
    PROJECT = "proj-e"
    issue = _make_issue(
        id="i-9",
        project_id=PROJECT,
        issue_number=9,
        tags=["intent:analyze"],
        status_id="working",
    )

    post_insert_row = _ReqRow(ReqState.INIT)
    fake_req_state = _FakeReqState(get_side_effect=[None, post_insert_row])
    fake_engine = _FakeEngine()

    monkeypatch.setattr(db, "get_pool", lambda: _FakePool([PROJECT]))
    monkeypatch.setattr(db, "get_obs_pool", lambda: None)  # obs DB absent
    monkeypatch.setattr(snapshot, "BKDClient", _make_fake_bkd_class([issue]))
    monkeypatch.setattr(snapshot, "settings", _FakeSettings())
    monkeypatch.setattr(snapshot, "req_state", fake_req_state)
    monkeypatch.setattr(snapshot, "engine", fake_engine)

    result = await snapshot.sync_once()

    assert len(fake_engine.step_calls) == 1, (
        "SNAP-ORPHAN-S5: engine.step MUST be called once even when obs_pool is None; "
        f"got {fake_engine.step_calls}"
    )
    assert result == 0, (
        f"SNAP-ORPHAN-S5: sync_once must return 0 (no obs UPSERTs) when obs_pool is None, "
        f"got {result!r}"
    )
