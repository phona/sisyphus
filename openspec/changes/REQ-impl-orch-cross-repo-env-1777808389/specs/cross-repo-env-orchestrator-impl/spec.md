## ADDED Requirements

### Requirement: cross_repo_env module exposes pure manifest + topology API

The orchestrator MUST ship a new module `orchestrator.cross_repo_env` that exposes pure (no I/O) functions for parsing `.sisyphus/env.yaml`, resolving dependency topology, mapping workspace directories, and resolving cross-repo branches. The module MUST NOT import `kubernetes`, `asyncpg`, or any orchestrator I/O subsystem so unit tests can exercise it without infrastructure.

#### Scenario: OCRE-S1 parse_manifest accepts a fully populated manifest
- **GIVEN** YAML text containing `emits: [endpoint, namespace]`, `needs: [ZonEaseTech/ttpos-server-go]`, `inputs: {BACKEND_ENDPOINT: "ZonEaseTech/ttpos-server-go.endpoint"}`, `branches: {develop: develop}`
- **WHEN** `parse_manifest` is called
- **THEN** it returns a Manifest with the four fields populated and merges the missing `release: release` default into the `branches` map

#### Scenario: OCRE-S2 parse_manifest rejects inputs referencing repo not listed in needs
- **GIVEN** YAML text where `inputs` references `some-org/some-repo.field` but `needs` is absent
- **WHEN** `parse_manifest` is called
- **THEN** it raises `ManifestError` whose message names `some-org/some-repo` as undeclared

#### Scenario: OCRE-S3 parse_manifest rejects malformed needs entry
- **GIVEN** YAML text with `needs: ["not-a-valid/repo/name"]`
- **WHEN** `parse_manifest` is called
- **THEN** it raises `ManifestError` whose message names the malformed entry

#### Scenario: OCRE-S4 parse_manifest rejects invalid env var name in inputs
- **GIVEN** YAML text with `inputs: {"123INVALID": "org/repo.field"}` and `needs: [org/repo]`
- **WHEN** `parse_manifest` is called
- **THEN** it raises `ManifestError` naming the invalid env var

#### Scenario: OCRE-S5 resolve_topology orders linear chain leaves first
- **GIVEN** repos A → B → C resolved through a stub manifest_loader
- **WHEN** `resolve_topology("A", loader)` is called
- **THEN** the returned list is `["C", "B", "A"]`

#### Scenario: OCRE-S6 resolve_topology deduplicates diamond and detects cycles
- **GIVEN** A needs [B, C], both B and C need D; separately, X needs Y and Y needs X
- **WHEN** `resolve_topology` runs on each input
- **THEN** the diamond returns `["D", "B", "C", "A"]` (D once, before B and C); the cyclic input raises `TopologyError` whose message contains `X → Y → X`

---

### Requirement: workspace_dir_map handles short-name collision

The orchestrator MUST expose `workspace_dir_map(repos: Iterable[str]) -> dict[str, str]` mapping each `OWNER/REPO` to the directory basename it should be cloned to under `/workspace/source/`. When two repositories share the same `<repo_short>` portion the mapping MUST disambiguate using `<owner>__<repo_short>` (double underscore) form for ALL conflicting entries; non-conflicting entries keep the short form. Single-repo input MUST keep the unchanged short-name basename to preserve current sisyphus-clone-repos behavior.

#### Scenario: OCRE-S7 distinct short names map to short basenames
- **GIVEN** repos `[ZonEaseTech/ttpos-server-go, ZonEaseTech/ttpos-flutter]`
- **WHEN** `workspace_dir_map` is called
- **THEN** the mapping is `{ZonEaseTech/ttpos-server-go: "ttpos-server-go", ZonEaseTech/ttpos-flutter: "ttpos-flutter"}`

#### Scenario: OCRE-S8 colliding short names resolve to OWNER__REPO form
- **GIVEN** repos `[org-a/shared-lib, org-b/shared-lib]`
- **WHEN** `workspace_dir_map` is called
- **THEN** the mapping is `{org-a/shared-lib: "org-a__shared-lib", org-b/shared-lib: "org-b__shared-lib"}`

#### Scenario: OCRE-S9 single-repo input preserves short basename
- **GIVEN** repos `[phona/sisyphus]`
- **WHEN** `workspace_dir_map` is called
- **THEN** the mapping is `{phona/sisyphus: "sisyphus"}`

---

### Requirement: resolve_branch implements the 4-step branch algorithm

The orchestrator MUST expose `resolve_branch(source_branch, source_manifest, needs_repo, needs_manifest, branch_exists)` returning a `BranchResolution` describing which branch should be checked out for the needs repo. Resolution MUST follow:

1. If `branch_exists(needs_repo, source_branch)` → return `source_branch` with reason `same_name`.
2. Else, infer the source branch class by matching `source_branch` against `source_manifest.branches.values()`; if `source_branch` is a feature branch (i.e., not in the values), default the class to `develop`.
3. Look up `needs_manifest.branches[class]`; if `branch_exists(needs_repo, candidate)` → return that branch with reason `class_fallback`.
4. Else → return `BranchResolution(branch=None, reason="branch_resolution_failed", failed_class=class)`.

