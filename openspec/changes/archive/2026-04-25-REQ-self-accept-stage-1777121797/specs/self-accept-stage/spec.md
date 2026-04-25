## ADDED Requirements

### Requirement: 顶层 Makefile MUST 提供 ci-accept-env-up / ci-accept-env-down target（self-dogfood）

The repo-root `Makefile` SHALL define two targets, `ci-accept-env-up` and
`ci-accept-env-down`, that conform to the integration-repo contract documented
in `docs/integration-contracts.md §2.3`. `ci-accept-env-up` MUST bring up an
ephemeral sisyphus lab via `deploy/accept-compose.yml` (docker compose),
wait for the orchestrator's `/healthz` endpoint to return HTTP 200, and print
a single JSON line as the **last non-empty stdout line** with at minimum
`endpoint` (URL reachable from inside the runner pod) and `namespace` fields.
`ci-accept-env-down` MUST be idempotent: it SHALL run `docker compose down -v`
on the same compose project and exit 0 even when the stack was never up.

#### Scenario: SDA-S1 ci-accept-env-up brings up stack and emits endpoint JSON

- **GIVEN** a runner pod with DinD started and source repo at `/workspace/source/sisyphus`
- **WHEN** `cd /workspace/source/sisyphus && SISYPHUS_NAMESPACE=accept-test make ci-accept-env-up`
- **THEN** the recipe builds the orchestrator image from `./orchestrator/`,
  runs `docker compose -p accept-test -f deploy/accept-compose.yml up -d`,
  polls `http://localhost:18000/healthz` until 200 (or fails on timeout),
  and writes `{"endpoint":"http://localhost:18000","namespace":"accept-test"}`
  as its last stdout line; exit code is 0

#### Scenario: SDA-S2 ci-accept-env-down is idempotent on missing stack

- **GIVEN** no compose project named `accept-test` is currently up
- **WHEN** `cd /workspace/source/sisyphus && SISYPHUS_NAMESPACE=accept-test make ci-accept-env-down`
- **THEN** the recipe runs `docker compose -p accept-test -f deploy/accept-compose.yml down -v`,
  exit code is 0, and no error propagates upward

### Requirement: deploy/accept-compose.yml MUST define postgres + orchestrator services

The compose file `deploy/accept-compose.yml` SHALL define exactly two services
needed for a sisyphus smoke lab: a `postgres` service (postgres:16-alpine) with
a `sisyphus` user/database and an emptied volume on each up, and an
`orchestrator` service built from the local `./orchestrator/` Dockerfile that
MUST publish container port 8000 to host port 18000 (override via
`SISYPHUS_ACCEPT_PORT` env). The orchestrator service MUST set dummy
`SISYPHUS_BKD_TOKEN` / `SISYPHUS_WEBHOOK_TOKEN` values, point
`SISYPHUS_PG_DSN` at the local postgres service, and disable in-cluster K8s
mode (`SISYPHUS_K8S_IN_CLUSTER=false`) so startup does not try to load a
kubeconfig.

#### Scenario: SDA-S3 compose stack starts and orchestrator answers /healthz

- **GIVEN** docker compose with `deploy/accept-compose.yml`
- **WHEN** `docker compose -p smoke -f deploy/accept-compose.yml up -d` runs
- **THEN** within 60 seconds `curl -sf http://localhost:18000/healthz`
  returns 200 with body `{"status":"ok"}` (orchestrator startup completed
  schema migration against the postgres service)

### Requirement: create_accept SHALL fall back to /workspace/source for single-source-repo self-host

`orchestrator/src/orchestrator/actions/create_accept.py` MUST resolve the
working directory for `make ci-accept-env-up` via a helper that first inspects
`/workspace/integration/<basename>/Makefile` (existing multi-repo behavior),
and if no such directory contains a `ci-accept-env-up:` target, falls back
to `/workspace/source/<basename>/Makefile` **only when exactly one source repo
under `/workspace/source/` carries that target**. If neither resolves, the
action MUST emit `ACCEPT_ENV_UP_FAIL` with reason `no integration dir resolvable`
and MUST NOT attempt `cd /workspace/integration/*` (which would shell-error on
empty glob). The helper SHALL be reused by
`orchestrator/src/orchestrator/actions/teardown_accept_env.py` so env-up and
env-down operate on the same resolved directory.

#### Scenario: SDA-S4 integration dir takes priority when present

- **GIVEN** `/workspace/integration/lab/Makefile` contains `ci-accept-env-up:` target
  AND `/workspace/source/sisyphus/Makefile` also contains `ci-accept-env-up:`
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `/workspace/integration/lab` (integration always wins
  when present)

#### Scenario: SDA-S5 single-source-repo with target → fall back

- **GIVEN** `/workspace/integration/` is empty AND
  `/workspace/source/sisyphus/Makefile` contains `ci-accept-env-up:` (and no other
  source repo carries the target)
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `/workspace/source/sisyphus` (self-host fallback)

#### Scenario: SDA-S6 source repo without target → no fallback

- **GIVEN** `/workspace/integration/` is empty AND `/workspace/source/foo/Makefile`
  has no `ci-accept-env-up:` target
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `None`; `create_accept` emits `accept-env-up.fail` with
  reason mentioning `no integration dir resolvable`

#### Scenario: SDA-S7 multiple source repos with target → no fallback

- **GIVEN** `/workspace/integration/` is empty AND **two** source repos
  (`/workspace/source/a` + `/workspace/source/b`) both carry
  `ci-accept-env-up:` target
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `None` (refuses to silently pick one); `create_accept`
  emits `accept-env-up.fail` with reason mentioning multiple candidates

### Requirement: skip_accept default MUST be false in deploy/my-values.yaml

The deployment values file `orchestrator/deploy/my-values.yaml` SHALL set
`env.skip_accept: false` so that REQs targeting the sisyphus repo run the
real accept stage end-to-end (env-up via compose, accept-agent against
endpoint, env-down on completion). The accompanying inline comment MUST point
to the self-host fallback rather than the historical "ttpos-arch-lab not yet
connected" placeholder.

#### Scenario: SDA-S8 skip_accept defaults to false in production deploy

- **GIVEN** `orchestrator/deploy/my-values.yaml` is the source of truth for
  `helm upgrade` on vm-node04
- **WHEN** the file is parsed
- **THEN** `env.skip_accept` is `false` and the comment references
  `deploy/accept-compose.yml` as the active accept env

### Requirement: accept-agent prompt MUST read spec.md from per-repo source path

`orchestrator/src/orchestrator/prompts/accept.md.j2` SHALL instruct the
accept-agent to read acceptance scenarios from
`/workspace/source/*/openspec/changes/<REQ>/specs/*/spec.md` (M16 multi-repo
layout), not the legacy `/workspace/openspec/...` path. The template MUST
work uniformly for single-repo (sisyphus self-host) and multi-repo REQs since
the glob iterates over all source repos.

#### Scenario: SDA-S9 accept prompt instructs glob over /workspace/source/*

- **GIVEN** the rendered accept agent prompt
- **WHEN** the agent follows Step 2 to load scenarios
- **THEN** the kubectl exec command reads
  `/workspace/source/*/openspec/changes/<REQ>/specs/*/spec.md` (the M16-correct
  per-repo layout)
