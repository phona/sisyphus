# silent-pass-detector Specification

## ADDED Requirements

### Requirement: Q18 SQL MUST detect three classes of silent-pass signals over the last 24h

The system SHALL ship a SQL file at
`observability/queries/sisyphus/18-silent-pass-detector.sql` that scans the
`artifact_checks` table for rows whose `passed = true` AND `checked_at >
now() - interval '24 hours'`, classifying each match into exactly one
`silent_pass_kind` column out of `'guard-leak'`, `'no-gha-pass'`, or
`'too-fast'`. The query MUST emit at minimum the columns `req_id`, `stage`,
`silent_pass_kind`, `duration_sec`, `p50_sec`, `ratio`, `evidence`, `cmd`,
and `checked_at`, so that an operator reading the Metabase table can
attribute each suspicious row to a REQ + stage and inspect the captured
checker output without re-querying.

#### Scenario: Q18-S1 SQL file exists at the canonical path

- **GIVEN** the working tree at the head of feat/REQ-metabase-silent-pass-detector-1777123726
- **WHEN** `ls observability/queries/sisyphus/18-silent-pass-detector.sql` runs
- **THEN** the file exists and is non-empty

#### Scenario: Q18-S2 SQL filters to passed=true within 24h window

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the WHERE clause is parsed
- **THEN** it contains both `c.passed = true` AND `c.checked_at > now() - interval '24 hours'` (case-insensitive on identifiers)

#### Scenario: Q18-S3 silent_pass_kind CASE branches cover the three signals

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the SELECT-list `silent_pass_kind` CASE expression is parsed
- **THEN** it produces literal string values `'guard-leak'`, `'no-gha-pass'`, AND `'too-fast'` in three distinct WHEN branches

#### Scenario: Q18-S4 output columns include req_id / stage / kind / duration / baseline / ratio / evidence / cmd / checked_at

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the top-level SELECT list is parsed
- **THEN** it projects columns named (or aliased) `req_id`, `stage`, `silent_pass_kind`, `duration_sec`, `p50_sec`, `ratio`, `evidence`, `cmd`, `checked_at`

### Requirement: guard-leak signal MUST mirror the literal "refusing to silent-pass" marker emitted by checker source

The system SHALL classify an `artifact_checks` row as
`silent_pass_kind = 'guard-leak'` when `passed = true` AND the concatenation
of `stdout_tail` and `stderr_tail` matches the literal substring
`refusing to silent-pass` (case-insensitive). This signal MUST take
precedence over `no-gha-pass` and `too-fast` in the CASE evaluation order,
because the marker is emitted directly by the
`spec_lint` / `dev_cross_check` / `staging_test` checker scripts when
their internal empty-source / `ran=0` guard fires — its presence alongside
`passed=true` proves the guard fired but exit code stayed 0, which is a
checker implementation bug that MUST surface immediately regardless of
duration baselines.

#### Scenario: Q18-S5 guard-leak literal "refusing to silent-pass" matches checker source

- **GIVEN** `orchestrator/src/orchestrator/checkers/spec_lint.py`, `dev_cross_check.py`, and `staging_test.py` are scanned
- **WHEN** `grep -c 'refusing to silent-pass'` runs across those three files
- **THEN** each file has at least 2 hits (matches the literal the SQL pattern depends on, so spec ↔ source stays in sync)

#### Scenario: Q18-S6 SQL ILIKE pattern uses literal substring "refusing to silent-pass"

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the WHERE clause and the silent_pass_kind CASE are parsed
- **THEN** both reference the literal string `refusing to silent-pass` via ILIKE pattern `%refusing to silent-pass%`

#### Scenario: Q18-S7 guard-leak takes precedence over no-gha-pass and too-fast

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the silent_pass_kind CASE expression is parsed
- **THEN** the WHEN branch testing `refusing to silent-pass` appears textually before the WHEN branch testing `no-gha-checks-ran` AND before the WHEN branch testing the duration ratio

### Requirement: no-gha-pass signal MUST mirror the literal "no-gha-checks-ran" marker emitted by pr_ci_watch source

The system SHALL classify an `artifact_checks` row as
`silent_pass_kind = 'no-gha-pass'` when `passed = true` AND `stdout_tail`
matches the literal substring `no-gha-checks-ran` (case-insensitive). This
signal MUST take precedence over `too-fast`, because
`orchestrator/src/orchestrator/checkers/pr_ci_watch.py:_classify` returns
`'no-gha'` for the all-green-but-zero-GHA-check-runs verdict and the caller
treats that as fail — so an `artifact_checks` row carrying both `passed=true`
AND that marker proves the classification logic was changed in a way that
broke the fail path.

#### Scenario: Q18-S8 SQL ILIKE pattern uses literal substring "no-gha-checks-ran"

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the WHERE clause and the silent_pass_kind CASE are parsed
- **THEN** both reference the literal string `no-gha-checks-ran` via ILIKE pattern `%no-gha-checks-ran%`

