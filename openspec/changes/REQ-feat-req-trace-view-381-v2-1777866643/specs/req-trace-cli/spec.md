# req-trace-cli — spec delta

## ADDED Requirements

### Requirement: Q24 SQL aggregates four main-DB tables into one timeline

The repository SHALL provide `observability/queries/sisyphus/24-req-trace.sql`
which MUST return one row per timeline event for a given REQ. The query MUST
accept a single Metabase parameter `{{req_id}}` (text), MUST union exactly four
sub-queries sourced from the orchestrator main DB tables `req_state` (history
JSONB unrolled via `jsonb_array_elements`), `stage_runs`, `verifier_decisions`
and `artifact_checks`, and MUST output exactly the columns `ts`
(`TIMESTAMPTZ`), `kind` (`TEXT`) and `detail` (`TEXT`) ordered by `ts ASC`.
The `kind` column MUST be one of the literals `trans`, `stage`, `verify`,
`check`. Each sub-query SHALL be filtered to the supplied `{{req_id}}` so the
result is bounded to a single REQ.

The query MUST NOT reference `event_log` or any object outside the orchestrator
main DB schema (event_log lives in `sisyphus_obs` — cross-DB joins are
explicitly out-of-scope per `migrations/0002_observability_views.sql`). The
query MUST be self-contained (no dependency on views) so it remains operable
even when `req_latency` / `req_summary` views are dropped.

#### Scenario: QQ-S1 four named CTEs feed the timeline
- **GIVEN** the file `observability/queries/sisyphus/24-req-trace.sql`
- **WHEN** an operator searches for `WITH`-clause CTE names
- **THEN** the file MUST define exactly four CTEs named `trans`, `stages`,
  `verifies`, `checks` (one per event source) and the outer SELECT MUST union
  all four

#### Scenario: QQ-S2 returns three columns named ts/kind/detail
- **GIVEN** the file `observability/queries/sisyphus/24-req-trace.sql`
- **WHEN** the file is read
- **THEN** the final `ORDER BY` clause MUST sort by `ts` ascending and the
  outer SELECT (or each UNION arm) MUST project columns aliased exactly `ts`,
  `kind`, `detail`

#### Scenario: QQ-S3 only the four kinds appear in the SQL
- **GIVEN** the file `observability/queries/sisyphus/24-req-trace.sql`
- **WHEN** the file is searched for single-quoted literals supplying the
  `kind` column
- **THEN** the only literals MUST be `'trans'`, `'stage'`, `'verify'`,
  `'check'`

#### Scenario: QQ-S4 parameter placeholder is the Metabase {{req_id}} form
- **GIVEN** the file `observability/queries/sisyphus/24-req-trace.sql`
- **WHEN** the file is searched for parameter references
- **THEN** every REQ filter (one per source table — req_state, stage_runs,
  verifier_decisions, artifact_checks) MUST reference the placeholder
  `{{req_id}}` (Metabase syntax) and the file MUST NOT hardcode any specific
  `REQ-…` literal as a SQL value

### Requirement: sisyphus-trace CLI renders the timeline at the terminal

The repository SHALL provide an executable `scripts/sisyphus-trace.py` whose
single positional argument is a REQ-id. The CLI MUST exit 0 on success and
non-zero on any error (missing kubectl, PG unreachable, REQ-id not found).
On success the CLI MUST print one human-readable line per timeline row in
the format `HH:MM:SS [<kind>] <detail>` separated from the header by a Unicode
divider line (one `─` repeated ≥ 20 times). The first stdout line MUST be
`sisyphus-trace <REQ-id>` (literal command + the resolved REQ-id) so output
is greppable by REQ.

The CLI SHALL accept the optional flags `--json`, `--namespace <ns>`,
`--pg-pod <pod>`. When `--json` is supplied the CLI MUST print one JSON object
per line (NDJSON) with keys `ts` (RFC3339 string), `kind`, `detail` and MUST
NOT print the ASCII header / divider. Default `--namespace` MUST equal
`sisyphus`; default `--pg-pod` MUST equal `sisyphus-postgresql-0` (matching
`sisyphus-admin.py` defaults so operators do not need to relearn flags).

The CLI MUST NOT swallow PG errors silently — any psql non-zero exit MUST be
reported to stderr and propagate as exit code 1.

#### Scenario: TRACE-S1 ASCII render shape
- **GIVEN** an in-memory list of three event rows
  `[(t1,"trans","INIT → ANALYZING"), (t2,"stage","analyze start"),
    (t3,"check","spec_lint passed")]`
- **WHEN** the renderer is invoked with `req_id="REQ-X"`
- **THEN** the output MUST start with the literal line `sisyphus-trace REQ-X`,
  followed by a divider line whose first character is `─`, followed by exactly
  three event lines each matching `^\d{2}:\d{2}:\d{2} \[(trans|stage|verify|check)\] `

#### Scenario: TRACE-S2 --json mode emits NDJSON
- **GIVEN** the same three event rows
- **WHEN** the renderer is invoked with `--json` mode
- **THEN** stdout MUST contain exactly three lines, each line MUST parse as a
  JSON object whose keys are exactly `{ts, kind, detail}`, and the header /
  divider MUST NOT appear

#### Scenario: TRACE-S3 missing REQ-id positional triggers usage error
- **GIVEN** the CLI argparse parser
- **WHEN** the parser is asked to parse an empty argv
- **THEN** parsing MUST fail (`SystemExit` with code 2) and the error message
  MUST mention `req_id`

#### Scenario: TRACE-S4 default kubectl knobs match sisyphus-admin
- **GIVEN** the CLI argparse parser built without overrides
- **WHEN** the resulting `Namespace` is inspected
- **THEN** `namespace` MUST equal `"sisyphus"` and `pg_pod` MUST equal
  `"sisyphus-postgresql-0"`

### Requirement: dashboard and CLAUDE.md surface the new tool

The file `observability/sisyphus-dashboard.md` MUST gain a Q24 section
documenting the SQL path, suggested visualization (Table) and parameter
binding (`req_id`). The file `CLAUDE.md` MUST gain a debug-oriented paragraph
referencing `scripts/sisyphus-trace.py <REQ-id>` so future operators know
which command to run when a REQ appears stuck. Neither file SHALL remove or
renumber existing Q1–Q23 entries.

#### Scenario: DOC-S1 dashboard contains a Q24 heading
- **GIVEN** `observability/sisyphus-dashboard.md`
- **WHEN** the file is searched for headings
- **THEN** there MUST exist a heading line matching `^### Q24\.` and the
  body MUST link `queries/sisyphus/24-req-trace.sql`

#### Scenario: DOC-S2 CLAUDE.md mentions sisyphus-trace
- **GIVEN** `CLAUDE.md`
- **WHEN** the file is searched for the literal `sisyphus-trace`
- **THEN** at least one occurrence MUST exist and that occurrence MUST be
  inside (or immediately under) a heading containing the word `debug`
  (case-insensitive)
