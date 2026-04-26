# observability-dashboard Specification

## ADDED Requirements

### Requirement: M7 sisyphus checker dashboard MUST 提供 5 条针对 artifact_checks 的 SQL 文件 (Q1–Q5)

The sisyphus repo SHALL ship the M7 milestone's five operations dashboard
queries as **checked-in SQL files** under `observability/queries/sisyphus/`,
named `01-stuck-checks.sql`, `02-check-duration-anomaly.sql`,
`03-stage-success-rate.sql`, `04-fail-kind-distribution.sql`, and
`05-active-req-overview.sql`. Every file MUST contain a single `SELECT`
statement that primarily reads from the `artifact_checks` table, because the
M7 cohort exists to surface "where is sisyphus's mechanical checker spinning"
and `artifact_checks` is the only table that records each `make ci-*` /
GitHub REST poll outcome at row granularity. Q5 MUST additionally `JOIN` /
sub-select `req_state` to expose the live `state` column alongside the latest
check; the other four MUST NOT depend on tables outside the sisyphus
observability surface (no `bkd_snapshot`, no `event_log`, no `stage_runs`,
no `verifier_decisions`).

#### Scenario: ODB-S1 Q1 stuck-checks SQL exists and reads artifact_checks

- **GIVEN** the working tree at HEAD of `feat/REQ-observability-metabase-1777189271`
- **WHEN** `observability/queries/sisyphus/01-stuck-checks.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain the literal substring `FROM artifact_checks`
- **AND** the file MUST NOT contain the literals `FROM bkd_snapshot`, `FROM event_log`, `FROM stage_runs`, or `FROM verifier_decisions`

#### Scenario: ODB-S2 Q2 check-duration-anomaly SQL exists and reads artifact_checks

- **GIVEN** the working tree at HEAD of `feat/REQ-observability-metabase-1777189271`
- **WHEN** `observability/queries/sisyphus/02-check-duration-anomaly.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain the literal substring `FROM artifact_checks`
- **AND** the file MUST NOT contain `FROM stage_runs` or `FROM verifier_decisions`

#### Scenario: ODB-S3 Q3 stage-success-rate SQL exists and reads artifact_checks

- **GIVEN** the working tree at HEAD of `feat/REQ-observability-metabase-1777189271`
- **WHEN** `observability/queries/sisyphus/03-stage-success-rate.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain the literal substring `FROM artifact_checks`

#### Scenario: ODB-S4 Q4 fail-kind-distribution SQL exists and reads artifact_checks

- **GIVEN** the working tree at HEAD of `feat/REQ-observability-metabase-1777189271`
- **WHEN** `observability/queries/sisyphus/04-fail-kind-distribution.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain the literal substring `FROM artifact_checks`

#### Scenario: ODB-S5 Q5 active-req-overview SQL joins artifact_checks with req_state

- **GIVEN** the working tree at HEAD of `feat/REQ-observability-metabase-1777189271`
- **WHEN** `observability/queries/sisyphus/05-active-req-overview.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain BOTH the literal substring `FROM artifact_checks` AND the literal substring `req_state` (Q5 surfaces the live REQ `state` column)

### Requirement: M14e quality dashboard MUST 提供 8 条针对 stage_runs / verifier_decisions 的 SQL 文件 (Q6–Q13)

The sisyphus repo SHALL ship the M14e milestone's eight quality dashboard
queries as **checked-in SQL files** under `observability/queries/sisyphus/`,
named `06-stage-success-rate-by-week.sql`, `07-stage-duration-percentiles.sql`,
`08-verifier-decision-accuracy.sql`, `09-fix-success-rate-by-fixer.sql`,
`10-token-cost-by-req.sql`, `11-parallel-dev-speedup.sql`,
`12-bugfix-loop-anomaly.sql`, and `13-watchdog-escalate-frequency.sql`. Every
file MUST contain a single `SELECT` statement whose primary `FROM` clause
references at least one of `stage_runs` or `verifier_decisions`, because
these two tables are the M14e instrumentation surface (see
`orchestrator/migrations/0004_stage_runs.sql` and
`0005_verifier_decisions.sql`). The M14e cohort MUST NOT depend on
`artifact_checks` for its primary data — that is the M7 surface — but Q13
MAY combine `stage_runs` and `verifier_decisions` to attribute escalates by
stage.

#### Scenario: ODB-S6 Q6 weekly stage success rate reads stage_runs

- **GIVEN** the working tree at HEAD
- **WHEN** `observability/queries/sisyphus/06-stage-success-rate-by-week.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain `FROM stage_runs`