#### Scenario: OCRE-S10 same-name takes priority
- **GIVEN** source branch `feat/REQ-42-foo`; `branch_exists` returns True for the same name in the needs repo
- **WHEN** `resolve_branch` runs
- **THEN** it returns `branch="feat/REQ-42-foo"` with reason `same_name`

#### Scenario: OCRE-S11 class fallback resolves develop alias
- **GIVEN** source branch `feat/REQ-42-foo`; the same name does not exist in the needs repo; needs manifest declares `branches: {develop: master, release: stable}`
- **WHEN** `resolve_branch` runs
- **THEN** it returns `branch="master"` with reason `class_fallback`

#### Scenario: OCRE-S12 no match returns failure resolution
- **GIVEN** source branch `feat/REQ-42-foo`; neither same-name nor the needs repo's class alias exists
- **WHEN** `resolve_branch` runs
- **THEN** it returns `branch=None` with reason `branch_resolution_failed`

---

### Requirement: create_accept multi-layer path orchestrates topology with attribution

When the source repository carries a `.sisyphus/env.yaml` declaring at least one `needs` entry, `create_accept` MUST:

1. Load every manifest reachable through `needs` from runner-pod-cloned repositories.
2. Compute the topological order via `resolve_topology` and clone any not-yet-present needs repos onto the resolved branch (per R6).
3. For each layer in topo order, invoke `make accept-env-up` in that layer's `/workspace/source/<dir>/` with environment variables built from the accumulated upstream emit bundle.
4. Parse the JSON tail of stdout, copy each declared `emits` field into the bundle keyed by full repo name, and pass the full bundle into the accept-agent prompt.
5. On any layer failure (non-zero exit, malformed JSON, missing emit field, branch resolution failure), record `failed_layer`, optionally `failed_field`, and a `layers` list of `{repo, status, duration_ms}` entries (succeeded → success, failing → failed, untouched → skipped) into the latest open `stage_runs.context` row before emitting `ACCEPT_ENV_UP_FAIL`.

#### Scenario: OCRE-S13 single-layer source repo without manifest preserves legacy behavior
- **GIVEN** `/workspace/source/<src>/.sisyphus/env.yaml` does not exist; the existing single-layer accept path passes
- **WHEN** `create_accept` runs
- **THEN** behavior is byte-identical to pre-cross-repo path: only the source repo's `make accept-env-up` is invoked and the v0.3-lite fallback path remains reachable when `thanatos` block is absent

#### Scenario: OCRE-S14 multi-layer success records all layers and merges bundle
- **GIVEN** topology `[ttpos-server-go, ttpos-flutter]` where ttpos-server-go emits `endpoint` and ttpos-flutter declares `inputs: {BACKEND_ENDPOINT: ttpos-server-go.endpoint}`; both layers exit 0 and emit JSON containing the declared fields
- **WHEN** the multi-layer path runs
- **THEN** ttpos-flutter's `accept-env-up` invocation receives `BACKEND_ENDPOINT` set to ttpos-server-go's emitted endpoint string; the bundle passed to the accept-agent contains `ZonEaseTech/ttpos-server-go.endpoint` and any ttpos-flutter emits; `stage_runs.context.layers` records two `success` entries with non-negative `duration_ms`

#### Scenario: OCRE-S15 missing emit field records failed_field and emits ACCEPT_ENV_UP_FAIL
- **GIVEN** ttpos-server-go's `accept-env-up` exits 0 but its JSON tail omits the declared `endpoint` field
- **WHEN** the multi-layer path inspects emits
- **THEN** `stage_runs.context.failed_layer` is `ZonEaseTech/ttpos-server-go`, `stage_runs.context.failed_field` is `endpoint`, and the action emits `ACCEPT_ENV_UP_FAIL`

---

### Requirement: teardown_accept_env runs reverse-order best-effort multi-layer down

When `req_state.context.accept_layers` lists more than one layer, `teardown_accept_env` MUST iterate the list in reverse topological order, invoking `make accept-env-down` once per layer in its `/workspace/source/<dir>/`. A non-zero exit in one layer's teardown MUST NOT prevent execution of remaining layers and MUST NOT change the emitted next-event (still `TEARDOWN_DONE_PASS` / `TEARDOWN_DONE_FAIL` based on `accept_result`). When `accept_layers` is absent or contains a single layer, behavior MUST remain identical to the existing single-layer teardown path.

#### Scenario: OCRE-S16 multi-layer teardown runs reverse-order even on first failure
- **GIVEN** `accept_layers` is `["ZonEaseTech/ttpos-server-go", "ZonEaseTech/ttpos-flutter"]` (topological order, leaves first); ttpos-flutter's teardown exits non-zero
- **WHEN** `teardown_accept_env` runs
- **THEN** ttpos-flutter's `make accept-env-down` runs first, ttpos-server-go's runs second after the failure, and the action still emits the next-event derived from `accept_result`
