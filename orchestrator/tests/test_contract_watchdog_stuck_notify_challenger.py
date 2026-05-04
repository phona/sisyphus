"""Contract tests for REQ-feat-stuck-notify-378-v2-1777866642.

Black-box behavioral contracts derived **solely** from:
  openspec/changes/REQ-feat-stuck-notify-378-v2-1777866642/specs/
    watchdog-stuck-notify/spec.md

Scenarios covered (one test func per scenario):
  WSN-S1  first stale tick → exactly one obs.record_event + log.warning,
          body starts with '⏰ ', watermark persisted as UTC ISO-8601
  WSN-S2  second tick within same stale window (watermark >= updated_at)
          → 0 obs.record_event, 0 telegram POSTs, returns notified=0
  WSN-S3  telegram POST raises httpx.ConnectError → tick MUST NOT raise,
          obs.record_event still called, watermark still persisted,
          'watchdog.stuck_notify.telegram_failed' log emitted
  WSN-S4  settings.escalated_stale_notify_enabled = False → short-circuit,
          no pool.fetch, no obs.record_event, returns
          {'checked': 0, 'notified': 0}

Black-box stance: this file does not import or reflect on any internal
helper of watchdog beyond what the spec mandates by name
(`_notify_stale_escalated_tick`, `obs.record_event`, the two structlog
event names, the `⏰ ` body prefix, the three settings keys, the return
dict shape).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import structlog.testing

from orchestrator import watchdog


# ─── Fake pool ──────────────────────────────────────────────────────────────
@dataclass
class _FakePool:
    """Minimal asyncpg-like Pool double.

    `fetch` returns the canned `rows` (mutated by tests via attr).
    `execute` / `executemany` / `fetchrow` capture all calls so the test
    can assert that a watermark UPDATE was issued without locking the
    exact SQL string (black-box).
    """

    rows: list = field(default_factory=list)
    fetch_calls: list = field(default_factory=list)
    execute_calls: list = field(default_factory=list)
    fetchrow_calls: list = field(default_factory=list)

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return list(self.rows)

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return "UPDATE 1"

    async def executemany(self, sql, args_seq):
        for args in args_seq:
            self.execute_calls.append((sql, tuple(args)))
        return "UPDATE 1"

    async def fetchrow(self, sql, *args):
        self.fetchrow_calls.append((sql, args))
        return self.rows[0] if self.rows else None


# ─── Common patch helpers ───────────────────────────────────────────────────
def _patch_pool(monkeypatch, pool):
    """Stub the pool reachable from watchdog.

    Mirrors the existing test convention (test_watchdog_bkd_sync.py),
    which patches `orchestrator.watchdog.db.get_pool` — i.e. the `db`
    sub-module imported into the watchdog module namespace.
    """
    monkeypatch.setattr("orchestrator.watchdog.db.get_pool", lambda: pool)


def _patch_settings(monkeypatch, *, enabled=True, threshold_sec=1800,
                    telegram_url=""):
    """Patch the singleton settings attributes — works regardless of import path."""
    from orchestrator import config as orch_config

    s = orch_config.settings
    monkeypatch.setattr(s, "escalated_stale_notify_enabled", enabled, raising=False)
    monkeypatch.setattr(s, "escalated_stale_threshold_sec", threshold_sec, raising=False)
    monkeypatch.setattr(s, "escalated_stale_telegram_url", telegram_url, raising=False)


def _patch_obs_record_event(monkeypatch):
    """Replace obs.record_event so we can count and inspect calls.

    Patches every import style permitted by Python without peeking at the
    watchdog source:
      * `from orchestrator import observability [as obs]` → patched at source
      * `from orchestrator.observability import record_event` → patched as a
        local binding on the watchdog module (only added if it exists there)
    """
    calls: list[tuple[tuple, dict]] = []

    async def _fake(*args, **kwargs):
        calls.append((args, kwargs))

    from orchestrator import observability as orch_obs

    monkeypatch.setattr(orch_obs, "record_event", _fake, raising=False)
    if hasattr(watchdog, "record_event"):
        monkeypatch.setattr(watchdog, "record_event", _fake, raising=False)
    return calls


class _RaisingAsyncClient:
    """httpx.AsyncClient stand-in whose .post() raises httpx.ConnectError."""

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        raise httpx.ConnectError("simulated connect failure")


def _patch_httpx_to_raise(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", _RaisingAsyncClient)


# ─── Test row factory ───────────────────────────────────────────────────────
def _make_stale_row(req_id="REQ-stuck-1", stale_for_sec=2400, *,
                    context: dict | None = None):
    """Mimic an asyncpg Record-like dict for a stale escalated REQ.

    Includes both `updated_at` (the canonical column the spec references)
    and `stuck_sec` (a common pre-computed elapsed-seconds projection)
    so the test row tolerates either implementation choice for the
    notification body.
    """
    return {
        "req_id": req_id,
        "project_id": "proj-test",
        "state": "escalated",
        "updated_at": datetime.now(timezone.utc) - timedelta(seconds=stale_for_sec),
        "stuck_sec": stale_for_sec,
        "context": dict(context or {}),
    }


# ─── Helpers to assert across capture sources ───────────────────────────────
def _flatten_call_args(call) -> list:
    """Combine positional + keyword arg values into a single inspectable list."""
    args, kwargs = call
    flat = list(args)
    flat.extend(kwargs.values())
    return flat


def _stringify(value) -> str:
    """Best-effort string projection of any value for substring search."""
    if isinstance(value, dict):
        return " ".join(_stringify(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_stringify(v) for v in value)
    return str(value)


def _any_arg_contains(calls, needle: str) -> bool:
    for call in calls:
        for v in _flatten_call_args(call):
            if needle in _stringify(v):
                return True
    return False


_ISO8601_UTC_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+\-]00:?00)"
)


def _any_execute_arg_is_iso8601_utc(execute_calls) -> bool:
    """Return True if any captured execute() arg contains a UTC ISO-8601 datetime.

    Tolerates dev wrapping the value as JSON (e.g. ``'{"stuck_notified_at":
    "...+00:00"}'``) by scanning every recursively-extracted string.
    """
    for _sql, args in execute_calls:
        for a in args:
            for cand in _candidate_strings(a):
                if _contains_utc_iso8601(cand):
                    return True
    return False


def _candidate_strings(value):
    if isinstance(value, str):
        yield value
        # If the string is JSON, also recurse into it so wrappers like
        # `{"stuck_notified_at": "..."}` are inspected as structured data.
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return
        if isinstance(decoded, (dict, list, str)):
            yield from _candidate_strings(decoded)
        return
    if isinstance(value, dict):
        for v in value.values():
            yield from _candidate_strings(v)
        return
    if isinstance(value, (list, tuple)):
        for v in value:
            yield from _candidate_strings(v)


def _contains_utc_iso8601(s: str) -> bool:
    if not isinstance(s, str) or len(s) < 19:
        return False
    for match in _ISO8601_UTC_RE.finditer(s):
        candidate = match.group(0).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if dt.tzinfo is not None and dt.utcoffset() == timedelta(0):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════
# WSN-S1: first stale tick notifies once and persists watermark
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_wsn_s1_first_stale_tick_notifies_once_and_persists_watermark(monkeypatch):
    pool = _FakePool(rows=[_make_stale_row("REQ-stuck-1", stale_for_sec=2400)])
    _patch_pool(monkeypatch, pool)
    _patch_settings(monkeypatch, enabled=True, threshold_sec=1800, telegram_url="")
    obs_calls = _patch_obs_record_event(monkeypatch)

    with structlog.testing.capture_logs() as log_records:
        result = await watchdog._notify_stale_escalated_tick()

    # Return shape contract.
    assert isinstance(result, dict), \
        f"WSN-S1: tick MUST return a dict, got {type(result).__name__}"
    assert "checked" in result and "notified" in result, \
        f"WSN-S1: return dict MUST have 'checked' and 'notified' keys, got {result!r}"
    assert result["notified"] == 1, \
        f"WSN-S1: exactly one REQ should be notified, got {result!r}"

    # Exactly one obs.record_event call with kind=watchdog_stuck_notify and the req_id.
    assert len(obs_calls) == 1, (
        f"WSN-S1: exactly one obs.record_event call MUST be made, "
        f"got {len(obs_calls)} calls: {obs_calls!r}"
    )
    args, kwargs = obs_calls[0]
    flat = list(args) + list(kwargs.values())
    assert any("watchdog_stuck_notify" == _stringify(v) or
               "watchdog_stuck_notify" in _stringify(v) for v in flat), (
        f"WSN-S1: obs.record_event call MUST include kind='watchdog_stuck_notify', "
        f"got args={args!r} kwargs={kwargs!r}"
    )
    assert any("REQ-stuck-1" in _stringify(v) for v in flat), (
        f"WSN-S1: obs.record_event call MUST include the matching req_id, "
        f"got args={args!r} kwargs={kwargs!r}"
    )

    # log.warning event named 'watchdog.stuck_notify' (NOT the .telegram_failed variant).
    notify_logs = [r for r in log_records
                   if r.get("event") == "watchdog.stuck_notify"
                   and r.get("log_level") in ("warning", "warn")]
    assert notify_logs, (
        f"WSN-S1: a log.warning event named 'watchdog.stuck_notify' MUST be recorded; "
        f"captured events: {[r.get('event') for r in log_records]!r}"
    )

    # Body string MUST start with '⏰ ' and contain the REQ id — search every
    # captured surface (obs extras, log fields) since spec doesn't fix the carrier.
    surfaces: list[str] = []
    for r in log_records:
        for v in r.values():
            surfaces.append(_stringify(v))
    for call in obs_calls:
        for v in _flatten_call_args(call):
            surfaces.append(_stringify(v))
    assert any(s.startswith("⏰ ") and "REQ-stuck-1" in s for s in surfaces), (
        "WSN-S1: a body string starting with '⏰ ' and containing the REQ id MUST "
        f"appear in obs extras or log fields; surfaces: {surfaces!r}"
    )

    # Watermark persisted as UTC ISO-8601 — at least one execute() arg must be one.
    assert pool.execute_calls, (
        "WSN-S1: watchdog MUST persist context.stuck_notified_at via pool.execute(); "
        "no execute() calls captured"
    )
    assert _any_execute_arg_is_iso8601_utc(pool.execute_calls), (
        f"WSN-S1: persisted stuck_notified_at MUST be a UTC ISO-8601 string; "
        f"captured execute() args: {[c[1] for c in pool.execute_calls]!r}"
    )
    assert _any_arg_contains(pool.execute_calls, "REQ-stuck-1"), (
        "WSN-S1: persistence call MUST scope to the REQ id 'REQ-stuck-1'; "
        f"captured execute() args: {[c[1] for c in pool.execute_calls]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# WSN-S2: second tick within same stale window is suppressed
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_wsn_s2_second_tick_in_same_window_is_suppressed(monkeypatch):
    # Row already has a watermark >= updated_at (current ESCALATED window
    # already notified). Use a watermark strictly newer than updated_at.
    row = _make_stale_row("REQ-stuck-2", stale_for_sec=2400)
    row["context"]["stuck_notified_at"] = (
        row["updated_at"] + timedelta(seconds=10)
    ).isoformat()
    pool = _FakePool(rows=[row])
    _patch_pool(monkeypatch, pool)
    _patch_settings(
        monkeypatch, enabled=True, threshold_sec=1800,
        telegram_url="https://api.telegram.example/bot/test",
    )
    obs_calls = _patch_obs_record_event(monkeypatch)

    posted: list = []

    class _SpyClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            posted.append((a, kw))

            class _R:
                status_code = 200

                def raise_for_status(self_inner):
                    return None
            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _SpyClient)

    result = await watchdog._notify_stale_escalated_tick()

    assert obs_calls == [], (
        f"WSN-S2: zero obs.record_event calls MUST be made, "
        f"got {len(obs_calls)}: {obs_calls!r}"
    )
    assert posted == [], (
        f"WSN-S2: zero HTTP POSTs to the telegram URL MUST occur, got {posted!r}"
    )
    assert isinstance(result, dict) and "checked" in result and "notified" in result, (
        f"WSN-S2: return MUST be dict with 'checked'/'notified' keys, got {result!r}"
    )
    assert result["checked"] >= 1, (
        f"WSN-S2: 'checked' MUST be >= 1 (the row was scanned), got {result!r}"
    )
    assert result["notified"] == 0, (
        f"WSN-S2: 'notified' MUST be 0 (suppressed by watermark), got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# WSN-S3: telegram POST is best-effort and never raises
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_wsn_s3_telegram_post_failure_is_swallowed(monkeypatch):
    pool = _FakePool(rows=[_make_stale_row("REQ-stuck-3", stale_for_sec=2400)])
    _patch_pool(monkeypatch, pool)
    _patch_settings(
        monkeypatch, enabled=True, threshold_sec=1800,
        telegram_url="https://api.telegram.example/bot/test/sendMessage",
    )
    obs_calls = _patch_obs_record_event(monkeypatch)
    _patch_httpx_to_raise(monkeypatch)

    with structlog.testing.capture_logs() as log_records:
        # Tick MUST NOT raise — even though httpx POST raises ConnectError.
        result = await watchdog._notify_stale_escalated_tick()

    assert isinstance(result, dict), (
        f"WSN-S3: tick MUST still return a dict on telegram failure, got {result!r}"
    )

    # In-DB notification is the source of truth → obs.record_event still called once.
    assert len(obs_calls) == 1, (
        f"WSN-S3: obs.record_event MUST still be invoked exactly once "
        f"despite telegram failure, got {len(obs_calls)}: {obs_calls!r}"
    )

    # Watermark MUST still be persisted (telegram is opportunistic, no retry).
    assert pool.execute_calls, (
        "WSN-S3: watermark MUST still be persisted via pool.execute() "
        "despite telegram failure; no execute() calls captured"
    )
    assert _any_execute_arg_is_iso8601_utc(pool.execute_calls), (
        "WSN-S3: persisted stuck_notified_at MUST be a UTC ISO-8601 string "
        f"even on telegram failure; got: {[c[1] for c in pool.execute_calls]!r}"
    )

    # A 'watchdog.stuck_notify.telegram_failed' log.warning event MUST be recorded.
    failed_logs = [r for r in log_records
                   if r.get("event") == "watchdog.stuck_notify.telegram_failed"
                   and r.get("log_level") in ("warning", "warn")]
    assert failed_logs, (
        "WSN-S3: a log.warning event named 'watchdog.stuck_notify.telegram_failed' "
        f"MUST be recorded; captured: {[r.get('event') for r in log_records]!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# WSN-S4: disabled flag short-circuits the tick
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_wsn_s4_disabled_flag_short_circuits(monkeypatch):
    # Row would otherwise qualify (stale, no watermark).
    pool = _FakePool(rows=[_make_stale_row("REQ-stuck-4", stale_for_sec=99999)])
    _patch_pool(monkeypatch, pool)
    _patch_settings(monkeypatch, enabled=False, threshold_sec=1800, telegram_url="")
    obs_calls = _patch_obs_record_event(monkeypatch)

    result = await watchdog._notify_stale_escalated_tick()

    assert result == {"checked": 0, "notified": 0}, (
        f"WSN-S4: disabled tick MUST return exactly "
        f"{{'checked': 0, 'notified': 0}}, got {result!r}"
    )
    assert pool.fetch_calls == [], (
        f"WSN-S4: pool.fetch MUST NOT be called when disabled, "
        f"got {len(pool.fetch_calls)} calls"
    )
    assert obs_calls == [], (
        f"WSN-S4: obs.record_event MUST NOT be called when disabled, "
        f"got {obs_calls!r}"
    )
