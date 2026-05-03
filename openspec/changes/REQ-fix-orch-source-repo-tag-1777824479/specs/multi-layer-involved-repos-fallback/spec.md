# multi-layer-involved-repos-fallback delta

## MODIFIED Requirements

### Requirement: _clone.resolve_repos MUST resolve involved_repos through a 5-layer fallback in fixed priority order

The orchestrator's server-side clone helper SHALL resolve the list of
repos to clone into the runner pod via a 5-layer ordered fallback. The
helper MUST consult the layers in this exact priority and return the
first layer whose normalized list is non-empty. The function MUST also
return a `source_label` string identifying which layer matched (for
observability and structured logging via `clone.exec` / `clone.done`):

| order | source_label                                  | input                                                              |
|-------|-----------------------------------------------|--------------------------------------------------------------------|
| 0     | `tags.source-repo`                            | BKD intent issue tags shaped `source-repo:<org>/<name>`            |
| 1     | `ctx.intake_finalized_intent.involved_repos`  | intake-agent finalized intent JSON written by webhook              |
| 2     | `ctx.involved_repos`                          | explicit ctx field set by upstream caller / fixture                |
| 3     | `tags.repo`                                   | BKD intent issue tags shaped `repo:<org>/<name>`                   |
| 4     | `settings.default_involved_repos`             | env `SISYPHUS_DEFAULT_INVOLVED_REPOS` (csv or JSON)                |

L0 (`tags.source-repo`) is a per-REQ explicit override and MUST win
over intake / ctx / `repo:` / settings. This is the same semantic as
the `base:` BKD tag (explicit per-REQ override of `default_base_branch`).
The existing L3 `tags.repo` layer is preserved as an additive fallback
used only when ctx is empty (its position in the table MUST NOT change).

When all five layers yield an empty (or non-list / all-non-string)
result, the helper MUST return `([], "none")` and the caller MUST skip
the server-side clone (let the analyze-agent run its prompt-driven
fallback per `analyze.md.j2` Part A.3).

The helper MUST normalize each layer's input by filtering out
non-string and empty entries while preserving original order and
deduplicating repeated slugs.

#### Scenario: MLIRF-S1 tags.source-repo wins over every other layer

- **GIVEN** `resolve_repos` is called with
  `ctx={"intake_finalized_intent": {"involved_repos": ["L1/x"]}, "involved_repos": ["L2/x"]}`,
  `tags=["source-repo:L0/x", "repo:L3/x"]`, `default_repos=["L4/x"]`
- **WHEN** the function returns
- **THEN** the result MUST be `(["L0/x"], "tags.source-repo")`

## ADDED Requirements

### Requirement: _clone helper MUST extract `source-repo:<org>/<name>` tags with strict slug validation and MUST NOT collide with the `repo:` extractor

The helper SHALL extract the slug after the literal prefix
`source-repo:` from each tag in the `tags` argument, MUST validate it
against the same regex used for `repo:` tags
(`^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]*$`), MUST
drop tags that do not start with `source-repo:` or whose slug fails
validation, MUST log a `clone.invalid_source_repo_tag` warning when a
`source-repo:` tag has an invalid slug (so operators can spot typos),
MUST tolerate non-string entries in the `tags` iterable without raising,
and MUST deduplicate repeated valid slugs while preserving
first-occurrence order.

The two extractors (`_extract_repo_tags` and `_extract_source_repo_tags`)
MUST be independent: a `source-repo:foo/bar` tag MUST NOT also be
counted by `_extract_repo_tags` (because `source-repo:` does not start
with `repo:`), and a `repo:foo/bar` tag MUST NOT be counted by
`_extract_source_repo_tags` (because `repo:` does not start with
`source-repo:`). This isolation lets `resolve_repos` use both extractors
in parallel layers without double-counting.

#### Scenario: SRTO-S1 source-repo: tag override wins over helm default_involved_repos (closes #362)

- **GIVEN** `settings.default_involved_repos = ["phona/sisyphus"]` (helm
  configured for sisyphus self-dogfood) and a direct-analyze REQ
  with `ctx = {"intent_title": "fix something in ttpos-flutter"}`
  (no `involved_repos`) and
  `tags = ["intent:analyze", "source-repo:ZonEaseTech/ttpos-flutter"]`
- **WHEN** `resolve_repos(ctx, tags=tags, default_repos=settings.default_involved_repos)`
  returns
- **THEN** the result MUST be
  `(["ZonEaseTech/ttpos-flutter"], "tags.source-repo")`
- **AND** the L4 `phona/sisyphus` MUST NOT appear in the result
- **AND** subsequent `clone_involved_repos_into_runner` invocations on
  the same inputs MUST issue exactly one `--base ...` clone for
  `ZonEaseTech/ttpos-flutter` (and zero clones of `phona/sisyphus`)

#### Scenario: SRTO-S2 source-repo: tag wins even when intake_finalized_intent declares a different repo

- **GIVEN** `resolve_repos` is called with
  `ctx={"intake_finalized_intent": {"involved_repos": ["other/intake-pick"]}}`,
  `tags=["source-repo:explicit/override"]`, `default_repos=[]`
- **WHEN** the function returns
- **THEN** the result MUST be
  `(["explicit/override"], "tags.source-repo")`
- **AND** the intake pick MUST NOT appear (the explicit per-REQ tag
  overrides intake-agent's understanding by design)

#### Scenario: SRTO-S3 multiple source-repo: tags merge to a deduped, ordered list

- **GIVEN** `_extract_source_repo_tags` is called with
  `tags = ["intent:analyze", "source-repo:phona/sisyphus", "source-repo:ZonEaseTech/ttpos-flutter", "source-repo:phona/sisyphus", "source-repo:invalid org/name", "source-repo:/missing-org", "source-repo:phona/repo-with.dots_and-dash"]`
- **WHEN** the function returns
- **THEN** the result MUST be exactly
  `["phona/sisyphus", "ZonEaseTech/ttpos-flutter", "phona/repo-with.dots_and-dash"]`
  (dedup by first occurrence; invalid slugs dropped)

#### Scenario: SRTO-S4 source-repo: and repo: tag extractors are independent

- **GIVEN** `tags = ["source-repo:A/x", "repo:B/y"]`
- **WHEN** `_extract_source_repo_tags(tags)` and `_extract_repo_tags(tags)`
  are both called
- **THEN** `_extract_source_repo_tags(tags)` MUST return `["A/x"]`
  (only the source-repo tag) and MUST NOT include `B/y`
- **AND** `_extract_repo_tags(tags)` MUST return `["B/y"]` (only the
  repo tag) and MUST NOT include `A/x`
- **AND** when fed through `resolve_repos` with empty ctx and empty
  default_repos, the result MUST be `(["A/x"], "tags.source-repo")`
  (L0 hits before L3 falls through)
