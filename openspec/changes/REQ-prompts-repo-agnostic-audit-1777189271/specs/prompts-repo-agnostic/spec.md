# prompts-repo-agnostic

## ADDED Requirements

### Requirement: Prompt templates MUST NOT bake in the GitHub org name `phona/` in placeholder examples

Every Jinja2 prompt template under `orchestrator/src/orchestrator/prompts/` MUST use a neutral `<owner>/...` placeholder for any inline GitHub repository example, and the literal string `phona/` SHALL NOT appear in any such template.
Sisyphus is a repo-agnostic orchestrator; baking the historical operator's GitHub org name into shipped prompts misleads agents about whether sisyphus is restricted to that org. The neutral form `<owner>/repo-a`, `<owner>/repo-b`, `<owner>/repo` matches the convention already used in `intake.md.j2` (`"involved_repos": ["owner/repo-a"]`), so this requirement extends that convention to every other template under the prompts directory tree (including `_shared/` and `verifier/`).

#### Scenario: PRA-S1 grep for `phona/` in prompt templates returns zero hits

- **GIVEN** the repo at `HEAD` of branch `feat/REQ-prompts-repo-agnostic-audit-1777189271`
- **WHEN** a tester runs
  `grep -RE 'phona/' orchestrator/src/orchestrator/prompts/`
- **THEN** the command MUST exit with status `1` (no matches) and produce no output

#### Scenario: PRA-S2 placeholder examples in changed files use `<owner>/` form

- **GIVEN** the seven files touched by this change that previously contained
  `phona/` placeholder examples (`analyze.md.j2`, `done_archive.md.j2`,
  `_shared/runner_container.md.j2`, `challenger.md.j2`,
  `verifier/dev_cross_check_fail.md.j2`, `verifier/spec_lint_fail.md.j2`,
  `verifier/_decision.md.j2`)
- **WHEN** a reader inspects the placeholder example lines
- **THEN** every previously `phona/`-prefixed example MUST now use either the
  literal `<owner>/` prefix or a documented placeholder name (e.g.
  `<spec_home_repo>` or `<spec_home_repo_basename>`) that is itself owner-agnostic

### Requirement: Prompt templates MUST NOT brand the Makefile ci contract as "ttpos-ci 标准"

Every prompt template that mentions the source-repo Makefile target contract (`ci-lint` / `ci-unit-test` / `ci-integration-test`) MUST refer to it as `Makefile ci 契约` with a pointer to `docs/integration-contracts.md`, and the literal string `ttpos-ci 标准` SHALL NOT appear in any prompt template.
The contract itself is brand-neutral — any source repo joining sisyphus must implement those Makefile targets — so the prompt MUST present it as a generic contract documented at `docs/integration-contracts.md` rather than as a product-line standard. Pointing at the canonical doc rather than the brand keeps the contract's authority in one place and lets new agents navigate to the actual rules.

#### Scenario: PRA-S3 grep for `ttpos-ci 标准` in prompt templates returns zero hits

- **GIVEN** the repo at `HEAD` of branch `feat/REQ-prompts-repo-agnostic-audit-1777189271`
- **WHEN** a tester runs
  `grep -RF 'ttpos-ci 标准' orchestrator/src/orchestrator/prompts/`
- **THEN** the command MUST exit with status `1` (no matches) and produce no output

#### Scenario: PRA-S4 every replaced site links to docs/integration-contracts.md

- **GIVEN** the six sites that previously read "ttpos-ci 标准" (analyze.md.j2 ×2,
  bugfix.md.j2 ×1, staging_test.md.j2 ×1, _shared/runner_container.md.j2 ×1,
  verifier/dev_cross_check_fail.md.j2 ×1, verifier/dev_cross_check_success.md.j2 ×1)
- **WHEN** a reader inspects each replacement
- **THEN** the new wording MUST be the literal phrase `Makefile ci 契约`, and at
  least one of the replacements per file MUST reference `docs/integration-contracts.md`
  so a new agent can navigate to the canonical contract

