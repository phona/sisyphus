# self-accept-stage delta — REQ-rename-accept-targets-1777124774

## RENAMED Requirements

- FROM: `### Requirement: 顶层 Makefile MUST 提供 ci-accept-env-up / ci-accept-env-down target（self-dogfood）`
- TO: `### Requirement: 顶层 Makefile MUST 提供 accept-env-up / accept-env-down target（self-dogfood）`

## MODIFIED Requirements

### Requirement: 顶层 Makefile MUST 提供 accept-env-up / accept-env-down target（self-dogfood）

The repo-root `Makefile` SHALL define two targets, `accept-env-up` and
`accept-env-down`, that conform to the integration-repo contract documented
in `docs/integration-contracts.md §2.3`. `accept-env-up` MUST bring up an
ephemeral sisyphus lab via `deploy/accept-compose.yml` (docker compose),
wait for the orchestrator's `/healthz` endpoint to return HTTP 200, and print
a single JSON line as the **last non-empty stdout line** with at minimum
`endpoint` (URL reachable from inside the runner pod) and `namespace` fields.
`accept-env-down` MUST be idempotent: it SHALL run `docker compose down -v`
on the same compose project and exit 0 even when the stack was never up.
The legacy `ci-accept-env-up` / `ci-accept-env-down` names from
REQ-accept-contract-docs-1777121224 MUST NOT appear as live recipe headers
or `.PHONY` entries in the repo-root Makefile.

#### Scenario: SDA-S1 accept-env-up brings up stack and emits endpoint JSON

- **GIVEN** a runner pod with DinD started and source repo at `/workspace/source/sisyphus`
- **WHEN** `cd /workspace/source/sisyphus && SISYPHUS_NAMESPACE=accept-test make accept-env-up`
- **THEN** the recipe builds the orchestrator image from `./orchestrator/`,
  runs `docker compose -p accept-test -f deploy/accept-compose.yml up -d`,
  polls `http://localhost:18000/healthz` until 200 (or fails on timeout),
  and writes `{"endpoint":"http://localhost:18000","namespace":"accept-test"}`
  as its last stdout line; exit code is 0

#### Scenario: SDA-S2 accept-env-down is idempotent on missing stack

- **GIVEN** no compose project named `accept-test` is currently up
- **WHEN** `cd /workspace/source/sisyphus && SISYPHUS_NAMESPACE=accept-test make accept-env-down`
- **THEN** the recipe runs `docker compose -p accept-test -f deploy/accept-compose.yml down -v`,
  exit code is 0, and no error propagates upward

### Requirement: create_accept SHALL fall back to /workspace/source for single-source-repo self-host

`orchestrator/src/orchestrator/actions/create_accept.py` MUST resolve the
working directory for `make accept-env-up` via a helper that first inspects
`/workspace/integration/<basename>/Makefile` (existing multi-repo behavior),
and if no such directory contains an `accept-env-up:` target, falls back
to `/workspace/source/<basename>/Makefile` **only when exactly one source repo
under `/workspace/source/` carries that target**. If neither resolves, the
action MUST emit `ACCEPT_ENV_UP_FAIL` with reason `no integration dir resolvable`
and MUST NOT attempt `cd /workspace/integration/*` (which would shell-error on
empty glob). The helper SHALL be reused by
`orchestrator/src/orchestrator/actions/teardown_accept_env.py` so env-up and
env-down operate on the same resolved directory. The matcher MUST grep for
`^accept-env-up:` (without the `ci-` prefix) so it stays in lock-step with
the renamed Makefile recipe header.

#### Scenario: SDA-S4 integration dir takes priority when present

- **GIVEN** `/workspace/integration/lab/Makefile` contains `accept-env-up:` target
  AND `/workspace/source/sisyphus/Makefile` also contains `accept-env-up:`
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `/workspace/integration/lab` (integration always wins
  when present)

#### Scenario: SDA-S5 single-source-repo with target → fall back

- **GIVEN** `/workspace/integration/` is empty AND
  `/workspace/source/sisyphus/Makefile` contains `accept-env-up:` (and no other
  source repo carries the target)
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `/workspace/source/sisyphus` (self-host fallback)

#### Scenario: SDA-S6 source repo without target → no fallback

- **GIVEN** `/workspace/integration/` is empty AND `/workspace/source/foo/Makefile`
  has no `accept-env-up:` target
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `None`; `create_accept` emits `accept-env-up.fail` with
  reason mentioning `no integration dir resolvable`

#### Scenario: SDA-S7 multiple source repos with target → no fallback

- **GIVEN** `/workspace/integration/` is empty AND **two** source repos
  (`/workspace/source/a` + `/workspace/source/b`) both carry
  `accept-env-up:` target
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `None` (refuses to silently pick one); `create_accept`
  emits `accept-env-up.fail` with reason mentioning multiple candidates
