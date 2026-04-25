# accept-env-target-naming Specification

## ADDED Requirements

### Requirement: integration repo Makefile target 契约 MUST 用 accept-env-up / accept-env-down 名

The canonical integration contract document `docs/integration-contracts.md` SHALL
declare the integration repo's lab-up / lab-down Makefile targets as
`accept-env-up` and `accept-env-down`. The system MUST NOT publish the
legacy names `accept-up` / `accept-down` in any §1 / §2 / §3 / §4 / §5 / §8
prose, table cell, code-block target header, or `.PHONY` declaration of that
document, because `orchestrator/src/orchestrator/actions/create_accept.py` and
`orchestrator/src/orchestrator/actions/teardown_accept_env.py` both invoke the
`accept-env-up` / `accept-env-down` names — publishing the old names would
direct integrators to implement a target that sisyphus never calls.

#### Scenario: AETN-S1 contract doc §2.3 lists accept-env-up / down (renamed from accept-up / accept-down)

- **GIVEN** `docs/integration-contracts.md` is read
- **WHEN** the §2.3 integration repo Makefile target table is parsed
- **THEN** the table contains rows for `make accept-env-up` and
  `make accept-env-down`, AND no row mentions `make accept-up` or `make accept-down`

#### Scenario: AETN-S2 contract doc §3 stdout JSON section title is renamed

- **GIVEN** `docs/integration-contracts.md` is read
- **WHEN** §3's heading and body are scanned
- **THEN** the heading reads `## 3. accept-env-up 的 stdout JSON 契约` (or contains
  the literal `accept-env-up`), AND the implementation-suggestion makefile
  block declares the target as `accept-env-up:` (not `accept-up:`)

#### Scenario: AETN-S3 contract doc §4.2 helm template uses renamed targets

- **GIVEN** `docs/integration-contracts.md` §4.2 helm-based integration repo template
- **WHEN** the makefile block is parsed
- **THEN** the `.PHONY` line lists `accept-env-up accept-env-down` and
  the recipe headers are `accept-env-up:` and `accept-env-down:`

#### Scenario: AETN-S4 contract doc §5 SISYPHUS_STAGE row uses renamed target as caller

- **GIVEN** `docs/integration-contracts.md` §5 env-var table
- **WHEN** the row whose env column is `SISYPHUS_STAGE` is read
- **THEN** the "何时有" cell names `accept-env-up / accept-env-down` (or the
  individual stages), AND does not name the legacy `accept-up / accept-down`

#### Scenario: AETN-S5 contract doc §8 troubleshooting bullet 4 uses renamed target

- **GIVEN** `docs/integration-contracts.md` §8 troubleshooting checklist
- **WHEN** the 4th bullet is read
- **THEN** the bullet's title says `accept-env-up 失败` (not `accept-up 失败`)

#### Scenario: AETN-S6 grep for legacy target names returns zero hits in canonical docs

- **GIVEN** the working tree at the head of feat/REQ-accept-contract-docs-1777121224
- **WHEN** `grep -RIn 'make accept-up\|make accept-down\|^accept-up:\|^accept-down:' docs/integration-contracts.md docs/architecture.md README.md CLAUDE.md` runs
- **THEN** the command exits with code 1 (no matches)

### Requirement: integration-contracts.md MUST 提供 docker-compose-based integration repo 模板 (§4.2.2)

The document `docs/integration-contracts.md` SHALL include, alongside the
existing helm-based example in §4.2, a docker-compose-based minimal viable
template (numbered §4.2.2 or labeled "Docker Compose 例") that integrators
without a Kubernetes lab can copy verbatim. The template MUST satisfy three
constraints: (a) declare `.PHONY: accept-env-up accept-env-down`;
(b) the `accept-env-up` recipe MUST start the compose stack and emit a
single trailing JSON line containing an `endpoint` field on stdout, conforming
to §3's stdout JSON contract; (c) the `accept-env-down` recipe MUST be
idempotent (best-effort tear-down with a leading `-` or `|| true`) — these
properties match the contract that `create_accept.py` and `teardown_accept_env.py`
expect of any integration repo regardless of whether it ships helm or compose.

#### Scenario: AETN-S7 §4.2.2 declares both accept-env-up and accept-env-down as PHONY targets

