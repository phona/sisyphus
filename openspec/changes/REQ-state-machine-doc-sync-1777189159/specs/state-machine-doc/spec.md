## ADDED Requirements

### Requirement: docs/state-machine.md MUST describe done-archive as no-auto-merge

The ReqState description for `archiving` in `docs/state-machine.md` SHALL state
that the done-archive agent does NOT auto-merge PRs and does NOT push to main.
The text MUST mention that final merge is left to a human reviewer per
PR #124, and MUST describe the agent's actual work as per-repo `openspec apply`
plus writing the archive result. The phrase "合 PR + 关 issue" (which implies
auto-merge) MUST NOT appear in the `archiving` row.

#### Scenario: SMD-S1 archiving row reflects no-auto-merge contract
- **GIVEN** `docs/state-machine.md` rendered as markdown
- **WHEN** a reader reads the ReqState table row whose first column is `archiving`
- **THEN** the description MUST contain the substring `不 auto-merge`
- **AND** the description MUST contain the substring `不 push main`
- **AND** the description MUST reference PR `#124`
- **AND** the description MUST NOT contain the substring `合 PR`

### Requirement: docs/state-machine.md MUST describe gh-incident as per-involved-repo loop

The ReqState description for `gh-incident-open` in `docs/state-machine.md` SHALL
describe the GH-incident side-effect path. It MUST reference both PR 118 and
PR 122 by number, MUST use the correct function name `gh_incident.open_incident()`
(not `file_incident`), MUST state that the escalate action loops over each
involved source repo, MUST mention the five-layer fallback used to resolve
incident-target repos (`intake_finalized_intent` then `ctx.involved_repos`
then `repo:` tag then `default_involved_repos` then `settings.gh_incident_repo`),
and MUST mention that the resulting URLs land in
`ctx.gh_incident_urls: dict[str, str]`.

#### Scenario: SMD-S2 gh-incident-open row references both PRs and correct function name
- **GIVEN** `docs/state-machine.md` rendered as markdown
- **WHEN** a reader reads the ReqState table row whose first column is `gh-incident-open`
- **THEN** the description MUST reference both PR `#118` and PR `#122`
- **AND** the description MUST contain the substring `gh_incident.open_incident()`
- **AND** the description MUST NOT contain the substring `gh_incident.file_incident()`

#### Scenario: SMD-S3 gh-incident-open row documents per-repo loop and ctx shape
- **GIVEN** `docs/state-machine.md` rendered as markdown
- **WHEN** a reader reads the ReqState table row whose first column is `gh-incident-open`
- **THEN** the description MUST contain the substring `每个 involved source repo`
- **AND** the description MUST contain the substring `ctx.gh_incident_urls`
- **AND** the description MUST mention all five fallback layers: `intake_finalized_intent`, `ctx.involved_repos`, `repo:` tag, `default_involved_repos`, `settings.gh_incident_repo`
