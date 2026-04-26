## MODIFIED Requirements

### Requirement: create_accept SHALL prefer /workspace/source for accept-env-up resolution and fall back to /workspace/integration only when source is unusable

`orchestrator/src/orchestrator/actions/create_accept.py` MUST resolve the
working directory for `make accept-env-up` via a helper that **first**
inspects `/workspace/source/<basename>/Makefile` (the canonical layout
produced by `scripts/sisyphus-clone-repos.sh`). When **exactly one** source
repo carries an `accept-env-up:` target, the helper SHALL pick that source
dir. The helper MUST only consult `/workspace/integration/<basename>/Makefile`
when source resolution is unusable — either no source repo carries the
target, or multiple do (in which case `/workspace/integration/*` acts as an
explicit tiebreaker the operator can stage). When neither path resolves,
the action MUST emit `ACCEPT_ENV_UP_FAIL` with reason `no integration dir
resolvable` and MUST NOT attempt `cd /workspace/integration/*` (which would
shell-error on empty glob). The helper SHALL be reused by
`orchestrator/src/orchestrator/actions/teardown_accept_env.py` so env-up
and env-down operate on the same resolved directory. The matcher MUST grep
for `^accept-env-up:` (without the `ci-` prefix) so it stays in lock-step
with the renamed Makefile recipe header.

#### Scenario: SDA-S4 single source repo with target wins over an explicit integration dir

- **GIVEN** `/workspace/integration/lab/Makefile` contains `accept-env-up:` target
  AND `/workspace/source/sisyphus/Makefile` also contains `accept-env-up:`
  (and no other source repo carries the target)
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `/workspace/source/sisyphus` (source-first: a single
  unambiguous source candidate is the canonical answer)

#### Scenario: SDA-S5 single source repo with target → use it (primary path)

- **GIVEN** `/workspace/integration/` is empty AND
  `/workspace/source/sisyphus/Makefile` contains `accept-env-up:` (and no
  other source repo carries the target)
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `/workspace/source/sisyphus`

#### Scenario: SDA-S6 source repo without target and empty integration → no fallback

- **GIVEN** `/workspace/integration/` is empty AND `/workspace/source/foo/Makefile`
  has no `accept-env-up:` target
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `None`; `create_accept` emits `accept-env-up.fail` with
  reason mentioning `no integration dir resolvable`

#### Scenario: SDA-S7 multiple source repos with target and no explicit integration → no fallback

- **GIVEN** `/workspace/integration/` is empty AND **two** source repos
  (`/workspace/source/a` + `/workspace/source/b`) both carry
  `accept-env-up:` target
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `None` (refuses to silently pick one); `create_accept`
  emits `accept-env-up.fail` with reason mentioning multiple candidates

#### Scenario: SDA-S10 multiple source repos with target + explicit integration dir → integration breaks the tie

- **GIVEN** **two** source repos (`/workspace/source/a` + `/workspace/source/b`)
  both carry `accept-env-up:` AND `/workspace/integration/lab/Makefile`
  also carries `accept-env-up:`
- **WHEN** `_resolve_integration_dir()` is called
- **THEN** it returns `/workspace/integration/lab` (explicit integration dir
  is the operator-supplied tiebreaker when source resolution is ambiguous)
