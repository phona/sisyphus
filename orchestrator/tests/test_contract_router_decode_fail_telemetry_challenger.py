"""Contract tests for router decode-fail telemetry (RDFT, REQ-feat-router-telemetry-v3-1777866642).

Black-box behavioral contracts derived from:
  openspec/changes/REQ-feat-router-telemetry-v3-1777866642/specs/router-decode-fail-telemetry/spec.md

Scenarios covered:
  RDFT-S1  webhook decode-fail path INSERTs a stage_runs row with
           stage='router_decode_fail' / outcome='silent_drop' / fail_reason=router-reason
           and context JSON containing issue_id, raw_tags, verifier_stage.
  RDFT-S2a BKD verifier issue gets a `router-decode-fail` tag (additive, no dupes,
           previous tags preserved) AND a description containing the literal
           'router decode 失败' plus the router reason verbatim.
  RDFT-S2b When BKDClient.update_issue raises, _emit_decode_fail_telemetry returns
           normally and emits a WARNING with key 'router.decode_fail.bkd_patch_failed'.
  RDFT-S3  _emit_decode_fail_telemetry emits exactly one structlog warning with
           event='router.decode_fail' binding issue_id, req_id, stage, reason, raw_tags;
           the warning fires BEFORE downstream emits so a hard exception in those
           paths still leaves the WARNING in the stream.
"""
from __future__ import annotations

import json
from typing import Any


# ─── Shared fakes ────────────────────────────────────────────────────────────


class _FakePool:
    """Capture both fetchrow and execute calls so we can inspect the stage_runs INSERT."""

    def __init__(self):
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))

        class _Row(dict):
            pass

        return _Row(id=1)

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))


def _find_stage_runs_insert(
    calls: list[tuple[str, tuple]],
) -> tuple[str, tuple] | None:
    for sql, args in calls:
        if "stage_runs" in sql.lower() and "insert" in sql.lower():
            return sql, args
    return None


def _make_bkd_factory(
    *,
    prev_tags: list[str],
    description: str = "",
    capture_update: list | None = None,
    update_raises: BaseException | None = None,
):
    """Return a fake BKDClient class compatible with `async with BKDClient(...) as bkd:`."""

    class _BKD:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get_issue(self, *a, **kw):
            class _Issue:
                pass

            _Issue.tags = list(prev_tags)
            _Issue.description = description
            return _Issue()

        async def update_issue(self, *a, **kw):
            if capture_update is not None:
                capture_update.append({"args": a, "kwargs": kw})
            if update_raises is not None:
                raise update_raises

    return _BKD


# ─── RDFT-S1: stage_runs row written when no decision JSON is found ──────────


async def test_rdft_s1_stage_runs_row_inserted_on_decode_fail(monkeypatch):
    """RDFT-S1: terminal verifier decode-fail must INSERT exactly one stage_runs
    row with stage='router_decode_fail', outcome='silent_drop', fail_reason
    matching the router reason, and context JSON containing issue_id, raw_tags,
    verifier_stage.

    Driven through the public helper webhook._emit_decode_fail_telemetry, which
    the spec names as the seam used by the webhook session.completed handler.
    """
    from orchestrator import webhook

    pool = _FakePool()
    raw_tags = ["verifier", "verify:staging_test", "REQ-rdft-s1"]
    reason = "no decision JSON found in tag or description"

    monkeypatch.setattr(
        webhook,
        "BKDClient",
        _make_bkd_factory(prev_tags=raw_tags, capture_update=[]),
    )

    await webhook._emit_decode_fail_telemetry(
        pool=pool,
        project_id="proj-rdft",
        issue_id="issue-rdft-s1",
        req_id="REQ-rdft-s1",
        verifier_stage="staging_test",
        reason=reason,
        raw_tags=raw_tags,
    )

    insert = _find_stage_runs_insert(pool.fetchrow_calls) or _find_stage_runs_insert(
        pool.execute_calls
    )
    assert insert is not None, (
        "RDFT-S1: webhook decode-fail path must INSERT INTO stage_runs; "
        f"saw fetchrow={pool.fetchrow_calls!r} execute={pool.execute_calls!r}"
    )
    sql, args = insert
    flat = list(args)

    # serialize once for cheap substring checks. Some columns (stage/outcome) may be
    # baked as SQL literals rather than bound params, so include the SQL in the blob.
    blob = sql + " " + " ".join(repr(a) for a in flat)
    assert "router_decode_fail" in blob, (
        f"RDFT-S1: stage column must be 'router_decode_fail'; sql={sql!r} args={flat!r}"
    )
    assert "silent_drop" in blob, (
        f"RDFT-S1: outcome column must be 'silent_drop'; sql={sql!r} args={flat!r}"
    )
    assert reason in blob, (
        f"RDFT-S1: fail_reason must match router reason {reason!r}; sql={sql!r} args={flat!r}"
    )

    # Find the context dict (asyncpg may pass dict or JSON-encoded string)
    ctx: dict | None = None
    for a in flat:
        if isinstance(a, dict) and "issue_id" in a:
            ctx = a
            break
        if isinstance(a, str) and a.lstrip().startswith("{"):
            try:
                d = json.loads(a)
            except Exception:
                continue
            if isinstance(d, dict) and "issue_id" in d:
                ctx = d
                break
    assert ctx is not None, (
        f"RDFT-S1: stage_runs row must include a context JSON with issue_id; args={flat!r}"
    )
    assert ctx.get("issue_id") == "issue-rdft-s1", (
        f"RDFT-S1: context.issue_id must be 'issue-rdft-s1'; got {ctx!r}"
    )
    assert ctx.get("verifier_stage") == "staging_test", (
        f"RDFT-S1: context.verifier_stage must be 'staging_test'; got {ctx!r}"
    )
    raw = ctx.get("raw_tags")
    assert isinstance(raw, list) and "verifier" in raw, (
        f"RDFT-S1: context.raw_tags must be a list including 'verifier'; got {ctx!r}"
    )