#### Scenario: Q18-S9 no-gha-pass literal matches pr_ci_watch source

- **GIVEN** `orchestrator/src/orchestrator/checkers/pr_ci_watch.py` is scanned
- **WHEN** `grep -c 'no-gha-checks-ran'` runs
- **THEN** the file has at least 1 hit (matches the literal the SQL depends on)

### Requirement: too-fast signal MUST use a 7d per-stage P50 baseline of passed-true samples with a 20-sample minimum

The system SHALL define a CTE named `baseline` that computes
`PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_sec)` per `stage` over
`artifact_checks` rows where `checked_at > now() - interval '7 days'`,
`duration_sec IS NOT NULL`, AND `passed = true`. The CTE MUST apply
`HAVING COUNT(*) >= 20` to drop stages with insufficient samples. The
top-level SELECT MUST then classify a row as
`silent_pass_kind = 'too-fast'` only when the joined baseline `p50_sec`
exists AND `c.duration_sec < b.p50_sec * 0.2`. Stages with insufficient
samples MUST NOT emit `too-fast` rows (no false positives from small
samples), but MUST still emit `guard-leak` / `no-gha-pass` rows because
those signals are absolute and do not depend on the baseline.

#### Scenario: Q18-S10 baseline CTE filters to passed=true samples over 7d

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the `baseline` CTE body is parsed
- **THEN** the WHERE clause contains `passed = true` AND `checked_at > now() - interval '7 days'` AND `duration_sec IS NOT NULL`

#### Scenario: Q18-S11 baseline CTE uses PERCENTILE_CONT(0.5) per stage

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the `baseline` CTE body is parsed
- **THEN** the SELECT computes `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_sec)` AND the GROUP BY is on `stage`

#### Scenario: Q18-S12 baseline CTE drops stages with fewer than 20 samples

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the `baseline` CTE body is parsed
- **THEN** it contains `HAVING COUNT(*) >= 20`

#### Scenario: Q18-S13 too-fast threshold uses 0.2 × P50

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the silent_pass_kind CASE and the WHERE clause are parsed
- **THEN** both express the too-fast condition as `duration_sec < ... p50_sec * 0.2` (using whatever alias the CTE binds to p50_sec)

#### Scenario: Q18-S14 stages without baseline still emit guard-leak / no-gha-pass rows

- **GIVEN** the SQL file `18-silent-pass-detector.sql` is read
- **WHEN** the JOIN between `artifact_checks` and `baseline` is parsed
- **THEN** it is a LEFT JOIN (so rows survive when no baseline exists), AND the WHERE clause guards the duration check with `b.p50_sec IS NOT NULL` so absent-baseline rows can still match the absolute markers

### Requirement: dashboard md MUST publish Q18 documentation with visualization, three signal kinds, layout, refresh, and alert rules

The document `observability/sisyphus-dashboard.md` SHALL contain a section
introducing Q18 that names all three `silent_pass_kind` values
(`guard-leak`, `no-gha-pass`, `too-fast`) and links to
`queries/sisyphus/18-silent-pass-detector.sql`. The section MUST specify
the visualization (Table) and intervention thresholds. The dashboard
layout, refresh-frequency, and alert sections MUST be amended in the same
document so a reader who only consults the dashboard md can wire Q18 into
Metabase without reading the SQL itself.

#### Scenario: Q18-S15 dashboard md introduces Q18 section with the SQL link

- **GIVEN** the file `observability/sisyphus-dashboard.md` is read
- **WHEN** the heading `### Q18.` (or its body) is parsed
- **THEN** it links to `queries/sisyphus/18-silent-pass-detector.sql` AND names all three values `guard-leak`, `no-gha-pass`, `too-fast`

#### Scenario: Q18-S16 dashboard md publishes Visualization=Table for Q18

- **GIVEN** the Q18 section of `observability/sisyphus-dashboard.md`
- **WHEN** the Visualization line is read
- **THEN** the visualization is `Table` AND the column order names `req_id`, `stage`, `silent_pass_kind`, `duration_sec`, `p50_sec`, `ratio`, `evidence`, `cmd`, `checked_at`

#### Scenario: Q18-S17 dashboard layout section adds a Q18 frame

- **GIVEN** `observability/sisyphus-dashboard.md` is read
- **WHEN** the "看板布局" section is parsed
- **THEN** it includes a frame mentioning `Q18` so Q18 has a designated dashboard slot

#### Scenario: Q18-S18 dashboard refresh-frequency section names Q18

- **GIVEN** `observability/sisyphus-dashboard.md` is read
- **WHEN** the "刷新频率" section is parsed
- **THEN** it names `Q18` with a refresh cadence (5 minute target, matching Q2)

#### Scenario: Q18-S19 dashboard alert section adds Q18 trigger for guard-leak / no-gha-pass

- **GIVEN** `observability/sisyphus-dashboard.md` is read
- **WHEN** the "告警" section is parsed
- **THEN** it lists `Q18` alongside an alert condition naming both `guard-leak` AND `no-gha-pass`
