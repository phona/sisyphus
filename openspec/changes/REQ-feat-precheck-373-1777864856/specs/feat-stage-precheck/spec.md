## ADDED Requirements

### Requirement: shared `precheck` hook renders step-0 fail-fast section in stage agent prompts

The orchestrator MUST ship a Jinja2 partial at
`orchestrator/src/orchestrator/prompts/_shared/hooks/precheck.md.j2`
that — when included via the `enabled_prompt_hooks` filename loop — renders a step-0
fail-fast precheck section into stage-agent prompts. The hook MUST gate rendering on
`stage_precheck_enabled.get(stage, False)`, so the section is emitted only for stages whose
agents work inside the runner pod (analyze / challenger / accept / staging_test /
pr_ci_watch / bugfix). When the section IS rendered, it SHALL instruct the agent to:

1. Verify pod env vars `SISYPHUS_REQ_ID`, `GH_TOKEN`, `KUBECONFIG` are non-empty inside the
   runner pod.
2. Verify the runner pod has a working `gh auth status`, `kubectl version --client`, and
   `make --version` (tool-presence smoke).
3. For each `/workspace/source/<repo>/`, run `make ci-precheck` (exit 0 = pass).
   The `ci-precheck` target is OPTIONAL on the business repo side; a `make -n ci-precheck`
   probe MUST treat the literal substring `No rule to make target` as a soft-skip (repo
   has not opted in yet) — only a non-zero exit AFTER the target was found counts as fail.
4. On any HARD precheck failure: PATCH the agent's own BKD issue with tags appended
   `result:fail` + `fail-reason:precheck:<item>` (item ∈ `env:SISYPHUS_REQ_ID`,
   `env:GH_TOKEN`, `env:KUBECONFIG`, `tool:gh`, `tool:kubectl`, `tool:make`,
   `ci-precheck:<repo>`), set `statusId='review'`, and END the session immediately
   (no follow-up, verifier escalates without retry).

The hook MUST NOT hard-code the literal `aissh-tao` provider name; all SSH-exec invocations
SHALL use `mcp__{{ mcp_capability_providers['ssh_exec'] }}__exec_run` so helm-values overrides
of the provider propagate to rendered prompts.

#### Scenario: PRECHECK-S1 hook renders section for ssh-pod-bound stages
- **GIVEN** the default `stage_precheck_enabled` configuration
- **WHEN** `analyze.md.j2`, `challenger.md.j2`, `accept.md.j2`, `staging_test.md.j2`,
  `pr_ci_watch.md.j2`, and `bugfix.md.j2` are rendered with the minimum required context
- **THEN** every rendered output MUST contain the precheck section title
  `## Stage Precheck` and reference the three precheck classes
  (env / tool / `make ci-precheck`)

#### Scenario: PRECHECK-S2 hook stays silent for chat-only stages
- **GIVEN** the default `stage_precheck_enabled` configuration where `intake` resolves to False
- **WHEN** `intake.md.j2` is rendered
- **THEN** the rendered output MUST NOT contain the substring `Stage Precheck`,
  preserving the chat-brainstorm flavour of the intake prompt

#### Scenario: PRECHECK-S3 hook stays silent when disabled via enabled_prompt_hooks
- **GIVEN** an operator override that sets `enabled_prompt_hooks = ["mcp_preflight",
  "self_issue_constraint"]` (precheck removed)
- **WHEN** `analyze.md.j2` is rendered
- **THEN** the rendered output MUST NOT contain the substring `Stage Precheck`,
  while the other hook sections (`MCP 依赖预检`, `只改本 issue`) MUST still be present —
  proving the for-loop honours `enabled_prompt_hooks` (pluggable invariant)

#### Scenario: PRECHECK-S4 hook documents fail tag scheme
- **GIVEN** an `analyze.md.j2` render where the precheck section is emitted
- **WHEN** the rendered text is searched
- **THEN** it MUST contain the literals `result:fail` and `fail-reason:precheck:`
  so the agent emits the canonical tag scheme on hard fail

#### Scenario: PRECHECK-S5 hook respects mcp_capability_providers indirection
- **GIVEN** an operator override that swaps `mcp_capability_providers['ssh_exec']` from
  `aissh-tao` to a different provider name
- **WHEN** `analyze.md.j2` is rendered
- **THEN** the precheck section MUST reference `mcp__<new-provider>__exec_run` and MUST
  NOT contain the substring `mcp__aissh-tao__exec_run`

#### Scenario: PRECHECK-S6 default hook ordering is preflight → precheck → self-issue
- **GIVEN** the default `enabled_prompt_hooks` configuration shipped by the orchestrator
- **WHEN** the list is inspected
- **THEN** it MUST equal `["mcp_preflight", "precheck", "self_issue_constraint"]` exactly
  (order matters: MCP must work before precheck can shell into the pod, and both fail-fast
  segments must precede the self-issue policy hook)