# ─── RDFT-S2a: BKD update_issue called with tag + warning text ───────────────


async def test_rdft_s2a_bkd_update_issue_with_tag_and_warning(monkeypatch):
    """RDFT-S2: _emit_decode_fail_telemetry MUST PATCH the verifier issue with
    both (a) tags = previous_tags + ['router-decode-fail'] (additive, no dupes,
    previous tags preserved) and (b) description containing the literal
    'router decode 失败' plus the router reason verbatim.
    """
    from orchestrator import webhook

    prev_tags = ["verifier", "verify:staging_test", "REQ-rdft-s2", "result:pass"]
    captured: list = []
    reason = "invalid decision: invalid action: 'pass'"

    monkeypatch.setattr(
        webhook,
        "BKDClient",
        _make_bkd_factory(
            prev_tags=prev_tags,
            description="prior body",
            capture_update=captured,
        ),
    )

    await webhook._emit_decode_fail_telemetry(
        pool=_FakePool(),
        project_id="proj-rdft",
        issue_id="issue-rdft-s2",
        req_id="REQ-rdft-s2",
        verifier_stage="staging_test",
        reason=reason,
        raw_tags=prev_tags,
    )

    assert len(captured) == 1, (
        f"RDFT-S2: BKD update_issue must be called exactly once for the decode-fail "
        f"issue; got {len(captured)} call(s): {captured!r}"
    )
    call = captured[0]
    kw = call["kwargs"]

    new_tags = kw.get("tags")
    assert new_tags is not None, (
        f"RDFT-S2: update_issue must pass tags=; got kwargs={kw!r}"
    )
    for t in prev_tags:
        assert t in new_tags, (
            f"RDFT-S2: previous tag {t!r} must be preserved in additive PATCH; "
            f"got {new_tags!r}"
        )
    assert "router-decode-fail" in new_tags, (
        f"RDFT-S2: new tags must include 'router-decode-fail'; got {new_tags!r}"
    )
    assert len(new_tags) == len(set(new_tags)), (
        f"RDFT-S2: tags must be deduplicated; got {new_tags!r}"
    )

    desc = kw.get("description")
    assert isinstance(desc, str), (
        f"RDFT-S2: update_issue must pass description=<str>; got {desc!r}"
    )
    assert "router decode 失败" in desc, (
        f"RDFT-S2: description must contain literal 'router decode 失败'; got {desc!r}"
    )
    assert reason in desc, (
        f"RDFT-S2: description must contain router reason verbatim {reason!r}; "
        f"got {desc!r}"
    )


# ─── RDFT-S2b: BKD failure is isolated, WARNING surfaced ─────────────────────


