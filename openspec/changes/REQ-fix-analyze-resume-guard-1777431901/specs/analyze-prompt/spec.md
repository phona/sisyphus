# analyze-prompt

## ADDED Requirements

### Requirement: analyze.md.j2 SHALL include a RESUME GUARD self-check at the top of the prompt

The `analyze.md.j2` prompt template MUST include a `RESUME GUARD` section placed before `Part A`. The section MUST instruct the agent to perform three objective checks using git and GitHub CLI:

1. `git ls-remote --heads origin feat/{REQ}` — verify the feature branch has been pushed.
2. `gh pr list --head feat/{REQ} --state open` — verify an open PR exists.
3. `git show origin/feat/{REQ}:openspec/changes/{REQ}/proposal.md` — verify the openspec artifact has been committed and pushed.

If ANY of the three checks indicates the analyze work is already complete, the agent MUST immediately stop execution and output a guard-triggered message without performing any further work.

The guard MUST be self-contained — the agent checks only git/GitHub facts and MUST NOT query sisyphus internal state.

#### Scenario: ARG-S1 all checks negative allows normal execution

- **GIVEN** the feature branch `feat/REQ-x` does not exist on origin
- **AND** no open PR exists for `feat/REQ-x`
- **AND** `openspec/changes/REQ-x/proposal.md` does not exist on origin
- **WHEN** the analyze-agent wakes and runs the RESUME GUARD checks
- **THEN** all three checks return negative
- **AND** the agent proceeds with normal analyze work

#### Scenario: ARG-S2 existing feature branch triggers guard

- **GIVEN** the feature branch `feat/REQ-x` already exists on origin
- **WHEN** the analyze-agent wakes and runs the RESUME GUARD checks
- **THEN** the first check returns positive
- **AND** the agent MUST immediately output the guard-triggered message
- **AND** the agent MUST NOT perform any analyze work

#### Scenario: ARG-S3 existing open PR triggers guard

- **GIVEN** no feature branch exists on origin
- **AND** an open PR for `feat/REQ-x` already exists
- **WHEN** the analyze-agent wakes and runs the RESUME GUARD checks
- **THEN** the second check returns positive
- **AND** the agent MUST immediately output the guard-triggered message
- **AND** the agent MUST NOT perform any analyze work

#### Scenario: ARG-S4 existing openspec artifact triggers guard

- **GIVEN** no feature branch exists on origin
- **AND** no open PR exists for `feat/REQ-x`
- **AND** `openspec/changes/REQ-x/proposal.md` already exists on the origin feature branch
- **WHEN** the analyze-agent wakes and runs the RESUME GUARD checks
- **THEN** the third check returns positive
- **AND** the agent MUST immediately output the guard-triggered message
- **AND** the agent MUST NOT perform any analyze work
