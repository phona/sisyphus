# Tasks — REQ-pr-issue-traceability-1777218612

## Stage: contract / spec
- [x] author `specs/cross-link/spec.md` with all ADDED requirements + scenarios
- [x] write `proposal.md` (motivation + scope + risk)

## Stage: implementation

### URL helpers + config
- [x] add `orchestrator/src/orchestrator/links.py` with
      `bkd_issue_url(project_id, issue_id)`,
      `format_pr_links_md(pr_urls)`,
      `discover_pr_urls(repos, branch)`
- [x] add `bkd_frontend_url: str = ""` to `Settings` in `config.py`

### Persistence
- [x] `webhook.py`: on fresh-REQ `insert_init`, also persist
      `bkd_intent_url` to context (computed via `bkd_issue_url`)
- [x] `actions/create_pr_ci_watch.py`: call `discover_pr_urls` before the
      checker / BKD-agent dispatch; persist `ctx.pr_urls`

### Cross-link embedding
- [x] `gh_incident.py`: `_format_body` accepts `bkd_intent_url`,
      `pr_urls`; renders clickable lines
- [x] `gh_incident.py`: `open_incident` accepts and forwards new kwargs
- [x] `actions/escalate.py`: pass `bkd_intent_url` + `pr_urls` from ctx
      to every `open_incident` call
- [x] `actions/start_analyze.py`: pass `bkd_intent_issue_url` template var
      to `analyze.md.j2` render
- [x] `prompts/analyze.md.j2`: append PR-body footer section requiring a
      `<!-- sisyphus:cross-link ... -->` block in every PR
- [x] `actions/done_archive.py`: pull `pr_urls` from ctx, pass to render
- [x] `prompts/done_archive.md.j2`: render `pr_urls` markdown bullets when
      template var present

### Observability
- [x] `observability/queries/sisyphus/05-active-req-overview.sql`: add
      `bkd_intent_url` and `pr_urls_md` columns

## Stage: unit test
- [x] `tests/test_links.py`: scenarios XLINK-S1..S6 (helpers)
- [x] `tests/test_contract_pr_issue_traceability.py`: webhook persists
      bkd_intent_url; create_pr_ci_watch persists pr_urls;
      gh_incident body contains clickable links; analyze prompt
      contains PR footer; q05 SQL selects new columns

## Stage: PR
- [x] `make ci-lint` clean
- [x] `make ci-unit-test` green
- [x] git push `feat/REQ-pr-issue-traceability-1777218612`
- [x] gh pr create
