"""Unit tests for observability/setup_metabase.py.

Scenarios:
  MBS-S1  load_sql returns file content for every Q1-Q18 SQL file
  MBS-S2  MetabaseClient.login stores session token from response
  MBS-S3  get_or_create_card creates when not found, skips when found
  MBS-S4  get_or_create_card updates when force=True and card exists
  MBS-S5  get_or_create_dashboard creates with layout cards
  MBS-S6  get_or_create_dashboard skips existing unless force
  MBS-S7  provision returns correct created/skipped counts
  MBS-S8  provision dry_run makes zero HTTP calls
  MBS-S9  QUESTIONS covers exactly Q1-Q18 with 18 entries
  MBS-S10 DASHBOARDS covers 3 dashboards with correct keys
  MBS-S11 every question's SQL filename exists on disk
  MBS-S12 cache_ttl values match spec (30s/120s/1800s groups)
  MBS-S13 main() returns 1 when required args missing
  MBS-S14 find_database_id matches by host+dbname, returns None on miss
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

# Import setup_metabase from observability/ directory (not an installed package)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_OBS_DIR = _REPO_ROOT / "observability"
if str(_OBS_DIR) not in sys.path:
    sys.path.insert(0, str(_OBS_DIR))

import setup_metabase as sm  # noqa: E402, I001


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _stub_http(calls: list[tuple], responses: dict[tuple[str, str], dict]):
    """Return an http_call function that records calls and returns canned responses."""
    def _call(method: str, url: str, body, token) -> dict:
        path = urlparse(url).path  # e.g. /api/session
        calls.append((method, path, body))
        for (m, p), resp in responses.items():
            if m == method and (p == path or path.startswith(p)):
                return resp
        return {}
    return _call


def _client(responses: dict | None = None, calls: list | None = None) -> sm.MetabaseClient:
    _calls = calls if calls is not None else []
    _responses = responses or {}
    return sm.MetabaseClient("http://mb.test", http_call=_stub_http(_calls, _responses))


# ── MBS-S1: SQL files readable ─────────────────────────────────────────────────

@pytest.mark.parametrize("q", sm.QUESTIONS)
def test_MBS_S1_load_sql_returns_content_for_all_questions(q: sm.QuestionSpec):
    """load_sql returns non-empty string for every Q1-Q18 SQL file."""
    content = sm.load_sql(q.filename)
    assert isinstance(content, str)
    assert len(content) > 50, f"{q.filename} content seems too short: {len(content)} chars"


# ── MBS-S2: login stores token ─────────────────────────────────────────────────

def test_MBS_S2_login_stores_session_token():
    """MetabaseClient.login stores the session id as token."""
    calls: list = []
    client = _client(
        responses={("POST", "/api/session"): {"id": "tok-abc"}},
        calls=calls,
    )
    client.login("user@test", "secret")
    assert client._token == "tok-abc"
    assert calls[0] == ("POST", "/api/session", {"username": "user@test", "password": "secret"})
    # token is forwarded on subsequent calls
    assert client._token == "tok-abc"


# ── MBS-S3: get_or_create_card idempotency ─────────────────────────────────────

def test_MBS_S3_get_or_create_card_creates_when_not_found():
    calls: list = []
    client = _client(
        responses={
            ("GET", "/api/card"): [{"id": 99, "name": "Other card"}],
            ("POST", "/api/card"): {"id": 42},
        },
        calls=calls,
    )
    card_id, created = client.get_or_create_card("Q1 Stuck checks (24h)", "SELECT 1", 7, "table", 30)
    assert card_id == 42
    assert created is True
    post_calls = [c for c in calls if c[0] == "POST" and c[1] == "/api/card"]
    assert len(post_calls) == 1
    assert post_calls[0][2]["name"] == "Q1 Stuck checks (24h)"
    assert post_calls[0][2]["display"] == "table"
    assert post_calls[0][2]["cache_ttl"] == 30


def test_MBS_S3_get_or_create_card_skips_when_found():
    calls: list = []
    client = _client(
        responses={
            ("GET", "/api/card"): [{"id": 55, "name": "Q1 Stuck checks (24h)"}],
        },
        calls=calls,
    )
    card_id, created = client.get_or_create_card("Q1 Stuck checks (24h)", "SELECT 1", 7, "table", 30)
    assert card_id == 55
    assert created is False
    post_calls = [c for c in calls if c[0] == "POST"]
    assert post_calls == [], "Should not POST when card already exists"


# ── MBS-S4: force updates existing card ────────────────────────────────────────

def test_MBS_S4_force_updates_existing_card():
    calls: list = []
    client = _client(
        responses={
            ("GET", "/api/card"): [{"id": 55, "name": "Q1 Stuck checks (24h)"}],
            ("PUT", "/api/card/55"): {},
        },
        calls=calls,
    )
    card_id, created = client.get_or_create_card(
        "Q1 Stuck checks (24h)", "SELECT 2", 7, "table", 30, force=True
    )
    assert card_id == 55
    assert created is False
    put_calls = [c for c in calls if c[0] == "PUT"]
    assert len(put_calls) == 1
    assert "/api/card/55" in put_calls[0][1]


# ── MBS-S5: dashboard created with layout ──────────────────────────────────────

def test_MBS_S5_get_or_create_dashboard_creates_with_cards():
    calls: list = []
    client = _client(
        responses={
            ("GET", "/api/dashboard"): [],
            ("POST", "/api/dashboard"): {"id": 10},
            ("PUT", "/api/dashboard/10"): {},
        },
        calls=calls,
    )
    card_id_map = {5: 100, 1: 101, 3: 102}
    layout = [(5, 0, 0, 9, 6), (1, 0, 9, 9, 6), (3, 6, 0, 9, 6)]
    dash_id, created = client.get_or_create_dashboard("Test Dash", card_id_map, layout)
    assert dash_id == 10
    assert created is True
    put_calls = [c for c in calls if c[0] == "PUT"]
    assert len(put_calls) == 1
    cards = put_calls[0][2]["cards"]
    assert len(cards) == 3
    card_ids_sent = {c["card_id"] for c in cards}
    assert card_ids_sent == {100, 101, 102}


# ── MBS-S6: skip existing dashboard unless force ───────────────────────────────

def test_MBS_S6_skip_existing_dashboard():
    calls: list = []
    client = _client(
        responses={
            ("GET", "/api/dashboard"): [{"id": 77, "name": "Sisyphus M7 — Checker Health"}],
        },
        calls=calls,
    )
    dash_id, created = client.get_or_create_dashboard(
        "Sisyphus M7 — Checker Health", {}, []
    )
    assert dash_id == 77
    assert created is False
    post_calls = [c for c in calls if c[0] == "POST"]
    assert post_calls == []


# ── MBS-S7: provision counts ───────────────────────────────────────────────────

def test_MBS_S7_provision_counts_created_and_skipped(tmp_path: Path):
    """provision returns correct created/skipped counts from mixed state."""
    # Prepare minimal SQL files
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    for q in sm.QUESTIONS:
        (sql_dir / q.filename).write_text(f"SELECT 1 -- {q.name}", encoding="utf-8")

    # Q1 (id=1) exists, others don't
    existing_cards = [{"id": 1, "name": sm.QUESTIONS[0].name}]

    call_counter: dict[str, int] = {"post_card": 0}

    def _http(method, url, body, token):
        path = urlparse(url).path
        if method == "GET" and path == "/api/database":
            return {"data": [{"id": 7, "details": {"host": "pg", "dbname": "sisyphus"}}]}
        if method == "GET" and path == "/api/card":
            return existing_cards
        if method == "POST" and path == "/api/card":
            call_counter["post_card"] += 1
            new_id = 100 + call_counter["post_card"]
            existing_cards.append({"id": new_id, "name": body["name"]})
            return {"id": new_id}
        if method == "GET" and path == "/api/dashboard":
            return []
        if method == "POST" and path == "/api/dashboard":
            return {"id": 200}
        if method == "PUT" and "/api/dashboard/" in path:
            return {}
        return {}

    client = sm.MetabaseClient("http://mb.test", http_call=_http)
    result = sm.provision(
        client,
        db_host="pg", db_port=5432, db_name="sisyphus", db_user="sisyphus", db_pass="pw",
        sql_dir=sql_dir,
        log=lambda _: None,
    )

    assert result.questions_created == len(sm.QUESTIONS) - 1
    assert result.questions_skipped == 1
    assert result.dashboards_created == len(sm.DASHBOARDS)
    assert result.dashboards_skipped == 0


# ── MBS-S8: dry_run makes no HTTP calls ────────────────────────────────────────

def test_MBS_S8_dry_run_makes_no_http_calls(tmp_path: Path):
    calls: list = []
    client = sm.MetabaseClient("http://mb.test", http_call=_stub_http(calls, {}))
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    for q in sm.QUESTIONS:
        (sql_dir / q.filename).write_text("SELECT 1", encoding="utf-8")

    sm.provision(
        client,
        db_host="pg", db_port=5432, db_name="sisyphus", db_user="s", db_pass="p",
        dry_run=True, sql_dir=sql_dir,
        log=lambda _: None,
    )
    assert calls == [], "dry_run MUST make zero HTTP calls"


# ── MBS-S9: QUESTIONS has exactly 18 entries ───────────────────────────────────

def test_MBS_S9_questions_has_18_entries():
    assert len(sm.QUESTIONS) == 18, f"Expected 18 questions, got {len(sm.QUESTIONS)}"
    numbers = [q.number for q in sm.QUESTIONS]
    assert numbers == list(range(1, 19)), "Question numbers must be 1..18 in order"


# ── MBS-S10: DASHBOARDS has 3 entries ─────────────────────────────────────────

def test_MBS_S10_dashboards_has_3_entries():
    assert len(sm.DASHBOARDS) == 3
    keys = {d.key for d in sm.DASHBOARDS}
    assert keys == {"m7", "m14e", "fixer"}


# ── MBS-S11: every SQL file exists on disk ─────────────────────────────────────

@pytest.mark.parametrize("q", sm.QUESTIONS)
def test_MBS_S11_sql_file_exists_on_disk(q: sm.QuestionSpec):
    path = _OBS_DIR / "queries" / "sisyphus" / q.filename
    assert path.exists(), f"SQL file missing: {path}"
    assert path.stat().st_size > 0, f"SQL file is empty: {path}"


# ── MBS-S12: cache_ttl values are within spec ─────────────────────────────────

def test_MBS_S12_cache_ttl_groups_match_spec():
    """Verify Q1/Q5 → 30s, Q2/3/4/12/13/17/18 → 120s, rest → 1800s."""
    spec_30s = {1, 5}
    spec_120s = {2, 3, 4, 12, 13, 17, 18}
    spec_1800s = {6, 7, 8, 9, 10, 11, 14, 15, 16}

    for q in sm.QUESTIONS:
        if q.number in spec_30s:
            assert q.cache_ttl == 30, f"Q{q.number} cache_ttl should be 30s"
        elif q.number in spec_120s:
            assert q.cache_ttl == 120, f"Q{q.number} cache_ttl should be 120s"
        elif q.number in spec_1800s:
            assert q.cache_ttl == 1800, f"Q{q.number} cache_ttl should be 1800s"


# ── MBS-S13: main returns 1 on missing args ────────────────────────────────────

def test_MBS_S13_main_returns_1_when_required_args_missing():
    rc = sm.main(["--url", "http://mb.test"])  # missing user/pass/db-host/db-pass
    assert rc == 1


# ── MBS-S14: find_database_id ─────────────────────────────────────────────────

def test_MBS_S14_find_database_id_matches_host_and_dbname():
    calls: list = []
    client = _client(
        responses={
            ("GET", "/api/database"): {"data": [
                {"id": 3, "details": {"host": "pg-host", "dbname": "sisyphus"}},
                {"id": 5, "details": {"host": "other", "dbname": "other"}},
            ]},
        },
        calls=calls,
    )
    assert client.find_database_id("pg-host", "sisyphus") == 3
    assert client.find_database_id("pg-host", "other") is None
    assert client.find_database_id("other", "sisyphus") is None
