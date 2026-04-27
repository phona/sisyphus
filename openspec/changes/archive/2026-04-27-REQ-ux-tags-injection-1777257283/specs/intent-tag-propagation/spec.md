## ADDED Requirements

### Requirement: intent_tags 模块必须暴露统一的 hint-tag 过滤器

The orchestrator MUST expose a pure helper module `orchestrator.intent_tags` that defines a closed set of "sisyphus-managed" tags and a `filter_propagatable_intent_tags(tags)` function. The helper MUST treat the following tags as sisyphus-managed (and therefore non-propagatable): the exact strings `sisyphus`, `intake`, `analyze`, `challenger`, `verifier`, `fixer`, `accept`, `staging-test`, `pr-ci`, `done-archive`; any tag whose prefix is one of `intent:`, `result:`, `pr-ci:`, `verify:`, `trigger:`, `decision:`, `fixer:`, `parent:`, `parent-id:`, `parent-stage:`, `target:`, `round-`, `pr:`; and any tag matching the regex `^REQ-[\w-]+$`. The helper MUST drop those tags, non-string entries, and empty / whitespace-only entries, and MUST preserve first-seen order while de-duplicating the survivors.

#### Scenario: UTI-S1 filter strips sisyphus-managed exact tags

- **GIVEN** input `["sisyphus", "intake", "analyze", "challenger", "verifier", "fixer", "accept", "staging-test", "pr-ci", "done-archive"]`
- **WHEN** `filter_propagatable_intent_tags` is called
- **THEN** result is `[]`

#### Scenario: UTI-S2 filter strips sisyphus-managed prefixes

- **GIVEN** input `["intent:analyze", "result:pass", "pr-ci:pass", "verify:dev_cross_check", "trigger:fail", "decision:eyJ...", "fixer:dev", "parent:analyze", "parent-id:abc123", "parent-stage:spec_lint", "target:phona/foo", "round-3", "pr:phona/foo#42"]`
- **WHEN** `filter_propagatable_intent_tags` is called
- **THEN** result is `[]`

#### Scenario: UTI-S3 filter strips REQ-* identifier tags

- **GIVEN** input `["REQ-ux-tags-injection-1777257283", "REQ-foo", "REQ-bar-baz-1234567"]`
- **WHEN** `filter_propagatable_intent_tags` is called
- **THEN** result is `[]`

#### Scenario: UTI-S4 filter keeps user-hint tags in first-seen order

- **GIVEN** input `["repo:phona/sisyphus", "ux:fast-track", "priority:high", "team:platform", "spec_home_repo:phona/sisyphus"]`
- **WHEN** `filter_propagatable_intent_tags` is called
- **THEN** result equals the input list (order preserved, all hints kept)

#### Scenario: UTI-S5 filter de-duplicates survivors

- **GIVEN** input `["repo:foo/bar", "repo:foo/bar", "ux:fast-track", "ux:fast-track"]`
- **WHEN** `filter_propagatable_intent_tags` is called
- **THEN** result is `["repo:foo/bar", "ux:fast-track"]`

#### Scenario: UTI-S6 filter mixes managed + hint and only forwards hints

- **GIVEN** input `["intent:analyze", "REQ-foo-1234", "analyze", "repo:phona/foo", "ux:fast-track", "result:pass", "pr:phona/foo#1"]`
- **WHEN** `filter_propagatable_intent_tags` is called
- **THEN** result is `["repo:phona/foo", "ux:fast-track"]`

#### Scenario: UTI-S7 filter is robust against None / non-string / blank entries

- **GIVEN** inputs `None`, `[]`, `[None, 42, "", "   ", "ux:ok"]`
- **WHEN** `filter_propagatable_intent_tags` is called
- **THEN** results are `[]`, `[]`, `["ux:ok"]`

#### Scenario: UTI-S8 filter is idempotent

- **GIVEN** any input list `xs`
- **WHEN** `filter_propagatable_intent_tags(filter_propagatable_intent_tags(xs))` is computed
- **THEN** the result equals `filter_propagatable_intent_tags(xs)`

### Requirement: start_intake 必须把 user-hint tag 转发到 intent issue

The `start_intake` action MUST, when issuing the BKD `update_issue` PATCH that rewrites the intent issue's tags, append `filter_propagatable_intent_tags(tags)` to the base array `["sisyphus", "intake", req_id]`. The base array MUST come first; user hint tags MUST be appended in the order produced by the filter. The action MUST NOT call `bkd.update_issue(tags=...)` with only the base array when the input `tags` kwarg contains hint tags.

