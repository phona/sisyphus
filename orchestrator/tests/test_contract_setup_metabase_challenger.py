"""Challenger contract tests for observability/setup_metabase.py.

Derived independently from spec MBS-S1..S14 without reading the implementation.
Challenger focus: token forwarding in subsequent calls (S2 gap in prior tests),
strict idempotency assertions, and layout fidelity.

Contract scenarios covered:
  MBS-S1   load_sql returns non-empty content for every Q1-Q18 file
  MBS-S2   login stores token AND subsequent API calls carry session token
  MBS-S3   get_or_create_card creates when not found; skips when found
  MBS-S4   get_or_create_card force=True issues PUT, returns existing id
  MBS-S5   get_or_create_dashboard creates dashboard + PUTs card layout
  MBS-S6   get_or_create_dashboard skips existing unless force
  MBS-S7   provision returns correct created/skipped counts (17/1 scenario)
  MBS-S8   provision dry_run=True records zero HTTP calls
  MBS-S9   QUESTIONS has exactly 18 entries numbered 1..18
  MBS-S10  DASHBOARDS has 3 entries with keys m7, m14e, fixer
  MBS-S11  every QuestionSpec.filename resolves to a non-empty file on disk
  MBS-S12  cache_ttl values match spec groups (30s / 120s / 1800s)
  MBS-S13  main() returns exit code 1 when required args are absent
  MBS-S14  find_database_id matches by host+dbname; returns None on mismatch
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_OBS_DIR = _REPO_ROOT / "observability"
if str(_OBS_DIR) not in sys.path:
    sys.path.insert(0, str(_OBS_DIR))

import setup_metabase as sm  # noqa: E402, I001


# ── HTTP stub that records method, path, body AND token ───────────────────────

class _Recorder:
    """Captures every http_call invocation including the session token."""

    def __init__(self, responses: dict[tuple[str, str], object] | None = None):
        self.calls: list[dict] = []
        self._responses = responses or {}

    def __call__(self, method: str, url: str, body, token) -> object:
        path = urlparse(url).path
        self.calls.append({"method": method, "path": path, "body": body, "token": token})
        key = (method, path)
        if key in self._responses:
            return self._responses[key]
        # prefix match
        for (m, p), resp in self._responses.items():
            if m == method and path.startswith(p):
                return resp
        return {}


def _client(responses: dict | None = None) -> tuple[sm.MetabaseClient, _Recorder]:
    rec = _Recorder(responses)
    return sm.MetabaseClient("http://mb.test", http_call=rec), rec


# ── MBS-S1: load_sql returns content for every Q1-Q18 ─────────────────────────

@pytest.mark.parametrize("q", sm.QUESTIONS)
def test_s1_load_sql_returns_nonempty_content(q: sm.QuestionSpec):
    content = sm.load_sql(q.filename)
    assert isinstance(content, str), f"load_sql({q.filename!r}) must return str"
    assert content.strip(), f"load_sql({q.filename!r}) returned empty/whitespace content"


# ── MBS-S2: login stores token AND subsequent calls include it ─────────────────

def test_s2_login_stores_token():
    client, _rec = _client({("POST", "/api/session"): {"id": "my-session-token"}})
    client.login("admin@example.com", "s3cr3t")
    assert client._token == "my-session-token"


def test_s2_subsequent_api_calls_carry_session_token():
    """Subsequent calls MUST forward the session token to http_call."""
    client, rec = _client({
        ("POST", "/api/session"): {"id": "forwarded-token"},
        ("GET", "/api/card"): [],
        ("POST", "/api/card"): {"id": 1},
    })
    client.login("u", "p")
    # Trigger a subsequent API call (create card)
    client.get_or_create_card("Test", "SELECT 1", 7, "table", 30)

    post_session = [c for c in rec.calls if c["method"] == "POST" and c["path"] == "/api/session"]
    other_calls = [c for c in rec.calls if not (c["method"] == "POST" and c["path"] == "/api/session")]

    assert post_session, "Expected a POST /api/session call"
    assert other_calls, "Expected at least one non-login API call after login"

    for call in other_calls:
        assert call["token"] == "forwarded-token", (
            f"Call {call['method']} {call['path']} carried token={call['token']!r}, "
            f"expected 'forwarded-token'. Subsequent calls MUST include session token."
        )


# ── MBS-S3: get_or_create_card idempotency ─────────────────────────────────────

def test_s3_create_card_when_not_found():
    client, rec = _client({
        ("GET", "/api/card"): [{"id": 9, "name": "Unrelated card"}],
        ("POST", "/api/card"): {"id": 42},
    })
    card_id, created = client.get_or_create_card("Q3 Stage success rate (7d)", "SELECT 3", 7, "bar", 120)
    assert card_id == 42
    assert created is True
    posts = [c for c in rec.calls if c["method"] == "POST" and c["path"] == "/api/card"]
    assert len(posts) == 1, "Exactly one POST /api/card expected"
    assert posts[0]["body"]["name"] == "Q3 Stage success rate (7d)"


def test_s3_skip_when_card_already_exists():
    client, rec = _client({
        ("GET", "/api/card"): [{"id": 55, "name": "Q3 Stage success rate (7d)"}],
    })
    card_id, created = client.get_or_create_card("Q3 Stage success rate (7d)", "SELECT 3", 7, "bar", 120)
    assert card_id == 55
    assert created is False
    posts = [c for c in rec.calls if c["method"] == "POST" and c["path"] == "/api/card"]
    assert posts == [], "No POST /api/card when card already exists"


# ── MBS-S4: force=True updates existing card ───────────────────────────────────

def test_s4_force_issues_put_and_returns_existing_id():
    client, rec = _client({
        ("GET", "/api/card"): [{"id": 55, "name": "Q3 Stage success rate (7d)"}],
        ("PUT", "/api/card"): {},
    })
    card_id, created = client.get_or_create_card(
        "Q3 Stage success rate (7d)", "SELECT updated", 7, "bar", 120, force=True
    )
    assert card_id == 55
    assert created is False
    puts = [c for c in rec.calls if c["method"] == "PUT"]
    assert len(puts) == 1, "Exactly one PUT expected when force=True"
    assert "/api/card/55" in puts[0]["path"] or puts[0]["path"].endswith("/55"), (
        f"PUT path should target card 55, got {puts[0]['path']}"
    )
    posts = [c for c in rec.calls if c["method"] == "POST" and c["path"] == "/api/card"]
    assert posts == [], "force=True must NOT POST a new card"


# ── MBS-S5: get_or_create_dashboard creates with card layout ──────────────────

def test_s5_dashboard_created_with_layout_cards():
    client, rec = _client({
        ("GET", "/api/dashboard"): [],
        ("POST", "/api/dashboard"): {"id": 10},
        ("PUT", "/api/dashboard"): {},
    })
    card_id_map = {1: 101, 5: 105, 3: 103}
    layout = [(1, 0, 0, 9, 6), (5, 9, 0, 9, 6), (3, 0, 6, 9, 6)]
    dash_id, created = client.get_or_create_dashboard("Sisyphus M7 — Checker Health", card_id_map, layout)
    assert dash_id == 10
    assert created is True

    post_dash = [c for c in rec.calls if c["method"] == "POST" and c["path"] == "/api/dashboard"]
    assert len(post_dash) == 1, "Exactly one POST /api/dashboard expected"
    assert post_dash[0]["body"]["name"] == "Sisyphus M7 — Checker Health"

    put_cards = [c for c in rec.calls if c["method"] == "PUT" and "/api/dashboard/" in c["path"]]
    assert len(put_cards) == 1, "Exactly one PUT /api/dashboard/<id>/cards expected"
    cards_sent = put_cards[0]["body"]["cards"]
    assert len(cards_sent) == 3, "Layout has 3 cards"
    card_ids_sent = {c["card_id"] for c in cards_sent}
    assert card_ids_sent == {101, 105, 103}, "All mapped card ids must be present in layout PUT"


# ── MBS-S6: skip existing dashboard unless force ──────────────────────────────

def test_s6_skip_existing_dashboard():
    client, rec = _client({
        ("GET", "/api/dashboard"): [{"id": 77, "name": "Sisyphus M7 — Checker Health"}],
    })
    dash_id, created = client.get_or_create_dashboard(
        "Sisyphus M7 — Checker Health", {}, [], force=False
    )
    assert dash_id == 77
    assert created is False
    posts = [c for c in rec.calls if c["method"] == "POST" and c["path"] == "/api/dashboard"]
    assert posts == [], "Must not POST dashboard when name already exists"


# ── MBS-S7: provision returns correct created/skipped counts ──────────────────

def test_s7_provision_counts(tmp_path: Path):
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    for q in sm.QUESTIONS:
        (sql_dir / q.filename).write_text(f"SELECT {q.number}", encoding="utf-8")

    # Seed first question as already existing
    existing_cards: list[dict] = [{"id": 1, "name": sm.QUESTIONS[0].name}]
    next_card_id = 100

    def _http(method, url, body, token):
        nonlocal next_card_id
        path = urlparse(url).path
        if method == "GET" and path == "/api/database":
            return {"data": [{"id": 7, "details": {"host": "pg", "dbname": "sisyphus"}}]}
        if method == "GET" and path == "/api/card":
            return list(existing_cards)
        if method == "POST" and path == "/api/card":
            next_card_id += 1
            existing_cards.append({"id": next_card_id, "name": body["name"]})
            return {"id": next_card_id}
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

    assert result.questions_created == len(sm.QUESTIONS) - 1, (
        f"Expected {len(sm.QUESTIONS) - 1} created, got {result.questions_created}"
    )
    assert result.questions_skipped == 1, (
        f"Expected 1 skipped, got {result.questions_skipped}"
    )
    assert result.dashboards_created == len(sm.DASHBOARDS), (
        f"Expected {len(sm.DASHBOARDS)} dashboards created, got {result.dashboards_created}"
    )
    assert result.dashboards_skipped == 0


# ── MBS-S8: dry_run makes zero HTTP calls ─────────────────────────────────────

def test_s8_dry_run_makes_zero_http_calls(tmp_path: Path):
    sql_dir = tmp_path / "sql"
    sql_dir.mkdir()
    for q in sm.QUESTIONS:
        (sql_dir / q.filename).write_text("SELECT 1", encoding="utf-8")

    rec = _Recorder()
    client = sm.MetabaseClient("http://mb.test", http_call=rec)
    sm.provision(
        client,
        db_host="pg", db_port=5432, db_name="sisyphus", db_user="s", db_pass="p",
        dry_run=True,
        sql_dir=sql_dir,
        log=lambda _: None,
    )
    assert rec.calls == [], (
        f"dry_run=True MUST make zero HTTP calls; got {len(rec.calls)}: {rec.calls}"
    )


# ── MBS-S9: QUESTIONS has exactly 18 entries, numbered 1..18 ──────────────────

def test_s9_questions_count_and_numbering():
    assert len(sm.QUESTIONS) == 18, f"Expected 18 questions, got {len(sm.QUESTIONS)}"
    numbers = [q.number for q in sm.QUESTIONS]
    assert numbers == list(range(1, 19)), (
        f"Question numbers must be 1..18 in order; got {numbers}"
    )


# ── MBS-S10: DASHBOARDS has 3 entries with correct keys ───────────────────────

def test_s10_dashboards_count_and_keys():
    assert len(sm.DASHBOARDS) == 3, f"Expected 3 dashboards, got {len(sm.DASHBOARDS)}"
    keys = {d.key for d in sm.DASHBOARDS}
    assert keys == {"m7", "m14e", "fixer"}, f"Dashboard keys must be {{m7, m14e, fixer}}, got {keys}"


# ── MBS-S11: every QuestionSpec SQL file exists on disk ───────────────────────

@pytest.mark.parametrize("q", sm.QUESTIONS)
def test_s11_sql_file_exists(q: sm.QuestionSpec):
    path = _OBS_DIR / "queries" / "sisyphus" / q.filename
    assert path.exists(), f"SQL file missing: {path}"
    assert path.stat().st_size > 0, f"SQL file is empty: {path}"


# ── MBS-S12: cache_ttl values match spec groups ───────────────────────────────

def test_s12_cache_ttl_groups():
    fast_30 = {1, 5}
    medium_120 = {2, 3, 4, 12, 13, 17, 18}
    slow_1800 = {6, 7, 8, 9, 10, 11, 14, 15, 16}
    for q in sm.QUESTIONS:
        if q.number in fast_30:
            assert q.cache_ttl == 30, f"Q{q.number} must have cache_ttl=30, got {q.cache_ttl}"
        elif q.number in medium_120:
            assert q.cache_ttl == 120, f"Q{q.number} must have cache_ttl=120, got {q.cache_ttl}"
        elif q.number in slow_1800:
            assert q.cache_ttl == 1800, f"Q{q.number} must have cache_ttl=1800, got {q.cache_ttl}"
        else:
            pytest.fail(f"Q{q.number} not assigned to any cache_ttl group")


# ── MBS-S13: main() returns 1 on missing required args ────────────────────────

def test_s13_main_returns_1_when_required_args_missing():
    rc = sm.main(["--url", "http://metabase.example.com"])
    assert rc == 1, f"main() must return 1 when required args absent, got {rc}"


# ── MBS-S14: find_database_id matches host+dbname, None on mismatch ───────────

def test_s14_find_database_id_exact_match():
    client, _ = _client({
        ("GET", "/api/database"): {"data": [
            {"id": 3, "details": {"host": "pg-host", "dbname": "sisyphus"}},
            {"id": 5, "details": {"host": "pg-host", "dbname": "other_db"}},
            {"id": 7, "details": {"host": "other-host", "dbname": "sisyphus"}},
        ]},
    })
    assert client.find_database_id("pg-host", "sisyphus") == 3


def test_s14_find_database_id_returns_none_on_host_mismatch():
    client, _ = _client({
        ("GET", "/api/database"): {"data": [
            {"id": 3, "details": {"host": "pg-host", "dbname": "sisyphus"}},
        ]},
    })
    assert client.find_database_id("wrong-host", "sisyphus") is None


def test_s14_find_database_id_returns_none_on_dbname_mismatch():
    client, _ = _client({
        ("GET", "/api/database"): {"data": [
            {"id": 3, "details": {"host": "pg-host", "dbname": "sisyphus"}},
        ]},
    })
    assert client.find_database_id("pg-host", "wrong-db") is None
