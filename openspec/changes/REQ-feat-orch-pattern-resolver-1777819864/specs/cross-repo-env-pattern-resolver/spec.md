## ADDED Requirements

### Requirement: cross_repo_env exposes pure pattern resolver + PreResolveError

The orchestrator MUST extend `orchestrator.cross_repo_env` with a hermetic pure-logic pattern
resolver implementing R12 of `feat-cross-repo-env-orchestration`. Concretely the module SHALL
expose:

- `EmitPattern` — frozen dataclass holding `field`, `pattern`, `vars` (a mapping
  string→string, where a value is either a literal or a reference of the form
  `${SISYPHUS_*}`).
- `Manifest.emit_patterns: dict[str, EmitPattern]` — a parallel record of pattern-form
  entries keyed by field name. The pre-existing `Manifest.emits` tuple MUST continue to list
  every emit field (bare-string + pattern-form) so `inputs` reference validation and the R4
  bundle surface remain unchanged.
- `PreResolveError(message, *, failed_phase, failed_layer)` — `Exception` subclass carrying
  the offending repo (`failed_layer`) and the constant phase sentinel
  `failed_phase == "pre_resolve"` (distinct from R10's runtime layer-attribution).
- `pre_resolve_endpoint_bundle(topology, manifest_loader, req_context)` — a pure function
  taking exactly those three positional/keyword parameters and no others. It MUST iterate
  `topology` in order, call `manifest_loader(repo)` for each, substitute every pattern-form
  emit's placeholders using the entry's `vars` map plus `${SISYPHUS_*}` references resolved
  against `req_context`, and return a partial bundle of the shape
  `dict[str, dict[str, str]]` keyed by `OWNER/REPO`. Bare-string emits MUST NOT appear in
  the returned bundle (they are filled in by R4 at layer runtime).

The function MUST raise `PreResolveError` and abort the iteration on first failure, attributing
to `failed_layer` the offending repo. Failure modes covered:

1. `manifest_loader(repo)` raises any exception — wrapped as `PreResolveError` whose message
   contains the literal substring `manifest fetch` (so EPCA-S10's distinguishability
   assertion holds).
2. A pattern's `vars` value references `${SISYPHUS_X}` and `req_context["SISYPHUS_X"]` is
   missing — `PreResolveError` whose message names `SISYPHUS_X`.
3. A pattern declares a `{VAR}` placeholder absent from `vars` — already rejected at parse
   time by R1, but the resolver re-checks for defence in depth and raises
   `PreResolveError` if it slips through.

#### Scenario: IMPL-S1 pre_resolve_endpoint_bundle is hermetic and three-arg only
- **GIVEN** the implementation of `pre_resolve_endpoint_bundle`
- **WHEN** `inspect.signature(pre_resolve_endpoint_bundle)` is examined
- **THEN** the parameter names MUST NOT contain any of `output`, `json`, `result`, `runtime`,
  `ready`, or `endpoint_bundle` — proving the function does not depend on layer-runtime data
  (consistent with EPCA-S4/S9 in `feat-cross-repo-env-orchestration`)

#### Scenario: IMPL-S2 mixed bare-string and pattern entries in one emits list
- **GIVEN** a manifest text containing
  ```yaml
  emits:
    - namespace
    - endpoint:
        pattern: "svc.{NS}:8080"
        vars:
          NS: "${SISYPHUS_NAMESPACE}"
  ```
- **WHEN** `parse_manifest` runs on the text
- **THEN** the returned `Manifest.emits` MUST equal `("namespace", "endpoint")` and
  `Manifest.emit_patterns` MUST contain only key `"endpoint"` (`"namespace"` MUST NOT
  appear in `emit_patterns`)

#### Scenario: IMPL-S3 pre-resolve substitutes literal vars verbatim
- **GIVEN** a pattern-form emit `pattern: "svc.{NS}:{PORT}"` with
  `vars: {NS: "${SISYPHUS_NAMESPACE}", PORT: "8080"}` and `req_context = {SISYPHUS_NAMESPACE: "ns-x"}`
- **WHEN** `pre_resolve_endpoint_bundle(["org/x"], loader, req_context)` runs
- **THEN** `bundle["org/x"]["endpoint"] == "svc.ns-x:8080"`, with the literal `8080`
  substituted byte-identical (no expansion attempt on non-`${SISYPHUS_*}` values)

#### Scenario: IMPL-S4 manifest_loader exception → PreResolveError(manifest fetch)
- **GIVEN** a manifest_loader that raises `RuntimeError("simulated 5xx")` when asked for
  `org/some-repo`
- **WHEN** `pre_resolve_endpoint_bundle(["org/some-repo"], loader, req_ctx)` runs
- **THEN** it MUST raise `PreResolveError` whose `failed_phase == "pre_resolve"` and
  `failed_layer == "org/some-repo"` and whose message contains the substring
  `"manifest fetch"` so EPCA-S10's distinguishability assertion holds

#### Scenario: IMPL-S5 unresolved ${SISYPHUS_*} → PreResolveError naming the variable
- **GIVEN** a manifest with `vars: {NS: "${SISYPHUS_GHOST}"}` and `req_context` lacking
  `SISYPHUS_GHOST`
- **WHEN** `pre_resolve_endpoint_bundle(["org/x"], loader, req_ctx)` runs
- **THEN** it MUST raise `PreResolveError` whose message contains `SISYPHUS_GHOST`,
  `failed_phase == "pre_resolve"`, `failed_layer == "org/x"`

#### Scenario: IMPL-S6 bundle shape excludes repos without pattern-form emits
- **GIVEN** topology `[org/be, org/fe]` where `org/be` has one pattern-form emit and
  `org/fe` has only `needs` + `inputs` (no pattern emits)
- **WHEN** pre-resolve runs
- **THEN** the returned bundle MUST contain key `"org/be"` with the resolved field, and
  MUST either omit `"org/fe"` entirely or map it to an empty dict — no other shapes are
  acceptable

---

### Requirement: accept stage wires pre-resolve before per-layer accept-env-up

In `orchestrator.actions.create_accept`, the multi-layer entry point MUST call
`pre_resolve_endpoint_bundle` immediately after topology resolution and **before** any
per-layer `make accept-env-up` invocation. The orchestrator MUST:

1. Build a `req_context` map containing at minimum `SISYPHUS_NAMESPACE`,
   `SISYPHUS_REQ_ID`, `SISYPHUS_REQ_BRANCH`, `SISYPHUS_SOURCE_REPO_SHA`. Missing context
   keys (e.g. when source SHA is not yet recorded) MUST default to the empty string —
   manifests that reference them WILL fail explicitly via R12 fail-loud, which is the
   desired behaviour.
2. Persist the returned bundle under `stage_runs.context.endpoint_bundle_pre_resolved` for
   the open accept stage_run, so observability (Metabase Q-series queries, admin readouts)
   sees pre-resolved values without joining elsewhere.
3. Seed the in-memory `bundle` (which drives R4 `inputs` injection) with the pre-resolved
   values. Subsequent per-layer JSON parse MUST iterate only **bare-string** emits — fields
   already in the pre-resolved bundle MUST NOT be re-extracted from the layer's
   accept-env-up output (R4 EPCA-S4 invariant).
4. On `PreResolveError` the stage MUST emit `ACCEPT_ENV_UP_FAIL` with attribution
   `failed_layer = e.failed_layer`, `failed_phase = "pre_resolve"`, and the error message
   surfaced as `reason` in the action result.

#### Scenario: IMPL-S7 pre-resolve fail aborts before any layer accept-env-up runs
- **GIVEN** a multi-layer topology where `pre_resolve_endpoint_bundle` raises
  `PreResolveError(failed_layer="org/x")`
- **WHEN** `create_accept` enters its multi-layer path
- **THEN** the action MUST return an `ACCEPT_ENV_UP_FAIL` event with
  `failed_phase == "pre_resolve"` and `failed_layer == "org/x"`; no `make accept-env-up`
  invocation MUST be attempted; `stage_runs.context.failed_phase` MUST equal `"pre_resolve"`

#### Scenario: IMPL-S8 pre-resolved values seed bundle and skip JSON re-extraction
- **GIVEN** a pattern-form emit `org/be.endpoint = "svc.ns-x:8080"` resolved by pre-resolve;
  `org/be`'s `make accept-env-up` outputs JSON without an `endpoint` key (because the
  pattern-form path does not require it)
- **WHEN** `create_accept` processes the layer's stdout
- **THEN** the layer MUST NOT fail with `missing emit field 'endpoint'`; the bundle MUST
  retain the pre-resolved value `"svc.ns-x:8080"` for `org/be.endpoint`
