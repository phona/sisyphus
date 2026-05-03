# feat-cross-repo-env-orchestration Specification

## Purpose
Cross-repo env orchestration via `.sisyphus/env.yaml`: each repository declares which env layers it owns, what it emits, and what it needs from upstream repos. Sisyphus orchestrates multi-layer accept-env-up/down in topological order within a single runner pod. Resolves #311 #326 #327 #333.

## Requirements

### Requirement: R1 .sisyphus/env.yaml manifest schema validation

Each repository that participates in cross-repo env orchestration SHALL declare a `.sisyphus/env.yaml` file at its root. The orchestrator MUST validate this file against the following schema before any topology resolution:

- `emits` (optional, list of strings): field names that `accept-env-up` JSON output exposes to downstream consumers. Each entry MUST be a non-empty string.
- `needs` (optional, list of strings): upstream repository full names (`OWNER/REPO`) that must be brought up before this layer. Each entry MUST match the pattern `[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+`.
- `inputs` (optional, map string→string): env var name to `OWNER/REPO.field` reference. The left-hand side MUST be a valid shell variable name; the right-hand side MUST reference a repo listed in `needs` and a field that repo lists in its `emits`.
- `branches` (optional, map string→string): base-branch name aliases. If omitted, defaults to `{develop: develop, release: release}`. Keys and values MUST be non-empty strings.

A manifest with `inputs` referencing a field from a repo not listed in `needs` MUST be rejected. A manifest with both `emits` and `needs` absent is valid (emit-only or consume-only layering).

#### Scenario: CREO-S1 valid manifest with emits and needs passes validation
- **GIVEN** `.sisyphus/env.yaml` containing `emits: [endpoint, namespace]`, `needs: [ZonEaseTech/ttpos-server-go]`, and `inputs: {BACKEND_ENDPOINT: "ZonEaseTech/ttpos-server-go.endpoint"}`
- **WHEN** the manifest validator runs
- **THEN** validation passes with no errors

#### Scenario: CREO-S2 manifest with inputs referencing undeclared needs is rejected
- **GIVEN** `.sisyphus/env.yaml` containing `inputs: {FOO: "some-org/some-repo.field"}` but `needs` is absent
- **WHEN** the manifest validator runs
- **THEN** validation fails with an error identifying `some-org/some-repo` as undeclared in `needs`

#### Scenario: CREO-S3 manifest with invalid repo name format is rejected
- **GIVEN** `.sisyphus/env.yaml` containing `needs: ["not-a-valid/repo/name"]`
- **WHEN** the manifest validator runs
- **THEN** validation fails with an error identifying the malformed entry

#### Scenario: CREO-S4 manifest with invalid shell var name in inputs is rejected
- **GIVEN** `.sisyphus/env.yaml` containing `inputs: {"123INVALID": "org/repo.field"}` and `needs: [org/repo]`
- **WHEN** the manifest validator runs
- **THEN** validation fails with an error identifying the invalid env var name

#### Scenario: CREO-S5 manifest with emits only is valid
- **GIVEN** `.sisyphus/env.yaml` containing only `emits: [endpoint]`
- **WHEN** the manifest validator runs
- **THEN** validation passes with no errors

---

### Requirement: R2 Dependency topology resolution

The orchestrator SHALL resolve the full dependency graph starting from the source REQ repository. It MUST recursively fetch `.sisyphus/env.yaml` for each repository reachable via `needs` links, detect cycles, and produce a topologically sorted list of `(repo, branch)` pairs. A cycle in the dependency graph MUST cause the REQ to fail with a descriptive error naming the cycle. A repository appearing in multiple `needs` declarations MUST be included only once in the sorted output (earliest position in topological order).

#### Scenario: CREO-S6 linear chain resolves in correct topo order
- **GIVEN** repo A needs repo B, repo B needs repo C, all have valid manifests
- **WHEN** topology resolution runs starting from A
- **THEN** the resolved order is [C, B, A] (leaves first)

#### Scenario: CREO-S7 diamond dependency deduplicates shared repo
- **GIVEN** repo A needs [B, C], both B and C need D, all have valid manifests
- **WHEN** topology resolution runs starting from A
- **THEN** D appears exactly once in the resolved order, before both B and C

#### Scenario: CREO-S8 cycle in needs graph causes fail-loud error
- **GIVEN** repo A needs B and repo B needs A
- **WHEN** topology resolution runs starting from A
- **THEN** resolution fails with an error that names the cycle (e.g., "A → B → A")

