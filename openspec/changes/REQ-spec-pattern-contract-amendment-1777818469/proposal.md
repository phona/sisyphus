# Proposal — endpoint pattern contract amendment

> **Type**: spec-only amendment to capability `feat-cross-repo-env-orchestration` (originally
> landed via PR #354 / spec #342). No implementation in this REQ; downstream impl REQ派 after
> merge per Phase B/C in the GitHub issue #359.
>
> **Refs**: #342 (origin spec), #359 (amendment driver), #311 #326 #327 #333 (root pain).
> Does **not** `Closes #` —— this is a spec PR, downstream impl REQ closes.

## Why

The current `feat-cross-repo-env-orchestration` spec (R1–R10 + Readiness Gate) ships a
**runtime endpoint passthrough** model:

1. Each layer emits real endpoint values inside its `make accept-env-up` JSON output (R4 + R5).
2. The orchestrator parses output, builds the bundle, injects vars to the next layer.
3. APK build / mobile-env-up / accept-agent receive values **after** the upstream layer is up.

Implementation experiment (`ZonEaseTech/ttpos-flutter#167`,
`ZonEaseTech/ttpos-arch-lab#16`) exposed three load-bearing problems with this runtime model:

- **Speed**: APK build cannot wait for backend `accept-env-up` to finish (5–10 min serial).
  Either we pre-build APK with a guess and risk drift, or we serialize and pay the latency.
- **Single source of truth**: consumers (APK, accept-agent) end up coding the backend's
  endpoint shape (`ttpos-flutter-lab.{ns}` virtual hostname + `ExternalName` shim across 5
  call-sites), so a backend rename / port change requires patching every consumer.
- **Security**: runtime config injection of `BASE_URL` into a built APK is a known footgun
  (recipient can be tricked into pointing the binary at a hostile origin).

The pre-existing R5 "opaque JSON passthrough" framing is **not wrong, just not enough**:
it described how values flow once they exist. It did not address **when** they exist or
**who** is the source of truth for their *shape*.

## What this amendment changes

Three surgical edits to the existing capability spec — enough to declare the architecture,
not enough to implement it. Implementation is a follow-up REQ.

### 1. R1 Manifest schema — add `emits[].pattern` + `emits[].vars`

`emits` entries gain a second admissible form: a single-key object whose value is a
**pattern contract** (a template string with `{VAR}` placeholders) plus a `vars` map
binding placeholders to either literal strings or `${SISYPHUS_*}` references to
orchestrator-provided REQ context variables (namespace, REQ id, source SHA…).

The legacy bare-string form (`emits: [endpoint]`) is **retained verbatim** so that
un-migrated repositories continue to validate. Migration is opt-in per repo.

### 2. R5 Endpoint value semantics — pattern contract resolver replaces "opaque passthrough only"

R5 previously committed only to "treat all emitted values as opaque JSON; pass through
unchanged." This still holds **after** values are resolved. The amended R5 names the
**source** of values:

- For **pattern-form emits**, the orchestrator pre-resolves the pattern using the layer's
  declared `vars` and the REQ context **before** the runner pod starts. The resolved
  string IS the emit value. No `accept-env-up` JSON parsing for these fields.
- For **bare-string emits**, the orchestrator parses `accept-env-up` JSON output at layer
  runtime exactly as before (R4 unchanged for this path).

Either source produces the same `endpoint_bundle` shape. Consumers see no difference.

### 3. ADD R12 Pre-resolve phase

A new requirement formalising the pre-resolve step: read every manifest in the topology,
substitute every pattern-form emit, fail-loud on unresolved placeholders, and have the
bundle ready **before** the runner pod is created. This is the requirement that lets APK
build run in parallel with `accept-env-up`, and that makes the manifest the single source
of truth for endpoint shape.

## What this amendment does **not** change

- **R2** topology resolution: identical algorithm, identical scenarios.
- **R3** workspace layout: unchanged.
- **R6** branch resolution: unchanged.
- **R7** teardown: unchanged.
- **R8** backward compat for repos without manifest: unchanged. Bare-string emits remain
  a first-class legacy path so today's deployments keep working without manifest edits.
- **R9** thanatos `.sisyphus/scenarios/` fallback: unchanged.
- **R10** failure layer attribution: unchanged. Pre-resolve failures are recorded under
  the same `stage_runs.context.failed_layer` shape, with `failed_phase: pre_resolve`.

## Out-of-scope (will not be in any impl REQ for this amendment)

- Cross-repo PR merge ordering automation
- Pattern syntax beyond `{VAR_NAME}` (no expressions, no defaults, no conditionals)
- Variable namespaces other than `${SISYPHUS_*}` (no `${ENV_*}`, no `${SECRET_*}`)
- Pre-resolve for non-emit fields (e.g. resolving `inputs` patterns)
- Migration tooling for legacy bare-string emits

## Roll-out

1. Merge this spec amendment PR (no code change).
2. Open impl REQ "sisyphus orch pattern resolver" (Phase B in #359):
   add manifest reader for new schema; add pre-resolve action; unit + integration tests
   per R12 readiness gate.
3. Open business-repo manifest REQs (Phase C): `ttpos-server-go` + `ttpos-flutter` adopt
   pattern form. The flutter `#167` PR is superseded by this path; close it once the new
   impl REQ lands.

## Side effects

| Issue | How resolved |
|-------|--------------|
| #311 backend not installed in mobile accept-env-up | reaffirmed by R12 + R4: mobile layer receives backend endpoint pre-resolved, never tries to deploy backend itself |
| #326 endpoint format mismatch (HTTP URL vs ADB host:port) | R5 amended: each layer's pattern declares its own format; no normalization, no surprise |
| #327 chart sub-chart assumption | unchanged — already addressed by R3+R4 |
| #333 three-party contract missing | strengthened: pattern contract is the machine-readable contract |
| #248 P0 thanatos M3 dogfood | unblocked: APK build can proceed in parallel with backend `accept-env-up`, removing the 5–10 min serial floor |
