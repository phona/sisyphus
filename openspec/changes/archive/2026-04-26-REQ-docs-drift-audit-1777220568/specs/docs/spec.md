# docs capability — drift audit (REQ-docs-drift-audit-1777220568)

## ADDED Requirements

### Requirement: Top-level docs reflect current code structure

The system SHALL keep the repository's top-level entry-point docs in sync with
the code at the same revision. Specifically, `README.md`, `CLAUDE.md`,
`docs/architecture.md`, `docs/state-machine.md`, `docs/prompts.md`,
`docs/api-tag-management-spec.md`, `docs/observability.md`, and
`observability/sisyphus-dashboard.md` MUST satisfy: every prompt template path,
action filename, checker filename, migration filename, ReqState / Event /
transition count, Metabase SQL count, and stage-flow diagram referenced in
those docs resolves to a file or symbol that exists in the repository.

#### Scenario: DOCS-S1 prompt template links resolve

- **GIVEN** the user reads `docs/prompts.md` on `main`
- **WHEN** they click any `.md.j2` link in the "Stage agent 模板" or
  "verifier-agent 模板" tables
- **THEN** the linked file exists at
  `orchestrator/src/orchestrator/prompts/<file>.md.j2` or
  `orchestrator/src/orchestrator/prompts/verifier/<file>.md.j2`

#### Scenario: DOCS-S2 action references resolve

- **GIVEN** the user reads `README.md` or `CLAUDE.md` "项目结构" section
- **WHEN** they look up any `actions/<name>.py` reference
- **THEN** the file exists in `orchestrator/src/orchestrator/actions/`, or the
  reference is removed if the action no longer exists (e.g. `fanout_specs.py`,
  `create_dev.py` cut in M16)

#### Scenario: DOCS-S3 state-machine doc enumerations match state.py

- **GIVEN** `orchestrator/src/orchestrator/state.py` lists 17 `ReqState` enum
  values and 27 `Event` enum values (CHALLENGER_RUNNING, CHALLENGER_PASS,
  CHALLENGER_FAIL added by M18)
- **WHEN** a reader compares against `docs/state-machine.md` "ReqState 枚举" and
  "Event 枚举" tables
- **THEN** every state and event in the table appears in `state.py`, every
  state and event in `state.py` appears in the table, and the count headers
  ("ReqState 枚举（N 个）" / "Event 枚举（N 个）") match the actual lengths

#### Scenario: DOCS-S4 mermaid stage diagram includes challenger

- **GIVEN** M18 inserted CHALLENGER_RUNNING between SPEC_LINT_RUNNING and
  DEV_CROSS_CHECK_RUNNING (per `state.py` TRANSITIONS table)
- **WHEN** a reader reads the mermaid stateDiagram in `docs/state-machine.md`
  or the flowchart in `docs/architecture.md` §2
- **THEN** the diagram shows `spec_lint_running --> challenger_running:
  spec-lint.pass` and `challenger_running --> dev_cross_check_running:
  challenger.pass` (and the failure edge `challenger_running --> review_running:
  challenger.fail`)

#### Scenario: DOCS-S5 metabase SQL count matches files on disk

- **GIVEN** `observability/queries/sisyphus/` contains 18 SQL files
  (`01-stuck-checks.sql` through `18-silent-pass-detector.sql`)
- **WHEN** a reader compares against the count claimed in `README.md`,
  `CLAUDE.md`, and `observability/sisyphus-dashboard.md`
- **THEN** all three docs say "18" (not "13"), and `sisyphus-dashboard.md`
  contains a section that documents Q17 (`17-dedup-retry-rate.sql`) alongside
  Q1–Q16 and Q18

#### Scenario: DOCS-S6 api-tag-management-spec describes BKD router tags

- **GIVEN** `router.py` reads BKD issue tags such as `intent:intake`,
  `intent:analyze`, `analyze`, `spec`, `challenger`, `dev`, `staging-test`,
  `pr-ci`, `accept`, `verifier`, `fixer`, `result:pass`, `result:fail`,
  `decision:<urlsafe-base64-json>`, `parent-id:<id>`, `parent:<stage>`,
  `round-N`, `target:<repo>`, `REQ-<slug>`
- **WHEN** a reader follows the `docs/api-tag-management-spec.md` link from
  `README.md` "文档索引" or `CLAUDE.md` "文档索引"
- **THEN** the doc describes those tags and where they enter the router (it
  MUST NOT be the Apifox API-endpoint label-lifecycle doc that previously
  occupied the same path)

#### Scenario: DOCS-S7 migration count and filenames match disk

- **GIVEN** `orchestrator/migrations/` holds 7 forward migrations (0001 through
  0007 with `_audit` and `_event_seen_processed_at`)
- **WHEN** a reader reads `README.md` "项目结构" or `CLAUDE.md` "项目结构" or
  `docs/observability.md` "数据模型"
- **THEN** the migration list / range stated in those docs reflects 0001–0007
  (not the older "0001 - 0005" / "0001~0005" wording)