async def test_rdft_s2b_bkd_failure_isolated(monkeypatch):
    """RDFT-S2 (failure-isolation half): when BKDClient.update_issue raises,
    _emit_decode_fail_telemetry MUST still return normally and emit a structlog
    WARNING with key 'router.decode_fail.bkd_patch_failed'.
    """
    from orchestrator import webhook

    captured: list[dict[str, Any]] = []

    class _StubLogger:
        def warning(self, event, **kw):
            captured.append({"level": "warning", "event": event, **kw})

        def info(self, *a, **kw):
            pass

        def error(self, *a, **kw):
            pass

        def debug(self, *a, **kw):
            pass

        def bind(self, **kw):
            return self

    monkeypatch.setattr(webhook, "log", _StubLogger())
    monkeypatch.setattr(
        webhook,
        "BKDClient",
        _make_bkd_factory(
            prev_tags=["verifier"],
            update_raises=RuntimeError("bkd-down"),
        ),
    )

    # MUST NOT raise — the surrounding webhook flow must keep going.
    await webhook._emit_decode_fail_telemetry(
        pool=_FakePool(),
        project_id="proj-rdft",
        issue_id="issue-rdft-s2b",
        req_id="REQ-rdft-s2b",
        verifier_stage="staging_test",
        reason="any reason",
        raw_tags=["verifier"],
    )

    found = [c for c in captured if c.get("event") == "router.decode_fail.bkd_patch_failed"]
    assert len(found) >= 1, (
        "RDFT-S2: BKD update_issue raise must NOT propagate; function must log a "
        "WARNING with event='router.decode_fail.bkd_patch_failed'. "
        f"Captured warnings: {captured!r}"
    )


# ─── RDFT-S3: structlog WARNING with structured fields, fired first ──────────


async def test_rdft_s3_warning_log_with_structured_fields(monkeypatch):
    """RDFT-S3: _emit_decode_fail_telemetry MUST emit exactly one structlog
    warning with event='router.decode_fail' binding issue_id, req_id, stage,
    reason, raw_tags. The warning MUST fire BEFORE any best-effort downstream
    emit so a hard exception in those paths still leaves the line in the stream.
    """
    import orchestrator.store.stage_runs as sr_mod
    from orchestrator import webhook

    captured: list[dict[str, Any]] = []

    class _StubLogger:
        def warning(self, event, **kw):
            captured.append({"event": event, **kw})

        def info(self, *a, **kw):
            pass

        def error(self, *a, **kw):
            pass

        def debug(self, *a, **kw):
            pass

        def bind(self, **kw):
            return self

    assert hasattr(webhook, "log"), (
        "RDFT-S3: webhook module must expose a `log` (structlog) attribute for telemetry"
    )
    monkeypatch.setattr(webhook, "log", _StubLogger())

    # Force EVERY downstream emit to blow up — log MUST still be captured because
    # it has to fire first.
    async def _boom(*a, **kw):
        raise RuntimeError("downstream-explosion")

    monkeypatch.setattr(sr_mod, "insert_decode_fail", _boom)
    if hasattr(webhook, "insert_decode_fail"):
        monkeypatch.setattr(webhook, "insert_decode_fail", _boom)
    monkeypatch.setattr(
        webhook,
        "BKDClient",
        _make_bkd_factory(
            prev_tags=["verifier"],
            update_raises=RuntimeError("downstream-explosion"),
        ),
    )

    raw_tags = ["verifier", "verify:staging_test", "REQ-rdft-s3"]
    reason = "no decision JSON found in tag or description"

    # MUST NOT raise even with everything below blowing up.
    await webhook._emit_decode_fail_telemetry(
        pool=_FakePool(),
        project_id="proj-rdft",
        issue_id="issue-rdft-s3",
        req_id="REQ-rdft-s3",
        verifier_stage="staging_test",
        reason=reason,
        raw_tags=raw_tags,
    )

    matches = [c for c in captured if c.get("event") == "router.decode_fail"]
    assert len(matches) == 1, (
        f"RDFT-S3: must emit exactly one log.warning with event='router.decode_fail'; "
        f"got {captured!r}"
    )
    rec = matches[0]
    for key, want in [
        ("issue_id", "issue-rdft-s3"),
        ("req_id", "REQ-rdft-s3"),
        ("stage", "staging_test"),
        ("reason", reason),
    ]:
        assert key in rec, f"RDFT-S3: log must bind {key!r}; got {rec!r}"
        assert rec[key] == want, f"RDFT-S3: log {key}={rec[key]!r}, want {want!r}"
    assert "raw_tags" in rec and list(rec["raw_tags"]) == list(raw_tags), (
        f"RDFT-S3: log must bind raw_tags={raw_tags!r}; got {rec.get('raw_tags')!r}"
    )
