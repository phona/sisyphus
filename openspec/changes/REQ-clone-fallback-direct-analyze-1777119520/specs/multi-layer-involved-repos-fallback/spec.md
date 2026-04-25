# multi-layer-involved-repos-fallback

## ADDED Requirements

### Requirement: _clone.resolve_repos MUST resolve involved_repos through a 4-layer fallback in fixed priority order

The orchestrator's server-side clone helper SHALL resolve the list of
repos to clone into the runner pod via a 4-layer ordered fallback. The
helper MUST consult the layers in this exact priority and return the
first layer whose normalized list is non-empty. The function MUST also
return a `source_label` string identifying which layer matched (for
observability and structured logging via `clone.exec` / `clone.done`):

| order | source_label                                  | input                                                  |
|-------|-----------------------------------------------|--------------------------------------------------------|
| 1     | `ctx.intake_finalized_intent.involved_repos`  | intake-agent finalized intent JSON written by webhook  |
| 2     | `ctx.involved_repos`                          | explicit ctx field set by upstream caller / fixture    |
| 3     | `tags.repo`                                   | BKD intent issue tags shaped `repo:<org>/<name>`       |
| 4     | `settings.default_involved_repos`             | env `SISYPHUS_DEFAULT_INVOLVED_REPOS` (csv or JSON)    |

When all four layers yield an empty (or non-list / all-non-string)
result, the helper MUST return `([], "none")` and the caller MUST skip
the server-side clone (let the analyze-agent run its prompt-driven
fallback per `analyze.md.j2` Part A.3).

The helper MUST normalize each layer's input by filtering out
non-string and empty entries while preserving original order and
deduplicating repeated slugs.

#### Scenario: MLIRF-S1 ctx.intake_finalized_intent wins over every other layer

- **GIVEN** `resolve_repos` is called with
  `ctx={"intake_finalized_intent": {"involved_repos": ["L1/x"]}, "involved_repos": ["L2/x"]}`,
  `tags=["repo:L3/x"]`, `default_repos=["L4/x"]`
- **WHEN** the function returns
- **THEN** the result MUST be `(["L1/x"], "ctx.intake_finalized_intent.involved_repos")`

#### Scenario: MLIRF-S2 layer fall-through to ctx.involved_repos when L1 missing

- **GIVEN** `resolve_repos` is called with
  `ctx={"involved_repos": ["L2/x"]}`, `tags=["repo:L3/x"]`,
  `default_repos=["L4/x"]`
- **WHEN** the function returns
- **THEN** the result MUST be `(["L2/x"], "ctx.involved_repos")`

#### Scenario: MLIRF-S3 layer fall-through to BKD `repo:` tags on direct analyze entry

- **GIVEN** `resolve_repos` is called with `ctx={"intent_title": "..."}`
  (no involved_repos), `tags=["analyze", "REQ-X", "repo:phona/sisyphus"]`,
  `default_repos=["L4/x"]`
- **WHEN** the function returns
- **THEN** the result MUST be `(["phona/sisyphus"], "tags.repo")`

#### Scenario: MLIRF-S4 layer fall-through to settings.default_involved_repos

- **GIVEN** `resolve_repos` is called with empty `ctx`, no `repo:` tags,
  `default_repos=["phona/sisyphus"]`
- **WHEN** the function returns
- **THEN** the result MUST be `(["phona/sisyphus"], "settings.default_involved_repos")`

#### Scenario: MLIRF-S5 all four layers empty returns `([], "none")` and caller skips clone

- **GIVEN** `resolve_repos` is called with empty `ctx`, empty `tags`,
  empty `default_repos`
- **WHEN** the function returns
- **THEN** the result MUST be `([], "none")`
- **AND** `clone_involved_repos_into_runner` calling this MUST return
  `(None, None)` and MUST NOT invoke `exec_in_runner`

### Requirement: _clone helper MUST extract `repo:<org>/<name>` tags with strict slug validation

The helper SHALL extract the slug after the literal prefix `repo:` from
each tag in the `tags` argument, MUST validate it against the regex
`^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]*$` (a strict
subset of GitHub org / repo naming rules: org max 39 chars, repo allows
`._-` and digits), MUST drop tags that do not start with `repo:` or
whose slug fails validation, MUST log a `clone.invalid_repo_tag`
warning when a `repo:` tag has an invalid slug (so operators can spot
typos), MUST tolerate non-string entries in the `tags` iterable without
raising, and MUST deduplicate repeated valid slugs while preserving
first-occurrence order.

#### Scenario: MLIRF-S6 valid slugs accepted, invalid slugs rejected, dedup preserves order

- **GIVEN** `tags = ["analyze", "REQ-X", "repo:phona/sisyphus", "repo:Zone-Ease_Tech/foo", "repo:invalid org/name", "repo:/missing-org", "repo:no-slash-here", "repo:phona/sisyphus", "repo:phona/repo-with.dots_and-dash"]`
- **WHEN** `_extract_repo_tags(tags)` returns
- **THEN** the result MUST be exactly
  `["phona/sisyphus", "phona/repo-with.dots_and-dash"]`
  (note: `Zone-Ease_Tech/foo` is rejected because the org segment
  contains an underscore, which is outside the allowed org charset
  `[A-Za-z0-9-]`)