#### Scenario: CREO-S9 source repo with no manifest resolves to single-layer list
- **GIVEN** the source REQ repository has no `.sisyphus/env.yaml`
- **WHEN** topology resolution runs
- **THEN** the resolved order contains only the source repo (backward-compat single-layer)

#### Scenario: CREO-S10 needs repo with no manifest is treated as leaf with no emits
- **GIVEN** repo A needs repo B; repo B has no `.sisyphus/env.yaml`
- **WHEN** topology resolution runs
- **THEN** B is included as a leaf node with an empty emits list, and A's inputs referencing B fields cause a validation error

---

### Requirement: R3 Runner pod multi-clone workspace layout

When an accept-env involves multiple repositories, sisyphus SHALL run a single runner pod for the entire accept stage. The pod MUST clone each repository in the topology into a dedicated subdirectory of `/workspace/`:

```
/workspace/<source_repo_short>/
/workspace/<needs_repo_1_short>/
/workspace/<needs_repo_2_short>/
```

`<repo_short>` is the repository name component (the part after `/` in `OWNER/REPO`). If two repositories share a short name, sisyphus MUST use the full `OWNER__REPO` form (double underscore) to avoid collision. Each clone MUST check out the branch resolved by R6.

#### Scenario: CREO-S11 two repos clone to separate subdirectories
- **GIVEN** the topology includes ZonEaseTech/ttpos-server-go and ZonEaseTech/ttpos-flutter
- **WHEN** the runner pod starts and clones all repos
- **THEN** `/workspace/ttpos-server-go/` and `/workspace/ttpos-flutter/` both exist with their respective code

#### Scenario: CREO-S12 short-name collision triggers OWNER__REPO form
- **GIVEN** the topology includes org-a/shared-lib and org-b/shared-lib
- **WHEN** the runner pod clones all repos
- **THEN** directories are `/workspace/org-a__shared-lib/` and `/workspace/org-b__shared-lib/`

#### Scenario: CREO-S13 single-repo REQ still uses short name subdirectory
- **GIVEN** the topology contains only one repository (no `needs`)
- **WHEN** the runner pod clones the repo
- **THEN** the working directory is `/workspace/<source_repo_short>/` (unchanged from current behavior)

---

### Requirement: R4 Sequential accept-env-up with env var injection

The orchestrator SHALL run `make accept-env-up` for each repository in topological order (leaves first). Before invoking each layer's `make`, it MUST inject the env vars declared in that layer's `inputs` map, resolved from the accumulated endpoint bundle of already-completed upstream layers. The JSON output of each layer's `accept-env-up` MUST be parsed; fields listed in that layer's `emits` MUST be extracted and merged into the running endpoint bundle. The accumulated bundle MUST be passed in full to the accept-agent at the end.

#### Scenario: CREO-S14 backend layer runs first and its endpoint is passed to mobile layer
- **GIVEN** topology [ttpos-server-go, ttpos-flutter]; server-go emits `endpoint`; flutter needs `BACKEND_ENDPOINT=ttpos-server-go.endpoint`
- **WHEN** sequential accept-env-up runs
- **THEN** `make accept-env-up` for ttpos-server-go runs first without injected vars; its `endpoint` value is injected as `BACKEND_ENDPOINT` when running ttpos-flutter's `make accept-env-up`

#### Scenario: CREO-S15 emits fields are extracted from accept-env-up JSON output
- **GIVEN** ttpos-server-go's `accept-env-up` outputs `{"endpoint": "http://svc:8080", "namespace": "ns-abc", "extra": "ignored"}`; manifest emits: [endpoint, namespace]
- **WHEN** the orchestrator processes the output
- **THEN** the endpoint bundle contains `{"ZonEaseTech/ttpos-server-go": {"endpoint": "http://svc:8080", "namespace": "ns-abc"}}` and `extra` is discarded

#### Scenario: CREO-S16 emitted field missing from accept-env-up JSON causes fail
- **GIVEN** manifest declares `emits: [endpoint]`; `accept-env-up` JSON output does not contain key `endpoint`
- **WHEN** the orchestrator parses the output
- **THEN** the accept stage fails with an error identifying the missing field and the layer name

#### Scenario: CREO-S17 accept-agent receives full accumulated endpoint bundle
- **GIVEN** topology [server-go, flutter]; both layers succeed and emit their fields
- **WHEN** accept-env-up completes for all layers
- **THEN** the BKD accept-agent issue is created with an endpoint bundle containing fields from all layers merged into a single JSON object

