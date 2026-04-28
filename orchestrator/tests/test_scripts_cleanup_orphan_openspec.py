"""Unit tests for cleanup_orphan_openspec_changes.py dry-run logic (Fix B).

REQ-openspec-changes-cleanup-1777343379

Scenarios:
  COP-S1  done state → action=delete
  COP-S2  escalated state → action=delete
  COP-S3  in-flight state → action=keep
  COP-S4  PG no record (ancient orphan) → action=delete
  COP-S5  _superseded and archive dirs are skipped (not in output)
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# Load the script module without running main()
_SCRIPT_PATH = (
    Path(__file__).parent.parent / "scripts" / "cleanup_orphan_openspec_changes.py"
)
_spec = importlib.util.spec_from_file_location("cleanup_script", _SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_classify = _mod._classify
_collect_req_dirs = _mod._collect_req_dirs
_TERMINAL_STATES = _mod._TERMINAL_STATES
_SKIP_DIRS = _mod._SKIP_DIRS


# ─── helpers ──────────────────────────────────────────────────────────────────


async def _fake_query_states(pg_url: str, req_ids: list[str]) -> dict[str, str]:
    """Simulate PG returning preset state values."""
    return _FAKE_STATES


_FAKE_STATES: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _patch_query(monkeypatch):
    monkeypatch.setattr(_mod, "_query_states", _fake_query_states)


# ─── COP-S1: done → delete ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s1_done_state_is_delete():
    """COP-S1: A REQ with state=done MUST appear in the delete list."""
    global _FAKE_STATES
    _FAKE_STATES = {"REQ-done-1234": "done"}

    statuses = await _classify(["REQ-done-1234"], pg_url="pg://fake")
    assert len(statuses) == 1
    assert statuses[0].action == "delete"
    assert statuses[0].state == "done"


# ─── COP-S2: escalated → delete ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s2_escalated_state_is_delete():
    """COP-S2: A REQ with state=escalated MUST appear in the delete list."""
    global _FAKE_STATES
    _FAKE_STATES = {"REQ-esc-9999": "escalated"}

    statuses = await _classify(["REQ-esc-9999"], pg_url="pg://fake")
    assert len(statuses) == 1
    assert statuses[0].action == "delete"
    assert statuses[0].state == "escalated"


# ─── COP-S3: in-flight → keep ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_s3_inflight_state_is_keep():
    """COP-S3: A REQ with an in-flight state (e.g. analyzing) MUST be kept."""
    global _FAKE_STATES
    _FAKE_STATES = {"REQ-live-5555": "analyzing"}

    statuses = await _classify(["REQ-live-5555"], pg_url="pg://fake")
    assert len(statuses) == 1
    assert statuses[0].action == "keep"


# ─── COP-S4: PG no record → delete ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_s4_no_pg_record_is_delete():
    """COP-S4: A REQ not found in PG (ancient orphan) MUST appear in delete list."""
    global _FAKE_STATES
    _FAKE_STATES = {}  # empty → not_found

    statuses = await _classify(["REQ-ancient-0000"], pg_url="pg://fake")
    assert len(statuses) == 1
    assert statuses[0].action == "delete"
    assert statuses[0].state == "not_found"


# ─── COP-S5: _superseded and archive are skipped ─────────────────────────────


def test_s5_skip_dirs_not_collected(tmp_path):
    """COP-S5: 'archive' and '_superseded' directories MUST be excluded from scan."""
    changes = tmp_path / "openspec" / "changes"
    changes.mkdir(parents=True)
    (changes / "REQ-real-1234").mkdir()
    (changes / "archive").mkdir()
    (changes / "_superseded").mkdir()

    result = _collect_req_dirs(tmp_path)
    assert "archive" not in result
    assert "_superseded" not in result
    assert "REQ-real-1234" in result


# ─── mixed batch ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mixed_batch():
    """COP-S6: Mixed batch: done+escalated+in-flight+not-found all classified correctly."""
    global _FAKE_STATES
    _FAKE_STATES = {
        "REQ-done-1": "done",
        "REQ-esc-2": "escalated",
        "REQ-live-3": "analyzing",
    }

    req_dirs = ["REQ-done-1", "REQ-esc-2", "REQ-live-3", "REQ-orphan-4"]
    statuses = await _classify(req_dirs, pg_url="pg://fake")

    by_name = {s.dir_name: s for s in statuses}
    assert by_name["REQ-done-1"].action == "delete"
    assert by_name["REQ-esc-2"].action == "delete"
    assert by_name["REQ-live-3"].action == "keep"
    assert by_name["REQ-orphan-4"].action == "delete"
    assert by_name["REQ-orphan-4"].state == "not_found"