#### Scenario: MLIRF-S7 None / non-string tags handled without raising

- **GIVEN** `tags = ["repo:phona/x", 42, None, "repo:phona/y"]`
- **WHEN** `_extract_repo_tags(tags)` returns
- **THEN** the result MUST be `["phona/x", "phona/y"]` and no exception
  MUST be raised

### Requirement: settings.default_involved_repos MUST exist and default to empty list

The `Settings` class SHALL declare a `default_involved_repos: list[str]`
field with a `default_factory=list` (i.e. default empty). The env var
binding MUST be `SISYPHUS_DEFAULT_INVOLVED_REPOS` (per the
`SettingsConfigDict(env_prefix="SISYPHUS_")` convention). Pydantic
SHALL accept both csv (`"phona/foo,phona/bar"`) and JSON array forms.
The default MUST be empty so multi-repo deployments are not silently
forced into a wrong default; single-repo deployments (sisyphus
self-dogfood, single-repo lab) opt in via env.

#### Scenario: MLIRF-S8 default_involved_repos field exists with empty default

- **GIVEN** `Settings.model_fields["default_involved_repos"]`
- **WHEN** the test inspects the field
- **THEN** the field MUST exist
- **AND** `default_factory()` MUST evaluate to `[]`

### Requirement: start_analyze and start_analyze_with_finalized_intent MUST forward tags + settings.default_involved_repos to the clone helper

Both `start_analyze` and `start_analyze_with_finalized_intent` SHALL
invoke `clone_involved_repos_into_runner` with both keyword arguments
`tags=tags` (the action's `tags` parameter) and
`default_repos=settings.default_involved_repos`. This is the only path
through which L3 (BKD `repo:` tags) and L4 (settings default) reach the
helper. Forgetting either keyword on either entry point MUST be caught
by the contract test (which greps the action source for the literal
substrings `tags=tags` and `default_repos=settings.default_involved_repos`).

#### Scenario: MLIRF-S9 direct-analyze entry with `repo:` tag clones via L3

- **GIVEN** `start_analyze` is invoked with `tags=["intent:analyze", "repo:phona/sisyphus"]`
  and `ctx={"intent_title": "..."}` (no `involved_repos`)
- **WHEN** the action runs against a fake runner controller
- **THEN** `exec_in_runner` MUST be called once with a command containing
  `/opt/sisyphus/scripts/sisyphus-clone-repos.sh` and the literal
  argument `phona/sisyphus`
- **AND** the action's return value MUST contain
  `cloned_repos == ["phona/sisyphus"]` and MUST NOT contain `emit`

#### Scenario: MLIRF-S10 direct-analyze entry with no ctx, no tags, settings.default set → L4 clones

- **GIVEN** `start_analyze` is invoked with `tags=["intent:analyze"]`,
  `ctx={"intent_title": "single-repo dogfood"}`, and
  `settings.default_involved_repos == ["phona/sisyphus"]`
- **WHEN** the action runs against a fake runner controller
- **THEN** `exec_in_runner` MUST be called once with `phona/sisyphus`
  in the command
- **AND** the action's return value MUST contain
  `cloned_repos == ["phona/sisyphus"]`

#### Scenario: MLIRF-S11 all four layers empty → no exec call, agent dispatched anyway

- **GIVEN** `start_analyze` is invoked with `tags=["intent:analyze"]`,
  `ctx={"intent_title": "no repos anywhere"}`, and
  `settings.default_involved_repos == []`
- **WHEN** the action runs
- **THEN** `exec_in_runner` MUST NOT be called
- **AND** the action MUST still call `BKDClient.follow_up_issue` (preserving
  the agent-driven prompt fallback path from `analyze.md.j2` Part A.3)
- **AND** the return value MUST contain `cloned_repos is None`

### Requirement: _clone helper MUST NOT introspect free-text fields to infer repo slugs

The `actions/_clone.py` module SHALL NOT consult `ctx.intent_title`, the
BKD issue prompt body, or any other free-form text field to fuzzy-parse
`org/repo` slugs. The forbidden source-text substrings `intent_title`,
`get_issue`, and `description` MUST NOT appear anywhere in the file
(neither in code nor in docstrings). This is enforced by a contract
test that greps the file. Rationale: paths like
`src/orchestrator`, `M14b/M14c`, or example slugs in surrounding markdown
documentation are indistinguishable from real `org/repo` slugs at the
regex layer; introducing fuzzy text parsing here trades a real ergonomic
gain (`repo:` tag + settings default already cover that) for an
unbounded false-positive surface that would corrupt
`/workspace/source/`. Any future contributor wanting to add free-text
parsing MUST first remove this guard with a follow-up REQ.

#### Scenario: MLIRF-S12 _clone.py source contains no free-text introspection markers

- **GIVEN** the file `orchestrator/src/orchestrator/actions/_clone.py`
- **WHEN** the contract test reads it as text and searches for the
  literals `intent_title`, `get_issue`, `description`
- **THEN** the search MUST yield zero matches across all three literals
