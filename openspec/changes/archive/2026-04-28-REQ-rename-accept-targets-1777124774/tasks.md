# Tasks — REQ-rename-accept-targets-1777124774

## Stage: spec / openspec change

- [x] Create `openspec/changes/REQ-rename-accept-targets-1777124774/proposal.md` (motivation + scope)
- [x] Create `openspec/changes/REQ-rename-accept-targets-1777124774/tasks.md` (this file)
- [x] Create `openspec/changes/REQ-rename-accept-targets-1777124774/specs/self-accept-stage/spec.md` with `## RENAMED Requirements` (heading flip) + `## MODIFIED Requirements` (body + scenarios SDA-S1 / SDA-S2 / SDA-S4 / SDA-S5 / SDA-S6 / SDA-S7)
- [x] `openspec validate REQ-rename-accept-targets-1777124774` passes

## Stage: implementation — sisyphus self-host code

- [x] Top-level `Makefile`: `.PHONY` + recipe headers `ci-accept-env-up` → `accept-env-up`, `ci-accept-env-down` → `accept-env-down`; comment block updated
- [x] `orchestrator/src/orchestrator/actions/create_accept.py`: command string + module docstring
- [x] `orchestrator/src/orchestrator/actions/teardown_accept_env.py`: command string + module docstring
- [x] `orchestrator/src/orchestrator/actions/_integration_resolver.py`: `grep -E '^ci-accept-env-up:'` → `^accept-env-up:`, all docstring + comment + log message references
- [x] `orchestrator/tests/test_create_accept_self_host.py`: assertion strings on the command for env-up + env-down
- [x] `scripts/sisyphus-accept-up-compose.sh` comment header
- [x] `scripts/sisyphus-accept-down-compose.sh` comment header
- [x] `deploy/accept-compose.yml` header comment block
- [x] `orchestrator/deploy/my-values.yaml` `skip_accept` comment
- [x] `orchestrator/src/orchestrator/prompts/analyze.md.j2` accept-agent bullet
- [x] `orchestrator/src/orchestrator/prompts/accept.md.j2` env-up / env-down narration

## Stage: implementation — applied spec

- [x] `openspec/specs/self-accept-stage/spec.md`: heading + body + scenarios SDA-S1 / SDA-S2 / SDA-S4..S7 use `accept-env-up` / `accept-env-down`

## Stage: implementation — docs

- [x] `docs/integration-contracts.md` §1 intro
- [x] `docs/integration-contracts.md` §2.3 Makefile target table + `cd /workspace/integration/...` invocation strings + idempotence sentence + transitional rename note
- [x] `docs/integration-contracts.md` §3 stdout JSON contract heading + body + helm template
- [x] `docs/integration-contracts.md` §4.2 helm `.PHONY` + recipe headers + heredoc / wait sleep block
- [x] `docs/integration-contracts.md` §4.2.2 docker-compose `.PHONY` + recipe headers + body
- [x] `docs/integration-contracts.md` §4.2.2 volume retention warning sentence
- [x] `docs/integration-contracts.md` §5 `SISYPHUS_STAGE` row
- [x] `docs/integration-contracts.md` §8 troubleshooting bullet 4
- [x] `docs/architecture.md` §2 happy-path mermaid (EnvUp + Teardown nodes)
- [x] `docs/architecture.md` §5 mechanical-checker row, §6 stage rows 7a / 8, §7 data-flow primitives, §8 env-var table, §13 roadmap bullet
- [x] `README.md` 当前架构 mermaid + 接入新业务 repo target table
- [x] `CLAUDE.md` Stage 流 one-liner
- [x] `orchestrator/docs/V0.2-PLAN.md` accept-lab table row + Makefile-target list + lab-接入 row
- [x] `orchestrator/docs/sisyphus-integration.md` `accept-up` / `accept-down` references in contract section

## Stage: implementation — prior in-flight change folder

- [x] `openspec/changes/REQ-accept-contract-docs-1777121224/proposal.md` body refs flipped to `accept-env-up` / `accept-env-down`
- [x] `openspec/changes/REQ-accept-contract-docs-1777121224/tasks.md` line 11 / 13 / 14 / 16 / 21 / 36 / 43 references flipped
- [x] `openspec/changes/REQ-accept-contract-docs-1777121224/specs/accept-env-target-naming/spec.md` Requirement headings + scenarios use new names

## Stage: validation

- [x] `make ci-lint` (BASE_REV=origin/main) clean
- [x] `make ci-unit-test` clean — `test_create_accept_self_host.py` passes with the renamed assertions
- [x] `make ci-integration-test` clean (or exit-5 pass)
- [x] `openspec validate REQ-rename-accept-targets-1777124774` clean
- [x] `openspec validate REQ-accept-contract-docs-1777121224` still clean after our body edits
- [x] `grep -RIn 'ci-accept-env-up\|ci-accept-env-down' .` returns only history (this proposal/tasks lineage + archive/) — no live code, doc, or applied-spec hits

## Stage: PR

- [x] `git checkout -b feat/REQ-rename-accept-targets-1777124774`
- [x] commit + push to origin
- [x] `gh pr create` titled `fix(accept naming): rename ci-accept-env-up/down → accept-env-up/down (drop ci- prefix)`