#### Scenario: ODB-S7 Q7 stage duration percentiles reads stage_runs

- **GIVEN** the working tree at HEAD
- **WHEN** `observability/queries/sisyphus/07-stage-duration-percentiles.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain `FROM stage_runs`

#### Scenario: ODB-S8 Q8 verifier decision accuracy reads verifier_decisions

- **GIVEN** the working tree at HEAD
- **WHEN** `observability/queries/sisyphus/08-verifier-decision-accuracy.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain `FROM verifier_decisions`

#### Scenario: ODB-S9 Q9 fix success rate reads verifier_decisions

- **GIVEN** the working tree at HEAD
- **WHEN** `observability/queries/sisyphus/09-fix-success-rate-by-fixer.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain `FROM verifier_decisions`

#### Scenario: ODB-S10 Q10 token cost reads stage_runs

- **GIVEN** the working tree at HEAD
- **WHEN** `observability/queries/sisyphus/10-token-cost-by-req.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain `FROM stage_runs`

#### Scenario: ODB-S11 Q11 parallel dev speedup reads stage_runs

- **GIVEN** the working tree at HEAD
- **WHEN** `observability/queries/sisyphus/11-parallel-dev-speedup.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain `FROM stage_runs`

#### Scenario: ODB-S12 Q12 bugfix loop anomaly reads stage_runs

- **GIVEN** the working tree at HEAD
- **WHEN** `observability/queries/sisyphus/12-bugfix-loop-anomaly.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain `FROM stage_runs`

#### Scenario: ODB-S13 Q13 watchdog escalate frequency joins stage_runs and verifier_decisions

- **GIVEN** the working tree at HEAD
- **WHEN** `observability/queries/sisyphus/13-watchdog-escalate-frequency.sql` is read
- **THEN** the file MUST exist with non-zero size
- **AND** it MUST contain BOTH the literal substrings `stage_runs` AND `verifier_decisions` (Q13 attributes escalates by stage)

### Requirement: sisyphus-dashboard.md MUST 索引全部 13 条 question 的链接、可视化形式与刷新频率

The canonical dashboard index `observability/sisyphus-dashboard.md` SHALL
index every Q1 through Q13 by linking the relative path
`queries/sisyphus/<NN>-...sql` (where `NN` is the zero-padded ordinal `01`
through `13`) for each question, MUST document each question's primary
visualization form (Table / Bar / Line / Pie), and MUST publish a refresh
frequency for every question in the dedicated 刷新频率 / Refresh section.
The 5 + 8 split between M7 (Q1–Q5, `artifact_checks`) and M14e (Q6–Q13,
`stage_runs` / `verifier_decisions`) MUST be stated explicitly in the file's
overview prose so future contributors don't conflate the two surfaces.

#### Scenario: ODB-S14 dashboard md links every SQL file by relative path

- **GIVEN** the working tree at HEAD
- **WHEN** `grep -c 'queries/sisyphus/<NN>-' observability/sisyphus-dashboard.md` is executed for each `NN` in `{01,02,...,13}`
- **THEN** every one of the 13 grep invocations MUST return ≥ 1 (each numbered SQL filename appears at least once in the index)

#### Scenario: ODB-S15 dashboard md states the M7 (5) + M14e (8) split in overview prose

- **GIVEN** `observability/sisyphus-dashboard.md` is read
- **WHEN** the file content is scanned
- **THEN** the file MUST contain the literal `5 + 8` (the canonical split announcement)
- **AND** the file MUST contain the literal substring `M7` AND the literal substring `M14e`
- **AND** the file MUST contain the literal substring `artifact_checks` (M7 source) AND at least one of `stage_runs` / `verifier_decisions` (M14e source)

#### Scenario: ODB-S16 dashboard md publishes refresh frequency for every question

- **GIVEN** `observability/sisyphus-dashboard.md` is read
- **WHEN** the dedicated refresh section is parsed
- **THEN** the file MUST contain a heading whose text begins with `## 刷新频率` (canonical Chinese refresh-section header)
- **AND** below that heading, every one of `Q1`, `Q2`, `Q3`, `Q4`, `Q5`, `Q6`, `Q7`, `Q8`, `Q9`, `Q10`, `Q11`, `Q12`, `Q13` MUST be mentioned at least once (refresh cadence is documented per question, even if grouped)
