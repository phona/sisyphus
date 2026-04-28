# Spec — bkd-intent-status-block

## ADDED Requirements

### Requirement: BKD intent stage prompts SHALL begin with a canonical status block

The orchestrator SHALL render a canonical markdown "REQ Status" section at the
top of every BKD intent stage prompt (`intake.md.j2` and `analyze.md.j2`). The
block MUST consolidate REQ identity, current stage, feat-branch name, runner
pod name, BKD intent issue URL, pre-cloned repos, and any known linked PRs into
a single markdown table whose four identity rows (REQ / Stage / Branch / Runner
Pod) are always present. Optional rows MUST be omitted when their input is
empty or missing — no empty cells, no placeholder text. The block MUST appear
above the `tools_whitelist.md.j2` include so that it is the first non-blank
section both the agent and human readers encounter.

#### Scenario: BISB-S1 partial renders a 7-row table when every field is set

- **GIVEN** the partial `_shared/status_block.md.j2` is rendered with
  `status_block` containing `req_id="REQ-foo"`, `stage="analyze"`,
  `bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1"`,
  `cloned_repos=["phona/sisyphus","ZonEaseTech/ttpos-server-go"]`, and
  `pr_links_inline="[phona/sisyphus#123](https://github.com/phona/sisyphus/pull/123)"`
- **WHEN** the partial is rendered to a string
- **THEN** the output MUST start with the heading `## REQ Status`
- **AND** the rendered markdown table MUST contain rows in this order: `REQ`
  (value `` `REQ-foo` ``), `Stage` (value `` `analyze` ``), `Branch` (value
  `` `feat/REQ-foo` ``), `Runner Pod` (value `` `runner-req-foo` ``), `BKD
  intent issue` (clickable link to the URL), `Pre-cloned repos` (comma-joined
  repo basenames), `Linked PRs` (the pre-rendered inline string)
- **AND** the output MUST end with a trailing horizontal rule `---` so the
  block is visually delimited from whatever follows

#### Scenario: BISB-S2 partial omits optional rows when their input is unset

- **GIVEN** `status_block={"req_id": "REQ-bar", "stage": "intake",
  "bkd_intent_issue_url": None, "cloned_repos": None, "pr_links_inline": None}`
- **WHEN** the partial is rendered
- **THEN** the rendered table MUST contain exactly four data rows (REQ, Stage,
  Branch, Runner Pod) and no others
- **AND** the strings `BKD intent issue`, `Pre-cloned repos`, and `Linked PRs`
  MUST NOT appear anywhere in the rendered output
- **AND** the rendered output MUST NOT contain any empty markdown table cells
  (`| |` or `|  |`)

#### Scenario: BISB-S3 analyze prompt opens with the status block above tools_whitelist

- **GIVEN** `analyze.md.j2` is rendered with `req_id="REQ-foo"`,
  `cloned_repos=["phona/sisyphus"]`,
  `bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1"`, and
  `status_block=build_status_block_ctx(req_id="REQ-foo", stage="analyze",
  bkd_intent_issue_url=..., cloned_repos=...)`
- **WHEN** the rendered prompt is searched
- **THEN** the substring `## REQ Status` MUST appear in the output
- **AND** the index of `## REQ Status` MUST be strictly less than the index of
  `## 工具白名单` (the heading defined inside `tools_whitelist.md.j2`)
- **AND** the rendered prompt MUST contain the row `Pre-cloned repos` with
  value `phona/sisyphus`

#### Scenario: BISB-S4 intake prompt opens with the status block and omits cloned_repos

- **GIVEN** `intake.md.j2` is rendered with `req_id="REQ-foo"`,
  `bkd_intent_issue_url="https://bkd.example/projects/p/issues/iss-1"`, and
  `status_block=build_status_block_ctx(req_id="REQ-foo", stage="intake",
  bkd_intent_issue_url=...)` (no `cloned_repos`, no `pr_urls`)
- **WHEN** the rendered prompt is searched
- **THEN** the substring `## REQ Status` MUST appear before `## 工具白名单`
- **AND** the row `Pre-cloned repos` MUST NOT appear in the rendered intake
  prompt (intake never pre-clones)
- **AND** the row `Linked PRs` MUST NOT appear (no PRs at intake)
- **AND** the row `BKD intent issue` MUST appear with the supplied URL

#### Scenario: BISB-S5 linked PRs row formats pr_urls via format_pr_links_inline

- **GIVEN** `build_status_block_ctx` is called with
  `pr_urls={"phona/sisyphus": "https://github.com/phona/sisyphus/pull/42",
  "ZonEaseTech/ttpos-server-go":
  "https://github.com/ZonEaseTech/ttpos-server-go/pull/7"}`
- **WHEN** the resulting context dict is rendered through the partial
- **THEN** the `Linked PRs` row MUST contain
  `[phona/sisyphus#42](https://github.com/phona/sisyphus/pull/42)`
- **AND** it MUST contain
  `[ZonEaseTech/ttpos-server-go#7](https://github.com/ZonEaseTech/ttpos-server-go/pull/7)`
- **AND** the two link strings MUST be separated by a comma so the row stays
  on a single markdown table cell

#### Scenario: BISB-S6 helper collapses empty inputs to None for clean row drop

- **GIVEN** `build_status_block_ctx(req_id="REQ-foo", stage="intake",
  bkd_intent_issue_url="", cloned_repos=[], pr_urls={})` is invoked
- **WHEN** its return value is inspected
- **THEN** the returned dict MUST have `bkd_intent_issue_url=None`,
  `cloned_repos=None`, and `pr_links_inline=None`
- **AND** `req_id` and `stage` MUST be preserved verbatim
- **AND** rendering through the partial MUST yield exactly the four
  always-on rows (proves the empty→None collapse drops optional rows cleanly)

#### Scenario: BISB-S7 omitted status_block kwarg is a no-op for backwards compat

- **GIVEN** `analyze.md.j2` is rendered without a `status_block` kwarg (the
  variable is therefore Jinja2-undefined / falsy) but with all other kwargs
  identical to a control rendering that does pass `status_block=None`
- **WHEN** both rendered outputs are compared after stripping leading/trailing
  whitespace
- **THEN** they MUST be byte-identical, proving the `{% if status_block %}`
  guard makes the include a no-op when the caller hasn't been wired yet
- **AND** neither output MUST contain the string `## REQ Status`