#### Scenario: UTI-S9 start_intake forwards repo: + ux: hints

- **GIVEN** webhook body.tags = `["intent:intake", "repo:phona/sisyphus", "ux:fast-track"]`
- **WHEN** `start_intake` runs and dispatches the rename PATCH
- **THEN** the BKD `update_issue` call's `tags` kwarg equals `["sisyphus", "intake", "REQ-X", "repo:phona/sisyphus", "ux:fast-track"]`

#### Scenario: UTI-S10 start_intake without hints stays backward compatible

- **GIVEN** webhook body.tags = `["intent:intake"]`
- **WHEN** `start_intake` runs and dispatches the rename PATCH
- **THEN** the BKD `update_issue` call's `tags` kwarg equals `["sisyphus", "intake", "REQ-X"]`

### Requirement: start_analyze 必须把 user-hint tag 转发到 intent issue

The `start_analyze` action MUST, when issuing the BKD `update_issue` PATCH that rewrites the intent issue's tags, append `filter_propagatable_intent_tags(tags)` to the base array `["analyze", req_id]`.

#### Scenario: UTI-S11 start_analyze forwards repo: tag through PATCH

- **GIVEN** webhook body.tags = `["intent:analyze", "repo:phona/sisyphus"]` and `start_analyze` is dispatched
- **WHEN** the rename PATCH is sent to BKD
- **THEN** the `update_issue` call's `tags` kwarg contains `"repo:phona/sisyphus"` and equals `["analyze", "REQ-X", "repo:phona/sisyphus"]`

#### Scenario: UTI-S12 start_analyze strips stale sisyphus-managed tags

- **GIVEN** webhook body.tags = `["intent:analyze", "result:pass", "pr:phona/foo#1", "repo:phona/foo", "ux:fast-track"]`
- **WHEN** `start_analyze` runs the rename PATCH
- **THEN** the `update_issue` call's `tags` kwarg equals `["analyze", "REQ-X", "repo:phona/foo", "ux:fast-track"]` (no `intent:`, `result:`, `pr:` tags forwarded)

### Requirement: start_analyze_with_finalized_intent 必须把 user-hint tag 转发到新建的 analyze issue

The `start_analyze_with_finalized_intent` action MUST, when calling `bkd.create_issue` to spawn the new analyze sub-issue, set its `tags` argument to `["analyze", req_id, *filter_propagatable_intent_tags(tags)]`. The `sisyphus` pipeline-identity tag is auto-injected by `BKDRestClient.create_issue` and MUST NOT be added explicitly here.

#### Scenario: UTI-S13 intake-path analyze inherits hints from intake issue

- **GIVEN** webhook body.tags = `["sisyphus", "intake", "REQ-X", "result:pass", "repo:phona/foo", "ux:fast-track"]` (intake completion)
- **WHEN** `start_analyze_with_finalized_intent` runs and creates the new analyze issue
- **THEN** the BKD `create_issue` call's `tags` kwarg equals `["analyze", "REQ-X", "repo:phona/foo", "ux:fast-track"]`

### Requirement: start_challenger 必须把 user-hint tag 转发到新建的 challenger issue

The `start_challenger` action MUST, when calling `bkd.create_issue`, set its `tags` argument to `["challenger", req_id, f"parent-id:{source_issue_id}", *pr_link_tags, *filter_propagatable_intent_tags(tags)]`. The order MUST be: role tag first, REQ id second, parent-id, PR-link tags from `pr_links.pr_link_tags`, then user hint tags last. The action MUST NOT lose any of those four groups.

#### Scenario: UTI-S14 challenger inherits hints from analyze issue

- **GIVEN** webhook body.tags = `["analyze", "REQ-X", "repo:phona/foo", "ux:fast-track"]` and `pr_links.pr_link_tags(...)` returns `["pr:phona/foo#42"]`
- **WHEN** `start_challenger` runs
- **THEN** the BKD `create_issue` call's `tags` kwarg starts with `["challenger", "REQ-X", "parent-id:<analyze-issue-id>"]`, then includes `"pr:phona/foo#42"`, and ends with `["repo:phona/foo", "ux:fast-track"]`

#### Scenario: UTI-S15 challenger filters out re-emitted role / managed tags

- **GIVEN** webhook body.tags = `["analyze", "REQ-X", "result:pass", "challenger", "intent:analyze", "repo:phona/foo"]`
- **WHEN** `start_challenger` runs
- **THEN** the BKD `create_issue` call's `tags` kwarg ends with `"repo:phona/foo"` and contains exactly one occurrence of `"challenger"`, `"REQ-X"`, and `"parent-id:..."`
