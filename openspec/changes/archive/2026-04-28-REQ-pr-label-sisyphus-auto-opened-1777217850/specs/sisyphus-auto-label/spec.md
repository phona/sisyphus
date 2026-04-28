# sisyphus-auto-label

## ADDED Requirements

### Requirement: orchestrator-created BKD issues MUST carry the `sisyphus` tag

Every BKD issue created by sisyphus orchestrator code (any path that ultimately calls `BKDRestClient.create_issue` or `BKDMcpClient.create_issue`) MUST include the literal string `sisyphus` in its `tags` array. The injection MUST happen inside the BKD client `create_issue` method itself rather than at each `actions/*` callsite, so adding a future stage cannot accidentally ship without the tag. The injection MUST be idempotent: when a caller already passes `"sisyphus"` in `tags`, the resulting tag list MUST NOT contain `"sisyphus"` more than once. The existing caller tag order MUST be preserved (auto-injected `"sisyphus"` is prepended; caller-supplied tags stay in their original order after).

#### Scenario: SAL-S1 BKDRestClient.create_issue auto-injects sisyphus when caller omits it

- **GIVEN** a `BKDRestClient` instance with a fake HTTP layer
- **WHEN** the test calls `await client.create_issue("p", "T", ["analyze", "REQ-1"])`
- **THEN** the request body POSTed to `/projects/p/issues` MUST contain
  `tags: ["sisyphus", "analyze", "REQ-1"]` in that order

#### Scenario: SAL-S2 BKDRestClient.create_issue is idempotent when caller already includes sisyphus

- **GIVEN** a `BKDRestClient` instance with a fake HTTP layer
- **WHEN** the test calls `await client.create_issue("p", "T", ["sisyphus", "analyze", "REQ-1"])`
- **THEN** the request body POSTed MUST contain
  `tags: ["sisyphus", "analyze", "REQ-1"]` exactly (no duplicate `"sisyphus"`)

#### Scenario: SAL-S3 BKDMcpClient.create_issue auto-injects sisyphus

- **GIVEN** a `BKDMcpClient` instance with a fake `call` method capturing args
- **WHEN** the test calls `await client.create_issue("p", "T", ["analyze"])`
- **THEN** the captured `tags` argument MUST contain `"sisyphus"` (and not duplicate it)

### Requirement: start_intake MUST tag the intent issue with `sisyphus`

`orchestrator/src/orchestrator/actions/start_intake.py` MUST include `"sisyphus"`
in the `tags` list passed to `bkd.update_issue(...)` when it renames the intent
issue to `[REQ-xxx] [INTAKE] — <title>`. Although the intent issue itself is opened
manually by the user (not via `create_issue`), once sisyphus picks it up and routes
it to intake, the issue is part of sisyphus pipeline's tracked workflow and SHALL
be uniformly identifiable alongside all other sisyphus-managed issues.

#### Scenario: SAL-S4 start_intake passes sisyphus in update_issue tags

- **GIVEN** a fake BKD client capturing `update_issue` kwargs
- **WHEN** `start_intake(...)` is invoked end-to-end with the fake client
- **THEN** the captured `update_issue` call's `tags` keyword argument MUST contain
  `"sisyphus"`, alongside the existing `"intake"` and `<req_id>` tags

### Requirement: analyze.md.j2 MUST instruct agents to label every PR with `sisyphus`

`orchestrator/src/orchestrator/prompts/analyze.md.j2` SHALL contain explicit
instructions that the analyze-agent (and any sub-agent it dispatches) MUST attach
the GitHub label `sisyphus` to every Pull Request it opens. The instructions MUST
be concrete enough that an LLM reading the rendered prompt can execute them without
guessing: there MUST be both a `gh label create sisyphus ... --force` invocation
shown (idempotent, ensures the label exists in the target repo before PR creation)
and a `gh pr create --label sisyphus ...` invocation shown.

#### Scenario: SAL-S5 rendered analyze prompt contains gh label create + gh pr create --label sisyphus

- **GIVEN** the analyze prompt rendered with a sample REQ id
  (e.g. via `orchestrator.prompts.render("analyze.md.j2", req_id="REQ-x", ...)`)
- **WHEN** the test searches the rendered string
- **THEN** the rendered text MUST contain the literal substring
  `gh label create sisyphus`
- **AND** MUST contain the literal substring `--label sisyphus` somewhere in a
  `gh pr create` example or instruction
- **AND** the literal substring `sisyphus` MUST be referenced as a label name
  (so the instruction is unambiguous to the reading LLM)

### Requirement: tools_whitelist.md.j2 MUST show `sisyphus` in the curl POST sub-issue tag example

`orchestrator/src/orchestrator/prompts/_shared/tools_whitelist.md.j2` SHALL show
the `sisyphus` tag in the example payload of the `curl POST /api/projects/{alias}/issues`
sub-issue fan-out call, so any sub-agent copy-pasting the example automatically
inherits the tag. The example MUST also be accompanied by a one-line instruction
that any sisyphus-launched sub-issue's `tags` array MUST include `"sisyphus"`.

#### Scenario: SAL-S6 rendered tools_whitelist contains sisyphus in curl POST example

- **GIVEN** the tools_whitelist partial rendered (e.g. via the same Jinja env that
  renders `analyze.md.j2`, which includes it)
- **WHEN** the test inspects the rendered text around the line
  `curl -sS -X POST http://localhost:3000/api/projects/$PROJECT/issues`
- **THEN** the example payload's `tags` array MUST include the literal string
  `"sisyphus"`
- **AND** the rendered text MUST include a one-line instruction explicitly
  requiring sub-issues created by sisyphus-launched agents to carry the
  `sisyphus` tag