- **GIVEN** `docs/integration-contracts.md` §4.2.2 docker-compose template
- **WHEN** the makefile block is parsed
- **THEN** there is a `.PHONY: accept-env-up accept-env-down` line, and
  both `accept-env-up:` and `accept-env-down:` appear as recipe headers

#### Scenario: AETN-S8 §4.2.2 accept-env-up emits trailing endpoint JSON on stdout

- **GIVEN** the §4.2.2 template's `accept-env-up` recipe
- **WHEN** the recipe body is read
- **THEN** the last command MUST `printf` (or equivalent) a JSON object containing
  the literal key `"endpoint"` followed by a newline, satisfying §3's
  "stdout 最后一行 JSON" rule

#### Scenario: AETN-S9 §4.2.2 accept-env-down is best-effort idempotent

- **GIVEN** the §4.2.2 template's `accept-env-down` recipe
- **WHEN** the recipe body is read
- **THEN** the recipe's primary teardown command is prefixed with `-` (make
  ignore-error semantics) or appended with `|| true`, so re-runs after a
  partial failure exit 0

### Requirement: docs/architecture.md MUST 跟 contract 文档同步 accept-env-up / accept-env-down 命名

The architecture document `docs/architecture.md` SHALL refer to the integration
repo lab-up / lab-down targets exclusively as `accept-env-up` / `accept-env-down`
in §2 happy-path mermaid, §5 role-division table, §6 stage table (rows 7a / 8),
§7 data-flow primitive table, §8 env-var table, and §13 evolution roadmap. The
system MUST NOT keep `accept-up` / `accept-down` strings in those sections, so
that a reader navigating from the architecture doc to a business-repo Makefile
sees a consistent name.

#### Scenario: AETN-S10 §2 mermaid EnvUp / Teardown nodes use renamed targets

- **GIVEN** `docs/architecture.md` §2 happy-path mermaid block
- **WHEN** the EnvUp and Teardown node labels are inspected
- **THEN** they contain `make accept-env-up` and `make accept-env-down` respectively,
  not the legacy names

#### Scenario: AETN-S11 §6 stage table rows 7a and 8 use renamed targets

- **GIVEN** `docs/architecture.md` §6 stage table
- **WHEN** rows 7a (accept env-up) and 8 (teardown) are read
- **THEN** the "产物 / 副作用" column names `make accept-env-up` and
  `make accept-env-down` respectively

#### Scenario: AETN-S12 §13 roadmap entry mentions renamed targets

- **GIVEN** `docs/architecture.md` §13 evolution roadmap
- **WHEN** the "接 ttpos-arch-lab 真 e2e" bullet is read
- **THEN** the bullet says `accept-env-up / accept-env-down 落到生产 lab`,
  not the legacy names

### Requirement: README.md 与 CLAUDE.md MUST 同步 accept-env-up / accept-env-down 命名

The repo entry-point documents `README.md` and `CLAUDE.md` SHALL refer to the
integration repo lab-up / lab-down targets as `accept-env-up` / `accept-env-down`.
For `README.md` this MUST be reflected in the "当前架构" mermaid diagram and the
"接入新业务 repo" minimum-target table. For `CLAUDE.md` this MUST be reflected
in the "Stage 流" one-liner. The system MUST NOT keep the legacy `accept-up` /
`accept-down` strings in either file because both are read by humans and AI
agents at session start and are the primary onboarding surface.

#### Scenario: AETN-S13 README.md mermaid uses renamed targets

- **GIVEN** `README.md` §"当前架构" mermaid block
- **WHEN** the lines mentioning lab-up and teardown are inspected
- **THEN** they contain `make accept-env-up` and `make accept-env-down`
  respectively

#### Scenario: AETN-S14 README.md "接入新业务 repo" target table uses renamed targets

- **GIVEN** `README.md` §"接入新业务 repo" minimum-target table
- **WHEN** the rows are scanned
- **THEN** there are rows for `make accept-env-up` and `make accept-env-down`,
  AND no row says `make accept-up` or `make accept-down`

#### Scenario: AETN-S15 CLAUDE.md "Stage 流" one-liner uses renamed targets

- **GIVEN** `CLAUDE.md` "## Stage 流" code block
- **WHEN** the line describing the accept stage is read
- **THEN** the line names `make accept-env-up` and `make accept-env-down`,
  not the legacy names
