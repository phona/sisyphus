# fixer-prompts

## ADDED Requirements

### Requirement: start_fixer SHALL route to a dedicated prompt template based on the fixer field in the verifier decision

The `start_fixer` action in `orchestrator/src/orchestrator/actions/_verifier.py` SHALL select the fixer prompt template according to the `fixer` field stored in the REQ context by the webhook decision parser. When `fixer` equals `"dev"`, the action MUST render `verifier-fix-dev.md.j2`. When `fixer` equals `"spec"`, the action MUST render `verifier-fix-spec.md.j2`. When the `fixer` field is absent or has any other value, the action MUST fall back to the legacy `bugfix.md.j2` template for backward compatibility.

#### Scenario: DFP-S1 dev fixer uses dedicated dev prompt

- **GIVEN** a verifier decision with `"fixer": "dev"` has been stored in ctx
- **WHEN** `start_fixer` is invoked
- **THEN** the rendered prompt MUST be from `verifier-fix-dev.md.j2`
- **AND** the prompt MUST contain the text "DEV FIXER"
- **AND** the prompt MUST contain the constraint "LOCKED：只改业务代码"

#### Scenario: DFP-S2 spec fixer uses dedicated spec prompt

- **GIVEN** a verifier decision with `"fixer": "spec"` has been stored in ctx
- **WHEN** `start_fixer` is invoked
- **THEN** the rendered prompt MUST be from `verifier-fix-spec.md.j2`
- **AND** the prompt MUST contain the text "SPEC FIXER"
- **AND** the prompt MUST contain the constraint "LOCKED：只改 spec 相关文件"

#### Scenario: DFP-S3 missing fixer field falls back to legacy bugfix prompt

- **GIVEN** a verifier decision with no `"fixer"` field (legacy decision format)
- **WHEN** `start_fixer` is invoked
- **THEN** the rendered prompt MUST fall back to `bugfix.md.j2`

### Requirement: verifier-fix-dev.md.j2 SHALL restrict the agent to modifying only business source code

The dev fixer prompt template MUST contain hard constraints that prohibit the agent from modifying spec files, test files, Makefile, CI configuration, openspec files, or any file outside the business implementation directories (such as `src/`, `lib/`, `cmd/`, `internal/`, `pkg/`). The prompt MUST instruct the agent to push fixes to the feat/REQ-x branch and to run `make ci-lint` with `BASE_REV` before pushing.

#### Scenario: DFP-S4 dev prompt contains business-code-only lock

- **GIVEN** `verifier-fix-dev.md.j2` is rendered
- **THEN** the prompt MUST contain an explicit prohibition against modifying openspec files
- **AND** the prompt MUST contain an explicit prohibition against modifying test files
- **AND** the prompt MUST contain an explicit prohibition against modifying Makefile or CI configuration

### Requirement: verifier-fix-spec.md.j2 SHALL restrict the agent to modifying only spec-related files

The spec fixer prompt template MUST contain hard constraints that prohibit the agent from modifying business source code, test files, Makefile, or CI configuration. The prompt MUST instruct the agent to fix openspec validation errors, scenario reference issues, and acceptance criteria problems. The prompt MUST include a pre-push self-check running `openspec validate` and `check-scenario-refs.sh`.

#### Scenario: DFP-S5 spec prompt contains spec-only lock

- **GIVEN** `verifier-fix-spec.md.j2` is rendered
- **THEN** the prompt MUST contain an explicit prohibition against modifying business source code
- **AND** the prompt MUST contain an explicit prohibition against modifying test files
- **AND** the prompt MUST contain a spec-drift warning (do not twist spec to match broken implementation)

### Requirement: webhook decision parser SHALL persist target_repo into the REQ context

The webhook handler in `orchestrator/src/orchestrator/webhook.py` that parses the verifier decision JSON MUST extract the `target_repo` field and store it in the REQ context under the key `verifier_target_repo`, alongside the existing `verifier_fixer`, `verifier_scope`, `verifier_reason`, and `verifier_confidence` fields.

#### Scenario: DFP-S6 target_repo from decision is stored in ctx

- **GIVEN** a verifier decision JSON containing `"target_repo": "owner/repo-a"`
- **WHEN** the webhook parses the decision
- **THEN** the REQ context MUST contain `"verifier_target_repo": "owner/repo-a"`

### Requirement: start_fixer SHALL pass target_repo to the rendered prompt template

When rendering the fixer prompt template, `start_fixer` MUST pass the `target_repo` value from the REQ context to the Jinja2 template renderer. The template MUST reference this value to scope the fixer's work to the specified repository.

#### Scenario: DFP-S7 target_repo appears in rendered dev prompt

- **GIVEN** `start_fixer` is invoked with ctx containing `"verifier_target_repo": "owner/repo-a"` and `"verifier_fixer": "dev"`
- **WHEN** the prompt is rendered
- **THEN** the rendered prompt MUST contain the target repository identifier
- **AND** the prompt MUST instruct the agent to modify only that repository
