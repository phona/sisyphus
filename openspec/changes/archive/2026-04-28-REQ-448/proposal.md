# REQ-448: feat(observability): Metabase provisioning script — setup_metabase.py

## Why

The Sisyphus observability stack (Postgres + Metabase) is designed in
`observability/sisyphus-dashboard.md` and documented in `observability/README.md`.
All 18 SQL questions (Q1–Q18) and 3 dashboards have been specified and their SQL
files committed.  But **every deployment still requires ~2 hours of manual
Metabase configuration**:

- Navigate to Admin → Databases → Add database
- Create 18 questions one by one (New Question → SQL mode → paste SQL → set display → save)
- Create 3 dashboards and drag cards into the documented grid layout
- Set cache TTL for each question individually

This is error-prone, not reproducible, and blocks the first real deployment.

## What changes

**NEW** `observability/setup_metabase.py` — a standalone Python CLI script that
provisions Metabase to the canonical Sisyphus configuration using the Metabase
v0.50 REST API:

1. Authenticate (POST `/api/session`)
2. Register the `sisyphus` PostgreSQL database (idempotent, skip if already present)
3. Create all 18 questions from `observability/queries/sisyphus/*.sql` with:
   - Correct `display` type per `sisyphus-dashboard.md` (table/bar/line/pie)
   - Cache TTL per spec (Q1/Q5 → 30 s, Q2–Q4/Q12–Q13/Q17–Q18 → 120 s, rest → 1 800 s)
4. Create 3 dashboards with documented grid layout:
   - `Sisyphus M7 — Checker Health` (Q5/Q1/Q3/Q4/Q2/Q18/Q17)
   - `Sisyphus M14e — Agent Quality` (Q12/Q13/Q6/Q8/Q9/Q11/Q7/Q10/Q14/Q15/Q16)
   - `Sisyphus Fixer Audit` (Q14/Q15/Q16)

**Idempotent**: existing items with matching names are skipped (`--force` to
overwrite).  `--dry-run` prints the plan without touching Metabase.

**NEW** `orchestrator/tests/test_setup_metabase.py` — 14 unit test scenarios
(MBS-S1..S14) covering SQL loading, HTTP client, idempotency, dry-run, and
configuration contracts.

## Impact

- No schema change, no migration, no orchestrator code change.
- Script is standalone (zero non-stdlib dependencies on Python ≥ 3.11).
- Enables reproducible Metabase setup in `< 2 min` vs `~2 h` manual.
- After first deployment, `README.md` deploy section now references the script.