---

### Requirement: R5 Endpoint JSON field passthrough

The orchestrator SHALL NOT impose any schema or format constraints on the values of endpoint fields. It MUST treat all emitted field values as opaque JSON values and pass them through unchanged. Format interpretation (e.g., whether a field is an HTTP URL or an ADB host:port) is the contract between the provider layer and the consumer layer or the accept-agent. The orchestrator MUST NOT attempt to parse, normalize, or validate field values beyond JSON type preservation.

#### Scenario: CREO-S18 HTTP URL endpoint is passed unchanged
- **GIVEN** server-go emits `endpoint: "http://ttpos-server-go.ns-abc.svc.cluster.local:8080"`
- **WHEN** the endpoint bundle is assembled
- **THEN** the accept-agent receives the field value `"http://ttpos-server-go.ns-abc.svc.cluster.local:8080"` byte-for-byte

#### Scenario: CREO-S19 ADB host:port endpoint is passed unchanged
- **GIVEN** mobile layer emits `device: "redroid-pod-x:5554"`
- **WHEN** the endpoint bundle is assembled
- **THEN** the accept-agent receives `"redroid-pod-x:5554"` unchanged, without conversion to a URL form

#### Scenario: CREO-S20 numeric and boolean JSON field values are preserved
- **GIVEN** a layer emits `port: 8080` (JSON number) and `tls: false` (JSON boolean)
- **WHEN** the endpoint bundle is assembled
- **THEN** the accept-agent receives the fields as native JSON number and boolean, not as strings

---

### Requirement: R6 Cross-repo branch resolution

For each repository in the `needs` graph, the orchestrator SHALL resolve which branch to check out using the following algorithm in order:

1. If a branch with the same name as the source REQ branch exists in the needs repo → check out that branch (collaborative change scenario).
2. Else: determine the base branch **class** of the source REQ branch by matching against the source repo's `branches` map (e.g., if source base is `develop`, class is `develop`).
3. Use the same class key to look up the branch name in the needs repo's `branches` map → check out that branch.
4. If neither step 1 nor step 3 yields an existing branch → fail-loud: emit `ACCEPT_ENV_UP_FAIL`, record the resolution failure in `stage_runs.context`, and escalate the REQ.

The `branches` map defaults to `{develop: develop, release: release}` when absent.

#### Scenario: CREO-S21 same-branch-name takes priority over class mapping
- **GIVEN** source REQ is on branch `feat/REQ-42-foo`; needs repo ttpos-server-go also has branch `feat/REQ-42-foo`
- **WHEN** branch resolution runs for ttpos-server-go
- **THEN** branch `feat/REQ-42-foo` is checked out in the needs repo

#### Scenario: CREO-S22 class-based fallback resolves develop-class branch
- **GIVEN** source REQ is on branch `feat/REQ-42-foo` whose base is `develop`; needs repo has no `feat/REQ-42-foo`; needs repo has `branches: {develop: develop, release: release}`
- **WHEN** branch resolution runs
- **THEN** `develop` branch of the needs repo is checked out

#### Scenario: CREO-S23 custom branch alias resolves correctly
- **GIVEN** source REQ base is `main`; source repo `branches: {develop: main, release: release}`; needs repo `branches: {develop: master, release: stable}`
- **WHEN** branch resolution runs for the needs repo
- **THEN** `master` branch of the needs repo is checked out

#### Scenario: CREO-S24 no matching branch triggers fail-loud escalation
- **GIVEN** source REQ is on a branch whose base class is `develop`; needs repo has no branch matching the same name or the develop-class mapping
- **WHEN** branch resolution runs
- **THEN** `ACCEPT_ENV_UP_FAIL` is emitted with context `{failed_layer: "<needs-repo>", reason: "branch_resolution_failed"}`; REQ is escalated

#### Scenario: CREO-S25 missing branches key uses default mapping
- **GIVEN** needs repo `.sisyphus/env.yaml` has no `branches` key; source REQ base is `develop`
- **WHEN** branch resolution runs
- **THEN** the default mapping `{develop: develop, release: release}` is applied and `develop` is checked out

---

### Requirement: R7 Best-effort reverse teardown

The orchestrator SHALL run `make accept-env-down` for each layer in reverse topological order (source repo first, leaves last) after the accept stage completes (whether pass or fail). Teardown MUST be best-effort: a failure in one layer's teardown MUST NOT prevent teardown of remaining layers. All teardown outcomes (success or failure per layer) MUST be logged. A teardown failure MUST NOT emit `ACCEPT_ENV_UP_FAIL` or alter the accept stage result.

