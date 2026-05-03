# server-side-clone-and-no-env-fallback delta

## ADDED Requirements

### Requirement: server-side clone MUST normalize base override keys to repo basename so per-repo overrides written as `<owner>/<repo>` are honored

The orchestrator and the `sisyphus-clone-repos.sh` helper SHALL agree on a single
canonical key form for `base_overrides` / `--base-for` mappings: **the repo
basename** (the last `/`-separated segment of `<owner>/<repo>`, with any trailing
`.git` stripped). Both layers MUST normalize incoming keys to that canonical form.

Concretely:

- `orchestrator.router.normalize_base_overrides(d)` MUST exist and return a new
  dict whose keys are the basename of each input key. `actions/start_analyze.py`
  and `actions/start_analyze_with_finalized_intent.py` MUST call it after
  merging `settings.default_base_branches` (which Helm operators commonly write
  with `<owner>/<repo>` keys) into the per-REQ `base_overrides`, before
  forwarding to `clone_involved_repos_into_runner`. This MUST happen before the
  override dict is persisted into `req_state.context.base_branches` and before
  it is rendered into the analyze prompt.
- `scripts/sisyphus-clone-repos.sh` MUST accept `--base-for KEY VAL` where KEY
  is either `<owner>/<repo>`, `<repo>.git`, or `<basename>`. The script MUST
  normalize KEY to basename when storing into its lookup map so that
  `_resolve_base()` (which keys by basename) hits regardless of caller form.

This contract closes the production failure documented in
phona/sisyphus#345 where Helm `env.default_base_branches: {phona/sisyphus: main}`
produced `--base-for phona/sisyphus main` arguments that the script silently
ignored, falling back to the global `--base develop` and failing every clone.

#### Scenario: CBOR-S1 helm-style owner/repo key resolves to per-repo override at clone time

- **GIVEN** `settings.default_base_branches = {"phona/sisyphus": "main"}` and
  `settings.default_base_branch = "develop"` and the analyze REQ involves
  exactly the repo `phona/sisyphus`
- **WHEN** `start_analyze` (or `start_analyze_with_finalized_intent`) runs and
  invokes `clone_involved_repos_into_runner`
- **THEN** the command passed to the runner pod MUST contain
  `--base-for sisyphus main` (basename key, not `phona/sisyphus`)
- **AND** the `sisyphus-clone-repos.sh` validation diagnostic MUST read
  `validating base branch 'main' for phona/sisyphus`, NOT `'develop'`
- **AND** `req_state.context.base_branches` MUST be persisted as
  `{"sisyphus": "main"}` (basename-keyed canonical form)

#### Scenario: CBOR-S2 basename-keyed override remains supported (backward compat)

- **GIVEN** `settings.default_base_branches = {"sisyphus": "main"}` (basename
  form, the pre-#345 supported shape)
- **WHEN** `start_analyze` runs the same flow
- **THEN** the script call MUST still contain `--base-for sisyphus main`
- **AND** the validation diagnostic MUST read
  `validating base branch 'main' for phona/sisyphus`
- **AND** `req_state.context.base_branches` MUST be `{"sisyphus": "main"}`

#### Scenario: CBOR-S3 mixed key forms across sources collapse to one basename entry

- **GIVEN** `extract_base_branches` returns `{"ttpos-flutter": "feat/hwt"}`
  (basename form, from a `base:ttpos-flutter:feat/hwt` BKD tag) and
  `settings.default_base_branches = {"phona/ttpos-server-go": "release"}`
  (owner/repo form)
- **WHEN** `start_analyze` merges + normalizes them
- **THEN** the resulting `base_overrides` MUST be exactly
  `{"ttpos-flutter": "feat/hwt", "ttpos-server-go": "release"}`
- **AND** the runner-pod command MUST contain both
  `--base-for ttpos-flutter feat/hwt` and `--base-for ttpos-server-go release`
