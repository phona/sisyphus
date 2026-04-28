#!/usr/bin/env python3
"""Provision Metabase questions and dashboards for Sisyphus observability.

Automates the Metabase setup described in observability/sisyphus-dashboard.md.
Creates 18 SQL questions (Q1-Q18) and 3 dashboards using the Metabase REST API.
Idempotent: items with matching names are skipped unless --force is given.

Usage:
    python setup_metabase.py [options]

    Required (env var or flag):
        MB_URL  / --url       Metabase base URL  (e.g. http://metabase.example.com)
        MB_USER / --user      Admin email
        MB_PASS / --pass      Admin password
        MB_DB_HOST / --db-host  PostgreSQL host for sisyphus DB
        MB_DB_PASS / --db-pass  DB password

    Optional:
        MB_DB_PORT / --db-port  PostgreSQL port (default: 5432)
        MB_DB_NAME / --db-name  Database name   (default: sisyphus)
        MB_DB_USER / --db-user  DB username     (default: sisyphus)
        --force    Overwrite existing questions / dashboards
        --dry-run  Print plan without making API calls
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
_SQL_DIR = _HERE / "queries" / "sisyphus"

# ── Question metadata ──────────────────────────────────────────────────────────

# display values accepted by Metabase /api/card: table, bar, line, pie, scalar, row
# cache_ttl is in seconds; 0 means use Metabase default (no override)


@dataclass(frozen=True)
class QuestionSpec:
    number: int          # 1-18
    filename: str        # SQL filename under _SQL_DIR
    name: str            # Metabase card name
    display: str         # Metabase display type
    cache_ttl: int       # seconds; 0 = no override
    dashboard: str       # dashboard name key


QUESTIONS: list[QuestionSpec] = [
    QuestionSpec(1,  "01-stuck-checks.sql",
                 "Q1 Stuck checks (24h)",
                 "table", 30, "m7"),
    QuestionSpec(2,  "02-check-duration-anomaly.sql",
                 "Q2 Check duration anomaly",
                 "table", 120, "m7"),
    QuestionSpec(3,  "03-stage-success-rate.sql",
                 "Q3 Stage success rate (7d)",
                 "bar", 120, "m7"),
    QuestionSpec(4,  "04-fail-kind-distribution.sql",
                 "Q4 Fail kind distribution",
                 "pie", 120, "m7"),
    QuestionSpec(5,  "05-active-req-overview.sql",
                 "Q5 Active REQ overview",
                 "table", 30, "m7"),
    QuestionSpec(6,  "06-stage-success-rate-by-week.sql",
                 "Q6 Stage success rate by week",
                 "line", 1800, "m14e"),
    QuestionSpec(7,  "07-stage-duration-percentiles.sql",
                 "Q7 Stage duration percentiles",
                 "table", 1800, "m14e"),
    QuestionSpec(8,  "08-verifier-decision-accuracy.sql",
                 "Q8 Verifier decision accuracy",
                 "bar", 1800, "m14e"),
    QuestionSpec(9,  "09-fix-success-rate-by-fixer.sql",
                 "Q9 Fix success rate by fixer",
                 "table", 1800, "m14e"),
    QuestionSpec(10, "10-token-cost-by-req.sql",
                 "Q10 Token cost by REQ",
                 "table", 1800, "m14e"),
    QuestionSpec(11, "11-parallel-dev-speedup.sql",
                 "Q11 Parallel dev speedup",
                 "table", 1800, "m14e"),
    QuestionSpec(12, "12-bugfix-loop-anomaly.sql",
                 "Q12 Bugfix loop anomaly",
                 "table", 120, "m14e"),
    QuestionSpec(13, "13-watchdog-escalate-frequency.sql",
                 "Q13 Watchdog escalate frequency",
                 "line", 120, "m14e"),
    QuestionSpec(14, "14-fixer-audit-verdict-trend.sql",
                 "Q14 Fixer audit verdict trend",
                 "bar", 1800, "fixer"),
    QuestionSpec(15, "15-suspicious-pass-decisions.sql",
                 "Q15 Suspicious pass decisions",
                 "table", 1800, "fixer"),
    QuestionSpec(16, "16-fixer-file-category-breakdown.sql",
                 "Q16 Fixer file category breakdown",
                 "bar", 1800, "fixer"),
    QuestionSpec(17, "17-dedup-retry-rate.sql",
                 "Q17 Webhook dedup processed-at split",
                 "bar", 120, "m7"),
    QuestionSpec(18, "18-silent-pass-detector.sql",
                 "Q18 Silent-pass detector",
                 "table", 120, "m7"),
]


# ── Dashboard layout ────────────────────────────────────────────────────────────
# Each entry: (question_number, row, col, size_x, size_y)
# Grid is 18 columns wide.

@dataclass(frozen=True)
class DashboardSpec:
    key: str
    name: str
    layout: list[tuple[int, int, int, int, int]]  # (q_number, row, col, size_x, size_y)


DASHBOARDS: list[DashboardSpec] = [
    DashboardSpec(
        key="m7",
        name="Sisyphus M7 — Checker Health",
        layout=[
            (5,  0,  0, 9, 6),   # Q5 Active REQ overview – left
            (1,  0,  9, 9, 6),   # Q1 Stuck checks – right
            (3,  6,  0, 9, 6),   # Q3 Stage success rate – left
            (4,  6,  9, 9, 6),   # Q4 Fail kind distribution – right
            (2,  12, 0, 18, 6),  # Q2 Duration anomaly – full width
            (18, 18, 0, 18, 6),  # Q18 Silent-pass detector – full width
            (17, 24, 0, 18, 6),  # Q17 Webhook dedup – full width
        ],
    ),
    DashboardSpec(
        key="m14e",
        name="Sisyphus M14e — Agent Quality",
        layout=[
            (12, 0,  0, 9, 6),   # Q12 Bugfix loop anomaly – left
            (13, 0,  9, 9, 6),   # Q13 Watchdog escalate – right
            (6,  6,  0, 9, 6),   # Q6 Stage success by week – left
            (8,  6,  9, 9, 6),   # Q8 Verifier accuracy – right
            (9,  12, 0, 9, 6),   # Q9 Fix success by fixer – left
            (11, 12, 9, 9, 6),   # Q11 Parallel speedup – right
            (7,  18, 0, 9, 6),   # Q7 Duration percentiles – left
            (10, 18, 9, 9, 6),   # Q10 Token cost – right
            (14, 24, 0, 9, 6),   # Q14 Fixer audit verdict – left
            (15, 24, 9, 9, 6),   # Q15 Suspicious pass – right
            (16, 30, 0, 18, 6),  # Q16 File category breakdown – full width
        ],
    ),
    DashboardSpec(
        key="fixer",
        name="Sisyphus Fixer Audit",
        layout=[
            (14, 0, 0, 9, 6),   # Q14 Fixer audit verdict – left
            (15, 0, 9, 9, 6),   # Q15 Suspicious pass – right
            (16, 6, 0, 18, 6),  # Q16 File category breakdown – full width
        ],
    ),
]


# ── HTTP client ─────────────────────────────────────────────────────────────────

HttpCallable = Any  # (method, url, body_dict, token) -> dict


def _default_http(method: str, url: str, body: dict | None, token: str | None) -> dict:
    """Thin urllib wrapper.  Returns parsed JSON response body."""
    data = json.dumps(body).encode() if body is not None else None
    headers: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["X-Metabase-Session"] = token

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        msg = raw.decode(errors="replace") if raw else str(exc)
        raise RuntimeError(f"HTTP {exc.code} {method} {url}: {msg}") from exc


# ── Metabase API client ─────────────────────────────────────────────────────────


class MetabaseClient:
    """REST API wrapper for Metabase v0.50."""

    def __init__(self, base_url: str, *, http_call: HttpCallable | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._token: str | None = None
        self._http = http_call or _default_http

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{self._base}{path}"
        return self._http(method, url, body, self._token)

    def login(self, user: str, password: str) -> None:
        result = self._call("POST", "/api/session", {"username": user, "password": password})
        self._token = result["id"]

    # ── Database ────────────────────────────────────────────────────────────────

    def list_databases(self) -> list[dict]:
        result = self._call("GET", "/api/database", None)
        return result.get("data", result) if isinstance(result, dict) else result

    def find_database_id(self, host: str, dbname: str) -> int | None:
        for db in self.list_databases():
            details = db.get("details", {})
            if details.get("host") == host and details.get("dbname") == dbname:
                return int(db["id"])
        return None

    def create_database(
        self,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
    ) -> int:
        body = {
            "engine": "postgres",
            "name": f"sisyphus ({dbname}@{host})",
            "details": {
                "host": host,
                "port": port,
                "dbname": dbname,
                "user": user,
                "password": password,
                "ssl": False,
            },
            "auto_run_queries": True,
        }
        result = self._call("POST", "/api/database", body)
        return int(result["id"])

    def get_or_create_database(
        self, host: str, port: int, dbname: str, user: str, password: str
    ) -> int:
        db_id = self.find_database_id(host, dbname)
        if db_id is not None:
            return db_id
        return self.create_database(host, port, dbname, user, password)

    # ── Cards (Questions) ───────────────────────────────────────────────────────

    def list_cards(self) -> list[dict]:
        return self._call("GET", "/api/card", None)  # returns list directly

    def find_card_id(self, name: str) -> int | None:
        for card in self.list_cards():
            if card.get("name") == name:
                return int(card["id"])
        return None

    def create_card(
        self,
        name: str,
        sql: str,
        database_id: int,
        display: str,
        cache_ttl: int,
    ) -> int:
        body: dict = {
            "name": name,
            "display": display,
            "dataset_query": {
                "type": "native",
                "native": {"query": sql},
                "database": database_id,
            },
            "visualization_settings": {},
        }
        if cache_ttl > 0:
            body["cache_ttl"] = cache_ttl
        result = self._call("POST", "/api/card", body)
        return int(result["id"])

    def update_card(
        self,
        card_id: int,
        sql: str,
        database_id: int,
        display: str,
        cache_ttl: int,
    ) -> None:
        body: dict = {
            "display": display,
            "dataset_query": {
                "type": "native",
                "native": {"query": sql},
                "database": database_id,
            },
        }
        if cache_ttl > 0:
            body["cache_ttl"] = cache_ttl
        self._call("PUT", f"/api/card/{card_id}", body)

    def get_or_create_card(
        self,
        name: str,
        sql: str,
        database_id: int,
        display: str,
        cache_ttl: int,
        *,
        force: bool = False,
    ) -> tuple[int, bool]:
        """Return (card_id, created).  If force=True, updates existing card."""
        existing = self.find_card_id(name)
        if existing is not None:
            if force:
                self.update_card(existing, sql, database_id, display, cache_ttl)
            return existing, False
        card_id = self.create_card(name, sql, database_id, display, cache_ttl)
        return card_id, True

    # ── Dashboards ──────────────────────────────────────────────────────────────

    def list_dashboards(self) -> list[dict]:
        return self._call("GET", "/api/dashboard", None)

    def find_dashboard_id(self, name: str) -> int | None:
        for dash in self.list_dashboards():
            if dash.get("name") == name:
                return int(dash["id"])
        return None

    def create_dashboard(self, name: str) -> int:
        result = self._call("POST", "/api/dashboard", {"name": name})
        return int(result["id"])

    def add_cards_to_dashboard(
        self, dashboard_id: int, cards: list[dict]
    ) -> None:
        """Add cards in bulk.  cards: list of {card_id, row, col, size_x, size_y}."""
        self._call("PUT", f"/api/dashboard/{dashboard_id}/cards", {"cards": cards})

    def get_or_create_dashboard(
        self,
        name: str,
        card_id_map: dict[int, int],
        layout: list[tuple[int, int, int, int, int]],
        *,
        force: bool = False,
    ) -> tuple[int, bool]:
        """Return (dashboard_id, created)."""
        existing = self.find_dashboard_id(name)
        if existing is not None and not force:
            return existing, False
        if existing is None:
            dashboard_id = self.create_dashboard(name)
            created = True
        else:
            dashboard_id = existing
            created = False

        cards = [
            {
                "id": None,
                "card_id": card_id_map[q_num],
                "row": row,
                "col": col,
                "size_x": size_x,
                "size_y": size_y,
            }
            for q_num, row, col, size_x, size_y in layout
            if q_num in card_id_map
        ]
        if cards:
            self.add_cards_to_dashboard(dashboard_id, cards)
        return dashboard_id, created


# ── SQL loading ─────────────────────────────────────────────────────────────────


def load_sql(filename: str, sql_dir: Path = _SQL_DIR) -> str:
    path = sql_dir / filename
    if not path.exists():
        raise FileNotFoundError(f"SQL file not found: {path}")
    return path.read_text(encoding="utf-8")


# ── Provisioning ────────────────────────────────────────────────────────────────


@dataclass
class ProvisionResult:
    questions_created: int = 0
    questions_skipped: int = 0
    dashboards_created: int = 0
    dashboards_skipped: int = 0
    card_id_map: dict[int, int] = field(default_factory=dict)  # q_number -> card_id


def provision(
    client: MetabaseClient,
    db_host: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_pass: str,
    *,
    force: bool = False,
    dry_run: bool = False,
    sql_dir: Path = _SQL_DIR,
    log=print,
) -> ProvisionResult:
    result = ProvisionResult()

    if dry_run:
        log("[dry-run] Would authenticate to Metabase")
        log(f"[dry-run] Would ensure DB connection: {db_user}@{db_host}/{db_name}")
        for q in QUESTIONS:
            log(f"[dry-run] Would provision question: {q.name}")
        for dash in DASHBOARDS:
            log(f"[dry-run] Would provision dashboard: {dash.name}")
        return result

    # Step 1: ensure database
    log(f"Ensuring database connection: {db_user}@{db_host}:{db_port}/{db_name}")
    database_id = client.get_or_create_database(db_host, db_port, db_name, db_user, db_pass)
    log(f"  Database id: {database_id}")

    # Step 2: provision questions
    for q in QUESTIONS:
        sql = load_sql(q.filename, sql_dir)
        card_id, created = client.get_or_create_card(
            q.name, sql, database_id, q.display, q.cache_ttl, force=force
        )
        result.card_id_map[q.number] = card_id
        if created:
            result.questions_created += 1
            log(f"  [created] {q.name} (id={card_id})")
        else:
            result.questions_skipped += 1
            log(f"  [skip]    {q.name} (id={card_id})")

    # Step 3: provision dashboards
    for dash in DASHBOARDS:
        dashboard_id, created = client.get_or_create_dashboard(
            dash.name, result.card_id_map, dash.layout, force=force
        )
        if created:
            result.dashboards_created += 1
            log(f"  [created] Dashboard: {dash.name} (id={dashboard_id})")
        else:
            result.dashboards_skipped += 1
            log(f"  [skip]    Dashboard: {dash.name} (id={dashboard_id})")

    return result


# ── CLI ─────────────────────────────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Provision Metabase for Sisyphus observability",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--url", default=os.environ.get("MB_URL", ""), help="Metabase URL")
    p.add_argument("--user", default=os.environ.get("MB_USER", ""), help="Admin email")
    p.add_argument("--pass", dest="password", default=os.environ.get("MB_PASS", ""),
                   help="Admin password")
    p.add_argument("--db-host", default=os.environ.get("MB_DB_HOST", ""), help="PG host")
    p.add_argument("--db-port", type=int, default=int(os.environ.get("MB_DB_PORT", "5432")),
                   help="PG port")
    p.add_argument("--db-name", default=os.environ.get("MB_DB_NAME", "sisyphus"),
                   help="Database name")
    p.add_argument("--db-user", default=os.environ.get("MB_DB_USER", "sisyphus"),
                   help="DB username")
    p.add_argument("--db-pass", default=os.environ.get("MB_DB_PASS", ""), help="DB password")
    p.add_argument("--force", action="store_true", help="Overwrite existing items")
    p.add_argument("--dry-run", action="store_true", help="Print plan, no API calls")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    missing = [f for f, v in [
        ("--url / MB_URL", args.url),
        ("--user / MB_USER", args.user),
        ("--pass / MB_PASS", args.password),
        ("--db-host / MB_DB_HOST", args.db_host),
        ("--db-pass / MB_DB_PASS", args.db_pass),
    ] if not v and not args.dry_run]
    if missing:
        print(f"Error: missing required arguments: {', '.join(missing)}", file=sys.stderr)
        return 1

    client = MetabaseClient(args.url)
    if not args.dry_run:
        print(f"Authenticating as {args.user} @ {args.url}")
        client.login(args.user, args.password)

    result = provision(
        client,
        db_host=args.db_host,
        db_port=args.db_port,
        db_name=args.db_name,
        db_user=args.db_user,
        db_pass=args.db_pass,
        force=args.force,
        dry_run=args.dry_run,
    )

    print(
        f"\nDone: {result.questions_created} questions created, "
        f"{result.questions_skipped} skipped; "
        f"{result.dashboards_created} dashboards created, "
        f"{result.dashboards_skipped} skipped."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