#### Scenario: CREO-S26 teardown runs in reverse topo order
- **GIVEN** topology [server-go, flutter] (server-go is leaf); accept stage completed
- **WHEN** teardown begins
- **THEN** `make accept-env-down` for ttpos-flutter runs first, then for ttpos-server-go

#### Scenario: CREO-S27 teardown failure in one layer does not block remaining layers
- **GIVEN** accept stage completed; flutter teardown exits non-zero
- **WHEN** teardown processes all layers
- **THEN** server-go teardown still runs; failure is logged; accept stage result is unaffected

#### Scenario: CREO-S28 teardown runs even when accept stage itself failed
- **GIVEN** accept-env-up for flutter failed mid-way (server-go was already up)
- **WHEN** failure handling triggers cleanup
- **THEN** `make accept-env-down` runs for all layers that were successfully started, in reverse order

---

### Requirement: R8 Backward compatibility degradation for repos without manifest

When the source REQ repository has no `.sisyphus/env.yaml` file, the orchestrator SHALL behave identically to the current single-layer accept model: clone only the source repo, run `make accept-env-up` once, parse the endpoint JSON, and pass the result to the accept-agent. No topology resolution is performed. No changes to orchestrator behavior are observable for these repos.

#### Scenario: CREO-S29 source repo without manifest uses legacy single-layer path
- **GIVEN** source REQ repository has no `.sisyphus/env.yaml`
- **WHEN** the accept stage starts
- **THEN** only the source repository is cloned; `make accept-env-up` is run once; behavior is identical to pre-cross-repo model

#### Scenario: CREO-S30 source repo with empty manifest (no needs) uses single-layer path
- **GIVEN** `.sisyphus/env.yaml` exists but contains only `emits: [endpoint]` with no `needs`
- **WHEN** the accept stage starts
- **THEN** only the source repository is cloned and run; no additional repositories are fetched

#### Scenario: CREO-S31 existing REQs in flight are unaffected after deployment
- **GIVEN** an in-progress REQ was dispatched before cross-repo support was deployed; its source repo has no manifest
- **WHEN** the accept stage runs post-deployment
- **THEN** behavior is identical to pre-deployment (single-layer path); no errors are introduced

---

### Requirement: R9 thanatos scenario path fallback

The thanatos MCP skill loader SHALL support a two-step fallback when resolving `skill_path` for a repository:

1. Check `.sisyphus/scenarios/` — if the directory exists and contains scenario files, use it.
2. If `.sisyphus/scenarios/` does not exist or is empty, fall back to `.thanatos/` (existing location).

This MUST be transparent to the accept-agent and thanatos runner: neither needs to know which path was used. Repositories with `.thanatos/` only continue to work without modification. New repositories SHOULD place scenarios under `.sisyphus/scenarios/`.

#### Scenario: CREO-S32 .sisyphus/scenarios/ takes priority over .thanatos/
- **GIVEN** a repository has both `.sisyphus/scenarios/feature.yaml` and `.thanatos/feature.yaml`
- **WHEN** the thanatos skill loader resolves skill_path
- **THEN** `.sisyphus/scenarios/feature.yaml` is used

#### Scenario: CREO-S33 fallback to .thanatos/ when .sisyphus/scenarios/ absent
- **GIVEN** a repository has `.thanatos/feature.yaml` but no `.sisyphus/scenarios/` directory
- **WHEN** the thanatos skill loader resolves skill_path
- **THEN** `.thanatos/feature.yaml` is used

#### Scenario: CREO-S34 fallback to .thanatos/ when .sisyphus/scenarios/ is empty
- **GIVEN** a repository has an empty `.sisyphus/scenarios/` directory and `.thanatos/feature.yaml`
- **WHEN** the thanatos skill loader resolves skill_path
- **THEN** `.thanatos/feature.yaml` is used

#### Scenario: CREO-S35 neither path exists returns appropriate error
- **GIVEN** a repository has neither `.sisyphus/scenarios/` nor `.thanatos/`
- **WHEN** the thanatos skill loader resolves skill_path
- **THEN** the loader returns an error indicating no scenario path found (same error as today when `.thanatos/` is missing)

---

### Requirement: R10 Failure layer attribution in stage_runs context

