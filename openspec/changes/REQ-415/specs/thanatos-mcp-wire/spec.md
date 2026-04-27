## ADDED Requirements

### Requirement: accept-env-up JSON contract carries an optional thanatos block

The `make accept-env-up` stdout-tail JSON contract SHALL accept an optional top-level `thanatos` object with three string fields — `pod`, `namespace`, and `skill_repo`. When the field is present every value MUST be a non-empty string; when absent, sisyphus consumers MUST treat the acceptance flow as not-thanatos-wired and fall back to legacy direct-curl behaviour. The existing `endpoint` and `namespace` top-level fields MUST keep their current semantics; the new `thanatos.namespace` exists to let business repos `helm install` the thanatos chart into a different K8s namespace than the lab stack (defaults to the top-level `namespace` when omitted).

#### Scenario: TMW-S1 thanatos block present is parsed into ctx fields

- **GIVEN** `make accept-env-up` stdout last line is
  `{"endpoint":"http://lab.svc:8080","namespace":"accept-req-415","thanatos":{"pod":"thanatos-abc","namespace":"accept-req-415","skill_repo":"ttpos-flutter"}}`
- **WHEN** `orchestrator.actions.create_accept.create_accept` parses it
- **THEN** the prompt rendered for the accept-agent receives
  `thanatos_pod="thanatos-abc"`, `thanatos_namespace="accept-req-415"`, and
  `thanatos_skill_repo="ttpos-flutter"` as render context

#### Scenario: TMW-S2 thanatos block absent leaves thanatos_pod empty

- **GIVEN** `make accept-env-up` stdout last line is
  `{"endpoint":"http://localhost:18000","namespace":"accept-req-415"}` (no
  `thanatos` key)
- **WHEN** `orchestrator.actions.create_accept.create_accept` parses it
- **THEN** the prompt rendered for the accept-agent receives `thanatos_pod` as
  `None` (or unset) so the template renders the legacy curl branch

### Requirement: accept.md.j2 invokes thanatos MCP via kubectl exec when wired

When the rendering context includes a non-empty `thanatos_pod`, the rendered `accept.md.j2` prompt MUST instruct the accept-agent to call thanatos MCP via `mcp__aissh-tao__exec_run` running `kubectl -n <thanatos_namespace> exec -i <thanatos_pod> -- python -m thanatos.server`, sending an MCP `tools/call` request for the `run_all` tool with `skill_path`, `spec_path`, and `endpoint` arguments. The prompt MUST direct the agent to apply every returned `kb_updates` entry to `/workspace/source/<thanatos_skill_repo>/` (action `append` SHALL concatenate; action `patch` SHALL overwrite) and then `git add .thanatos/`, `git commit`, `git push origin feat/<REQ>` so the knowledge base persists on the feature branch.

When `thanatos_pod` is empty / unset, the prompt MUST keep the legacy direct behaviour — glob `/workspace/source/*/openspec/changes/<REQ>/specs/*/spec.md` and curl the endpoint per scenario.

In both branches the prompt MUST instruct the agent to PATCH the BKD issue with `tags=[accept, <REQ>, result:pass]` on full pass and `tags=[accept, <REQ>, result:fail]` on any scenario failure, and SHALL set `statusId=review` so the engine picks up the verifier hand-off.

#### Scenario: TMW-S3 thanatos branch contains MCP exec instructions

- **GIVEN** `accept.md.j2` is rendered with `thanatos_pod="thanatos-abc"`,
  `thanatos_namespace="accept-req-415"`, `thanatos_skill_repo="ttpos-flutter"`,
  `endpoint="http://lab.svc:8080"`, `req_id="REQ-415"`
- **WHEN** the template engine produces the prompt text
- **THEN** the output contains the substring
  `kubectl -n accept-req-415 exec -i thanatos-abc -- python -m thanatos.server`
- **AND** the output mentions `tools/call` and the `run_all` tool name
- **AND** the output instructs the agent to apply `kb_updates` and
  `git push origin feat/REQ-415`

#### Scenario: TMW-S4 fallback branch keeps legacy spec.md glob behaviour

- **GIVEN** `accept.md.j2` is rendered with `thanatos_pod=None` (any other
  thanatos_* value irrelevant)
- **WHEN** the template engine produces the prompt text
- **THEN** the output contains the substring
  `/workspace/source/*/openspec/changes/REQ-415/specs/*/spec.md`
- **AND** the output does not contain `python -m thanatos.server`

#### Scenario: TMW-S5 both branches gate on result tag and statusId=review

- **GIVEN** `accept.md.j2` is rendered twice — once with `thanatos_pod` set and
  once with it unset (same `req_id="REQ-415"`)
- **WHEN** the template engine produces both prompt texts
- **THEN** every rendered output contains both `result:pass` and `result:fail`
  tag tokens
- **AND** every rendered output references `statusId=review`

### Requirement: create_accept defaults thanatos.namespace to the lab namespace

When the `thanatos` block omits its own `namespace` field but supplies a `pod`, `orchestrator.actions.create_accept.create_accept` SHALL default `thanatos_namespace` in the prompt context to the top-level `namespace` field of the env-up JSON. This keeps single-namespace deployments (lab + thanatos co-installed) from having to repeat the namespace name; consumers MUST treat the absence of `thanatos.namespace` as equivalent to `thanatos.namespace == top.namespace`.

#### Scenario: TMW-S6 thanatos block without namespace inherits top-level

- **GIVEN** `make accept-env-up` stdout last line is
  `{"endpoint":"http://lab.svc:8080","namespace":"accept-req-415","thanatos":{"pod":"thanatos-abc","skill_repo":"ttpos-flutter"}}`
- **WHEN** `create_accept` parses it and renders `accept.md.j2`
- **THEN** the rendered prompt's `kubectl -n <ns> exec` command uses
  `accept-req-415` as the namespace