### Requirement: Prompt templates MUST NOT bake in the acceptance scenario id prefix `FEATURE-A`

`accept.md.j2` and the verifier `accept_*.md.j2` templates SHALL describe acceptance
scenarios as any markdown block whose heading matches `^####\s+Scenario:` (the same
pattern enforced by `scripts/check-scenario-refs.sh`'s `HEADING_PATTERN`), without
prescribing a `FEATURE-A` (or any other) prefix on the scenario id. Scenario ids are
defined by the spec author per repo (e.g. `analyze.md.j2:45` itself uses `UBOX-S1`),
so the prompt MUST NOT presuppose a specific prefix. The literal string `FEATURE-A`
MUST NOT appear in any prompt template under `orchestrator/src/orchestrator/prompts/`.

#### Scenario: PRA-S5 grep for `FEATURE-A` in prompt templates returns zero hits

- **GIVEN** the repo at `HEAD` of branch `feat/REQ-prompts-repo-agnostic-audit-1777189271`
- **WHEN** a tester runs
  `grep -RF 'FEATURE-A' orchestrator/src/orchestrator/prompts/`
- **THEN** the command MUST exit with status `1` (no matches) and produce no output

#### Scenario: PRA-S6 accept.md.j2 instructs reading scenarios via `#### Scenario:` heading match

- **GIVEN** `orchestrator/src/orchestrator/prompts/accept.md.j2` after this change
- **WHEN** a reader inspects Step 2 (Read Acceptance Scenario)
- **THEN** the instruction MUST direct the agent to enumerate every block whose
  heading matches `#### Scenario:` (or equivalent neutral phrasing referring to the
  spec-defined heading), and MUST NOT name `FEATURE-A` or any specific prefix as
  the discriminator
- **AND** the reporting template example MUST use a neutral placeholder
  (e.g. `<scenario-id>` or `S1`) instead of `FEATURE-A1` / `FEATURE-A2`

### Requirement: Prompt templates MUST NOT hardcode `/workspace/source/sisyphus` or any specific repo basename in workspace paths

`bugfix.md.j2` and `challenger.md.j2` SHALL refer to a runner-pod source-repo
working directory only via the documented `/workspace/source/<repo-basename>/`
convention (where the basename is filled at runtime), not via a literal repo name
like `sisyphus`. Hardcoding `sisyphus` makes the bugfix flow only work when sisyphus
itself is the repo under fix (the M0 self-bootstrap case); any other REQ would `cd:
no such file or directory`. Hardcoding `<owner>/<repo>` as the directory segment
(challenger.md.j2's prior bug) is also forbidden, because `scripts/sisyphus-clone-repos.sh`
clones to the basename, not to a `<owner>/<repo>` nested path.

#### Scenario: PRA-S7 grep for `/workspace/source/sisyphus` in prompt templates returns zero hits

- **GIVEN** the repo at `HEAD` of branch `feat/REQ-prompts-repo-agnostic-audit-1777189271`
- **WHEN** a tester runs
  `grep -RF '/workspace/source/sisyphus' orchestrator/src/orchestrator/prompts/`
- **THEN** the command MUST exit with status `1` (no matches) and produce no output

#### Scenario: PRA-S8 challenger.md.j2 path uses basename placeholder

- **GIVEN** `orchestrator/src/orchestrator/prompts/challenger.md.j2` after this change
- **WHEN** a reader inspects every `/workspace/source/...` path
- **THEN** any `<spec_home_repo>` placeholder used as a path segment MUST be replaced
  by `<spec_home_repo_basename>` (or equivalent basename-form placeholder), aligning
  with `scripts/sisyphus-clone-repos.sh`'s clone-to-basename behavior, and the
  template MUST include a one-line note explaining that the basename is the GitHub
  repo name's last `/`-separated segment