When any layer's `accept-env-up` fails, the orchestrator SHALL record the failing layer's repository full name in `stage_runs.context` under the key `failed_layer`. If the failure is a missing emitted field, the key `failed_field` MUST also be recorded. All layer execution results (layer name, duration in milliseconds, status: `success` | `failed` | `skipped`) MUST be recorded in `stage_runs.context` under `layers` as an ordered list. This data MUST be written before emitting `ACCEPT_ENV_UP_FAIL`.

#### Scenario: CREO-S36 failed layer name appears in stage_runs context
- **GIVEN** topology [server-go, flutter]; server-go succeeds; flutter's `accept-env-up` exits non-zero
- **WHEN** `ACCEPT_ENV_UP_FAIL` is emitted
- **THEN** `stage_runs.context.failed_layer` equals `"ZonEaseTech/ttpos-flutter"`

#### Scenario: CREO-S37 all layer outcomes are recorded even after mid-chain failure
- **GIVEN** topology [C, B, A]; B fails
- **WHEN** the accept stage records context
- **THEN** `stage_runs.context.layers` is `[{repo: C, status: success, duration_ms: N}, {repo: B, status: failed, duration_ms: M}, {repo: A, status: skipped, duration_ms: 0}]`

#### Scenario: CREO-S38 missing emits field records failed_field in context
- **GIVEN** server-go's `accept-env-up` output is missing the declared `endpoint` field
- **WHEN** the orchestrator detects the missing field
- **THEN** `stage_runs.context.failed_layer` is `"ZonEaseTech/ttpos-server-go"` and `stage_runs.context.failed_field` is `"endpoint"`

#### Scenario: CREO-S39 successful multi-layer accept records all layers as success
- **GIVEN** topology [server-go, flutter]; both layers succeed
- **WHEN** the accept stage completes
- **THEN** `stage_runs.context.layers` contains two entries both with `status: success` and non-zero `duration_ms`

---

## Readiness Gate

Implementation PRs for this spec MUST satisfy all of the following before merge:

- [ ] **R1 unit tests**: manifest schema validator covers valid manifests, missing `needs` with `inputs` references, invalid repo name format, invalid shell var name, and emits-only manifest.
- [ ] **R2 unit tests**: topology resolver covers linear chain, diamond deduplication, cycle detection (naming the cycle in the error), single-repo no-manifest, and needs-repo with no manifest treated as leaf.
- [ ] **R3 unit tests**: workspace layout logic covers two repos with distinct short names and two repos with colliding short names (OWNER__REPO form).
- [ ] **R4 unit tests**: sequential accept-env-up covers correct env var injection from upstream emits, missing emitted field error, and full bundle assembly for accept-agent.
- [ ] **R6 unit tests**: branch resolution covers same-name priority, class-based fallback (default and custom aliases), and fail-loud when no branch matches.
- [ ] **R8 unit tests**: backward-compat path (no manifest) produces identical behavior to pre-cross-repo single-layer accept for all existing test fixtures.
- [ ] **R9 unit tests**: thanatos fallback reader covers `.sisyphus/scenarios/` priority, fallback to `.thanatos/`, empty `.sisyphus/scenarios/` fallback, and neither-exists error.
- [ ] **R10 unit tests**: `stage_runs.context` structure covers mid-chain failure with `failed_layer`, missing-field failure with `failed_field`, and full-success all-layers recording.
- [ ] **End-to-end dogfood**: at least one live REQ using `ttpos-flutter` (which needs `ttpos-server-go`) runs the full accept stage with both layers brought up and at least one thanatos scenario passing.
- [ ] **No production regression**: existing single-layer REQs (repos without `.sisyphus/env.yaml`) pass CI unchanged.

## Side Effects

Merging this spec and its implementation REQ is expected to resolve or materially mitigate the following issues:

| Issue | How resolved |
|-------|--------------|
| #326 endpoint format mismatch (mobile gets HTTP URL instead of ADB host:port) | R5: endpoint passthrough with no normalization; mobile emits `device: "host:port"` directly |
| #311 accept-env-up missing backend (ttpos-flutter Makefile never installed server-go) | R4: server-go is a separate layer, orchestrated by sisyphus before flutter layer |
| #327 chart sub-chart assumption (runner tries to deploy sub-charts via flutter Makefile) | R3+R4: each repo's Makefile only manages its own layer; no cross-repo chart assumptions |
| #333 three-party contract missing (sisyphus ↔ mobile ↔ backend) | R1+R5: `.sisyphus/env.yaml` is the machine-readable three-party contract; `emits`/`inputs` are explicit |
