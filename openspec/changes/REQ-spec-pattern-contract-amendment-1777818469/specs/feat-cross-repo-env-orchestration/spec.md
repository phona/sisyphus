# feat-cross-repo-env-orchestration: endpoint pattern contract amendment

> Amendment to capability `feat-cross-repo-env-orchestration` (originally landed via
> spec PR #342, impl PR #354). Replaces the runtime-only "layer emit真值" semantics with
> a pre-resolved pattern contract; preserves the bare-string emit form for backward
> compatibility. Implementation is deferred to a follow-up REQ; this PR is spec-only.

## MODIFIED Requirements

### Requirement: R1 .sisyphus/env.yaml manifest schema validation

Each repository that participates in cross-repo env orchestration SHALL declare a `.sisyphus/env.yaml` file at its root. The orchestrator MUST validate this file against the following schema before any topology resolution:

- `emits` (optional, list): each entry MUST be one of two admissible forms.
  - **Bare-string form** (legacy, retained for backward compatibility): a non-empty string field name. Values for these fields are sourced at layer runtime from the JSON output of `make accept-env-up` (R4).
  - **Pattern form** (new): a single-key object where the key is the field name and the value is a map with two required sub-keys:
    - `pattern` (string, required): a template string containing zero or more `{VAR_NAME}` placeholders. `VAR_NAME` MUST match `[A-Z_][A-Z0-9_]*`.
    - `vars` (map, required): each placeholder used in `pattern` MUST be a key of `vars`. Values MUST be one of: a literal string, or a reference of the form `${SISYPHUS_*}` (an orchestrator-provided REQ-context variable; see R12). Unresolved or undeclared placeholders MUST be rejected at validation time.
  Mixing bare-string and pattern entries in the same `emits` list MUST be permitted.
- `needs` (optional, list of strings): upstream repository full names (`OWNER/REPO`) that must be brought up before this layer. Each entry MUST match the pattern `[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+`.
- `inputs` (optional, map string→string): env var name to `OWNER/REPO.field` reference. The left-hand side MUST be a valid shell variable name; the right-hand side MUST reference a repo listed in `needs` and a field that repo lists in its `emits` (in either form).
- `branches` (optional, map string→string): base-branch name aliases. If omitted, defaults to `{develop: develop, release: release}`. Keys and values MUST be non-empty strings.

A manifest with `inputs` referencing a field from a repo not listed in `needs` MUST be rejected. A manifest with both `emits` and `needs` absent is valid (emit-only or consume-only layering). A pattern-form `emits` entry whose `pattern` references a placeholder not declared in its `vars` MUST be rejected.

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

#### Scenario: EPCA-S1 pattern-form emit with literal and SISYPHUS var passes validation
- **GIVEN** `.sisyphus/env.yaml` containing
  ```yaml
  emits:
    - endpoint:
        pattern: "ttpos-server-go.{NAMESPACE}.svc.cluster.local:{PORT}"
        vars:
          NAMESPACE: "${SISYPHUS_NAMESPACE}"
          PORT: "8080"
  ```
- **WHEN** the manifest validator runs
- **THEN** validation passes with no errors and the entry is recorded as a pattern-form emit with field name `endpoint`

#### Scenario: EPCA-S2 mixed bare-string and pattern emits in one list is valid
- **GIVEN** `.sisyphus/env.yaml` containing `emits: [namespace, {endpoint: {pattern: "svc.{NS}:8080", vars: {NS: "${SISYPHUS_NAMESPACE}"}}}]`
- **WHEN** the manifest validator runs
- **THEN** validation passes; the validator MUST record `namespace` as a bare-string emit and `endpoint` as a pattern-form emit

#### Scenario: EPCA-S3 pattern referencing undeclared placeholder is rejected
- **GIVEN** `.sisyphus/env.yaml` containing
  ```yaml
  emits:
    - endpoint:
        pattern: "svc.{NS}.local:{PORT}"
        vars:
          NS: "${SISYPHUS_NAMESPACE}"
  ```
  (`PORT` is used in `pattern` but absent from `vars`)
- **WHEN** the manifest validator runs
- **THEN** validation fails with an error identifying `PORT` as an undeclared placeholder in the `endpoint` pattern

---

### Requirement: R4 Sequential accept-env-up with env var injection

The orchestrator SHALL run `make accept-env-up` for each repository in topological order (leaves first). Before invoking each layer's `make`, it MUST inject the env vars declared in that layer's `inputs` map, resolved from the **endpoint bundle** (see below). After the layer's `accept-env-up` completes, the orchestrator MUST update the bundle with values for that layer's emits using the source rules:

- For **pattern-form emits** (R1): the field's value is taken from the pre-resolved bundle assembled in R12 **before** the layer ran. The orchestrator MUST NOT re-resolve from the layer's runtime output.
- For **bare-string emits** (legacy): the orchestrator MUST parse the JSON output of the layer's `accept-env-up`, extract each declared field, and merge it into the bundle keyed by `OWNER/REPO`. Fields not listed in `emits` MUST be discarded.

The accumulated bundle MUST be passed in full to the accept-agent at the end. When a layer's `inputs` reference an upstream pattern-form emit, the value is already in the bundle from R12 and is injected immediately — including before the upstream layer's own `accept-env-up` has run, when consumers (e.g. APK build) need the value early.

#### Scenario: CREO-S14 backend layer runs first and its endpoint is passed to mobile layer
- **GIVEN** topology [ttpos-server-go, ttpos-flutter]; server-go emits `endpoint` (bare-string); flutter needs `BACKEND_ENDPOINT=ttpos-server-go.endpoint`
- **WHEN** sequential accept-env-up runs
- **THEN** `make accept-env-up` for ttpos-server-go runs first without injected vars; its `endpoint` value parsed from `accept-env-up` JSON output is injected as `BACKEND_ENDPOINT` when running ttpos-flutter's `make accept-env-up`

#### Scenario: CREO-S15 bare-string emits fields are extracted from accept-env-up JSON output
- **GIVEN** ttpos-server-go's `accept-env-up` outputs `{"endpoint": "http://svc:8080", "namespace": "ns-abc", "extra": "ignored"}`; manifest emits: `[endpoint, namespace]` (both bare-string)
- **WHEN** the orchestrator processes the output
- **THEN** the endpoint bundle contains `{"ZonEaseTech/ttpos-server-go": {"endpoint": "http://svc:8080", "namespace": "ns-abc"}}` and `extra` is discarded

#### Scenario: CREO-S16 missing bare-string emit field in accept-env-up JSON causes fail
- **GIVEN** manifest declares `emits: [endpoint]` (bare-string); `accept-env-up` JSON output does not contain key `endpoint`
- **WHEN** the orchestrator parses the output
- **THEN** the accept stage fails with an error identifying the missing field and the layer name

#### Scenario: CREO-S17 accept-agent receives full accumulated endpoint bundle
- **GIVEN** topology [server-go, flutter]; both layers succeed and emit their fields (in any mix of bare-string and pattern form)
- **WHEN** accept-env-up completes for all layers
- **THEN** the BKD accept-agent issue is created with an endpoint bundle containing fields from all layers merged into a single JSON object

#### Scenario: EPCA-S4 pattern-form emit value is injected to consumer without parsing layer output
- **GIVEN** topology [server-go, flutter]; server-go emits `endpoint` in pattern form (R12 has pre-resolved it to `"ttpos-server-go.req-7-abc.svc.cluster.local:8080"`); flutter needs `BACKEND_ENDPOINT=ZonEaseTech/ttpos-server-go.endpoint`
- **WHEN** the orchestrator prepares to run flutter's `make accept-env-up`
- **THEN** `BACKEND_ENDPOINT="ttpos-server-go.req-7-abc.svc.cluster.local:8080"` is injected; the orchestrator MUST NOT parse server-go's `accept-env-up` JSON output to obtain this value (it is already in the pre-resolved bundle from R12)

---

### Requirement: R5 Endpoint value resolution and passthrough

Endpoint field values in the bundle SHALL come from one of two sources, determined by the `emits` form declared in the layer's manifest (R1):

1. **Pattern-form emits** — the orchestrator MUST resolve the `pattern` template by substituting every `{VAR_NAME}` placeholder using the layer's declared `vars` map. Literal values are taken verbatim. `${SISYPHUS_*}` references MUST be expanded against the orchestrator's REQ context (see R12 for the supported variable namespace). The resolved value is a string and is the canonical emit value; no `accept-env-up` JSON output parsing is performed for this field.
2. **Bare-string emits** — the orchestrator MUST extract the field value from the JSON output of the layer's `accept-env-up` invocation and pass it through unchanged. JSON type information (string, number, boolean, null, array, object) MUST be preserved.

Once a value is in the bundle (from either source), the orchestrator MUST treat it as opaque and pass it through to consumers and the accept-agent unchanged. The orchestrator MUST NOT impose any schema or format constraints on the values themselves — format interpretation (HTTP URL vs ADB host:port vs other) is the contract between provider and consumer. The orchestrator MUST NOT parse, normalize, or validate field values beyond the type-preservation rule above.

#### Scenario: CREO-S18 HTTP URL bare-string endpoint is passed unchanged
- **GIVEN** server-go emits `endpoint` (bare-string) and `accept-env-up` outputs `{"endpoint": "http://ttpos-server-go.ns-abc.svc.cluster.local:8080"}`
- **WHEN** the endpoint bundle is assembled
- **THEN** the accept-agent receives the field value `"http://ttpos-server-go.ns-abc.svc.cluster.local:8080"` byte-for-byte

#### Scenario: CREO-S19 ADB host:port bare-string endpoint is passed unchanged
- **GIVEN** mobile layer emits `device` (bare-string) and `accept-env-up` outputs `{"device": "redroid-pod-x:5554"}`
- **WHEN** the endpoint bundle is assembled
- **THEN** the accept-agent receives `"redroid-pod-x:5554"` unchanged, without conversion to a URL form

#### Scenario: CREO-S20 numeric and boolean JSON field values are preserved for bare-string emits
- **GIVEN** a layer emits `port` and `tls` (both bare-string) and `accept-env-up` outputs `{"port": 8080, "tls": false}`
- **WHEN** the endpoint bundle is assembled
- **THEN** the accept-agent receives `port` as JSON number `8080` and `tls` as JSON boolean `false`, not as strings

#### Scenario: EPCA-S5 pattern-form emit resolves to a string value
- **GIVEN** a layer manifest declares
  ```yaml
  emits:
    - endpoint:
        pattern: "ttpos-server-go.{NS}.svc.cluster.local:{PORT}"
        vars:
          NS: "${SISYPHUS_NAMESPACE}"
          PORT: "8080"
  ```
  and the REQ context provides `SISYPHUS_NAMESPACE=req-7-abc`
- **WHEN** R12 pre-resolve executes for this layer
- **THEN** the bundle contains `{"<repo>": {"endpoint": "ttpos-server-go.req-7-abc.svc.cluster.local:8080"}}` — a JSON string with the placeholders fully substituted

#### Scenario: EPCA-S6 pattern-form emit value is passed through opaquely after resolution
- **GIVEN** a pattern resolves to `"redroid.req-7-abc.svc.cluster.local:5554"`
- **WHEN** the consumer layer (or accept-agent) reads the value via `inputs`
- **THEN** the orchestrator MUST NOT reformat the string (no URL prefixing, no port-stripping, no host-only extraction); the consumer receives the byte-identical string from the bundle

---

## ADDED Requirements

### Requirement: R12 Pre-resolve phase before runner pod startup

The orchestrator SHALL execute a **pre-resolve phase** between source-REQ admission and runner-pod creation. In this phase, after R2 topology resolution but before any `kubectl apply` for the runner pod, the orchestrator MUST:

1. For every repository in the topology, fetch the pinned `.sisyphus/env.yaml` content (using the branch resolved by R6).
2. For every pattern-form entry in each manifest's `emits`, substitute `{VAR_NAME}` placeholders using the entry's `vars` map. `${SISYPHUS_*}` references MUST be expanded against the **REQ context**, which MUST include at minimum:
   - `SISYPHUS_NAMESPACE` — the kubernetes namespace assigned to this REQ (typically derived from `req-{N}-{slug}`)
   - `SISYPHUS_REQ_ID` — the REQ identifier
   - `SISYPHUS_REQ_BRANCH` — the source REQ branch name
   - `SISYPHUS_SOURCE_REPO_SHA` — the source REQ commit SHA at admission time
3. Assemble the resolved values into a partial `endpoint_bundle` keyed by `OWNER/REPO`. Bare-string emits are left unresolved at this phase (their values are filled in at layer runtime by R4).
4. Persist the partial bundle in `stage_runs.context.endpoint_bundle_pre_resolved` (a `dict[str, dict[str, str]]`) so it is observable in Metabase and survives runner pod restarts.

The pre-resolved bundle MUST be available to all consumers — including out-of-band consumers such as APK build dispatch and the mobile-env-up layer — at the moment the runner pod (or any consumer side-channel) starts, **without** waiting for any layer's `accept-env-up` to run.

The orchestrator MUST fail-loud on any pre-resolve error: an unresolved `${SISYPHUS_*}` reference (variable not in the REQ context), a `pattern` referencing a placeholder not declared in `vars` (already rejected by R1, but re-checked here for defence in depth), or a manifest fetch failure for a repo in the topology. Failure MUST cause the REQ to escalate **before** the runner pod is created; `stage_runs.context.failed_layer` MUST identify the offending repo and `stage_runs.context.failed_phase` MUST be `"pre_resolve"` (a new sentinel value distinct from R10's runtime layer-attribution).

#### Scenario: EPCA-S7 pre-resolve assembles bundle before runner pod creation
- **GIVEN** topology [ttpos-server-go, ttpos-flutter] resolved by R2; ttpos-server-go's manifest declares `emits: [{endpoint: {pattern: "ttpos-server-go.{NS}.svc.cluster.local:{PORT}", vars: {NS: "${SISYPHUS_NAMESPACE}", PORT: "8080"}}}]`; REQ context includes `SISYPHUS_NAMESPACE=req-7-foo`
- **WHEN** the pre-resolve phase runs
- **THEN** `stage_runs.context.endpoint_bundle_pre_resolved` equals `{"ZonEaseTech/ttpos-server-go": {"endpoint": "ttpos-server-go.req-7-foo.svc.cluster.local:8080"}}` and the runner pod for this REQ is created **after** this write completes

#### Scenario: EPCA-S8 unresolved SISYPHUS context variable causes pre-resolve fail-loud
- **GIVEN** a manifest pattern references `${SISYPHUS_NONEXISTENT_VAR}` which is not in the orchestrator's REQ-context allow-list
- **WHEN** the pre-resolve phase runs
- **THEN** the REQ MUST escalate before the runner pod is created; `stage_runs.context.failed_phase` equals `"pre_resolve"`; `stage_runs.context.failed_layer` equals the offending repo's full name; the error message MUST name `SISYPHUS_NONEXISTENT_VAR` as the unresolved reference

#### Scenario: EPCA-S9 pre-resolved value is available to APK build dispatch in parallel with accept-env-up
- **GIVEN** an APK build dispatch consumer needs `BACKEND_ENDPOINT` from `ZonEaseTech/ttpos-server-go.endpoint` (pattern form); pre-resolve completed
- **WHEN** the orchestrator triggers both APK build dispatch and `make accept-env-up` for ttpos-server-go
- **THEN** the APK build job receives `BACKEND_ENDPOINT` from the pre-resolved bundle at dispatch time, without waiting for ttpos-server-go's `accept-env-up` to start or complete

#### Scenario: EPCA-S10 manifest fetch failure during pre-resolve fails the REQ before runner pod starts
- **GIVEN** topology resolution lists `org/some-repo` and that repo's manifest fetch returns HTTP 5xx
- **WHEN** pre-resolve attempts to read the manifest
- **THEN** the REQ MUST escalate before runner pod creation; `stage_runs.context.failed_phase` equals `"pre_resolve"`; `stage_runs.context.failed_layer` equals `"org/some-repo"`; the error MUST distinguish the manifest-fetch failure from a placeholder-resolution failure
