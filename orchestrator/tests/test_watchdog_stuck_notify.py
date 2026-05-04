"""REQ-feat-stuck-notify-378-v2-1777866642 unit tests for
`watchdog._notify_stale_escalated_tick`.

Covers spec scenarios WSN-S1..WSN-S4 + the format helper.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from orchestrator import watchdog


# ─── Fake pool: records fetch SQL + execute calls ─────────────────────────
@dataclass
class FakePool:
    rows: list = field(default_factory=list)
    fetch_calls: list = field(default_factory=list)
    execute_calls: list = field(default_factory=list)

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, args))
        return self.rows

    async def execute(self, sql, *args):
        self.execute_calls.append((sql, args))
        return None


def _esc_row(req_id="REQ-1", project_id="proj-1", stuck_sec=2400, ctx=None,
             updated_offset_sec=2400):
    """Mimic the asyncpg row shape returned by the SELECT in
    `_notify_stale_escalated_tick` (req_id / project_id / context / updated_at /
    stuck_sec)."""
    return {
        "req_id": req_id,
        "project_id": project_id,
        "context": json.dumps(ctx or {}),
        "updated_at": datetime.now(UTC) - timedelta(seconds=updated_offset_sec),
        "stuck_sec": stuck_sec,
    }


def _patch_pool(monkeypatch, pool):
    monkeypatch.setattr("orchestrator.watchdog.db.get_pool", lambda: pool)


def _patch_obs(monkeypatch):
    calls: list = []

    async def fake_record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("orchestrator.watchdog.observability.record_event", fake_record)
    return calls


def _patch_telegram(monkeypatch, *, raise_exc: Exception | None = None):
    posts: list = []

    async def fake_post(url, text):
        posts.append((url, text))
        if raise_exc:
            raise raise_exc
        return True

    monkeypatch.setattr("orchestrator.watchdog._post_telegram_notify", fake_post)
    return posts


# ─── format helper (prefix contract) ──────────────────────────────────────
def test_format_stuck_notify_text_starts_with_clock_emoji():
    text = watchdog._format_stuck_notify_text("REQ-foo", 1900, "ESCALATED")
    assert text.startswith("⏰ "), "dashboards rely on this prefix to string-match"
    assert "REQ-foo" in text
    assert "31min" in text  # 1900 // 60


# ─── WSN-S1: first stale tick notifies once and persists watermark ────────
@pytest.mark.asyncio
async def test_wsn_s1_first_stale_tick_notifies_and_persists(monkeypatch):
    monkeypatch.setattr(watchdog.settings, "escalated_stale_notify_enabled", True)
    monkeypatch.setattr(watchdog.settings, "escalated_stale_threshold_sec", 1800)
    monkeypatch.setattr(watchdog.settings, "escalated_stale_telegram_url", "")

    pool = FakePool(rows=[_esc_row(req_id="REQ-1", stuck_sec=2400, ctx={})])
    _patch_pool(monkeypatch, pool)
    obs_calls = _patch_obs(monkeypatch)

    result = await watchdog._notify_stale_escalated_tick()

    assert result == {"checked": 1, "notified": 1}
    assert len(obs_calls) == 1
    assert obs_calls[0]["kind"] == "watchdog_stuck_notify"
    assert obs_calls[0]["req_id"] == "REQ-1"
    assert obs_calls[0]["error_msg"].startswith("⏰ ")
    assert "REQ-1" in obs_calls[0]["error_msg"]

    # watermark persisted via update_context (UPDATE req_state ... context = context || $2::jsonb)
    update_calls = [c for c in pool.execute_calls if "UPDATE req_state" in c[0]]
    assert len(update_calls) == 1
    _sql, args = update_calls[0]
    assert args[0] == "REQ-1"
    persisted = json.loads(args[1])
    assert "stuck_notified_at" in persisted
    # round-trip: must parse back as ISO datetime
    datetime.fromisoformat(persisted["stuck_notified_at"])


# ─── WSN-S2: second tick within same stale window is suppressed ───────────
@pytest.mark.asyncio
async def test_wsn_s2_existing_watermark_suppresses_second_notify(monkeypatch):
    monkeypatch.setattr(watchdog.settings, "escalated_stale_notify_enabled", True)
    monkeypatch.setattr(watchdog.settings, "escalated_stale_threshold_sec", 1800)
    monkeypatch.setattr(watchdog.settings, "escalated_stale_telegram_url",
                        "https://hook.example.test/")

    # watermark set 1min ago, updated_at 40min ago → watermark > updated_at → skip
    watermark = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
    pool = FakePool(rows=[_esc_row(
        req_id="REQ-1", stuck_sec=2400,
        updated_offset_sec=2400,
        ctx={"stuck_notified_at": watermark},
    )])
    _patch_pool(monkeypatch, pool)
    obs_calls = _patch_obs(monkeypatch)
    posts = _patch_telegram(monkeypatch)

    result = await watchdog._notify_stale_escalated_tick()

    assert result == {"checked": 1, "notified": 0}
    assert obs_calls == []
    assert posts == []
    # no UPDATE req_state on suppressed row
    assert [c for c in pool.execute_calls if "UPDATE req_state" in c[0]] == []


# ─── WSN-S3: telegram POST is best-effort and never raises ────────────────
@pytest.mark.asyncio
async def test_wsn_s3_telegram_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(watchdog.settings, "escalated_stale_notify_enabled", True)
    monkeypatch.setattr(watchdog.settings, "escalated_stale_threshold_sec", 1800)
    monkeypatch.setattr(watchdog.settings, "escalated_stale_telegram_url",
                        "https://hook.example.test/")

    pool = FakePool(rows=[_esc_row(req_id="REQ-2", stuck_sec=2400, ctx={})])
    _patch_pool(monkeypatch, pool)
    obs_calls = _patch_obs(monkeypatch)

    # _post_telegram_notify itself swallows; route the underlying httpx call to raise
    # so we exercise the real best-effort path.
    async def boom(*a, **kw):
        raise httpx.ConnectError("DNS down")

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, *a, **kw):
            await boom()

    monkeypatch.setattr("orchestrator.watchdog.httpx.AsyncClient",
                        lambda *a, **kw: FakeClient())

    result = await watchdog._notify_stale_escalated_tick()

    assert result == {"checked": 1, "notified": 1}
    assert len(obs_calls) == 1
    assert obs_calls[0]["req_id"] == "REQ-2"
    # watermark still persisted (telegram failure must not block retry-suppression)
    update_calls = [c for c in pool.execute_calls if "UPDATE req_state" in c[0]]
    assert len(update_calls) == 1


# ─── WSN-S4: disabled flag short-circuits the tick ────────────────────────
@pytest.mark.asyncio
async def test_wsn_s4_disabled_flag_short_circuits(monkeypatch):
    monkeypatch.setattr(watchdog.settings, "escalated_stale_notify_enabled", False)

    pool = FakePool(rows=[_esc_row(req_id="REQ-X", ctx={})])
    _patch_pool(monkeypatch, pool)
    obs_calls = _patch_obs(monkeypatch)

    result = await watchdog._notify_stale_escalated_tick()

    assert result == {"checked": 0, "notified": 0}
    # pool.fetch must not be called when disabled
    assert pool.fetch_calls == []
    assert obs_calls == []


# ─── extra: stale watermark (older than updated_at) still notifies ────────
@pytest.mark.asyncio
async def test_stale_watermark_below_updated_at_notifies_again(monkeypatch):
    """Re-escalate: REQ left ESCALATED then re-entered → updated_at jumped → old
    watermark < updated_at → tick must notify again."""
    monkeypatch.setattr(watchdog.settings, "escalated_stale_notify_enabled", True)
    monkeypatch.setattr(watchdog.settings, "escalated_stale_threshold_sec", 1800)
    monkeypatch.setattr(watchdog.settings, "escalated_stale_telegram_url", "")

    # watermark from a previous ESCALATED period (older than current updated_at)
    old_watermark = (datetime.now(UTC) - timedelta(seconds=10000)).isoformat()
    pool = FakePool(rows=[_esc_row(
        req_id="REQ-3", stuck_sec=2400, updated_offset_sec=2400,
        ctx={"stuck_notified_at": old_watermark},
    )])
    _patch_pool(monkeypatch, pool)
    obs_calls = _patch_obs(monkeypatch)

    result = await watchdog._notify_stale_escalated_tick()

    assert result == {"checked": 1, "notified": 1}
    assert len(obs_calls) == 1
