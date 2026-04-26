# REQ-docs-drift-audit-1777220568 — chore(docs): audit drift between docs and code

## Why

Code has moved through M16 (cut spec/dev fanout, "for-each-repo" multi-repo
checkers), M17 (analyze-agent全责交付，sisyphus 不再起 spec/dev BKD 子 agent), and
M18 (challenger-agent 黑盒读 spec 写 contract test). Several authoritative docs
(`README.md`, `CLAUDE.md`, `docs/architecture.md`, `docs/state-machine.md`,
`docs/prompts.md`, `docs/api-tag-management-spec.md`,
`observability/sisyphus-dashboard.md`) still describe the M14/M15-era system. The
mismatch is material:

- `docs/api-tag-management-spec.md` is wholly unrelated content (Apifox
  endpoint-lifecycle labels) yet README + CLAUDE both link it as the "BKD issue
  tag 命名规范（router 依赖）" — readers following the doc index land on a doc
  that does not describe `router.py` tag handling at all.
- `docs/prompts.md` links `spec.md.j2` / `dev.md.j2` / `actions/fanout_specs.py`
  / `actions/create_dev.py` — none of these files exist after M16. Lists "12 个"
  verifier templates while the dir holds 14 stage_trigger pairs (17 files).
- `docs/state-machine.md` lists 16 ReqState / 25 Event; `state.py` enumerates 17
  ReqState (CHALLENGER_RUNNING added M18) and 27 Event (CHALLENGER_PASS /
  CHALLENGER_FAIL added M18). The mermaid stateDiagram does not draw the
  CHALLENGER stage between SPEC_LINT and DEV_CROSS_CHECK.
- README + CLAUDE quote stale counts: "13 张 Metabase 看板" (real: 18 SQLs Q01–Q18),
  "15 个 stage 推进动作" (real: 12 non-helper actions), migrations "0001 - 0005"
  (real: 0001–0007), "13 ReqState × 18 Event" (real: 17 × 27).

The goal is to make these docs match the code today; no behavior change.

## What Changes

- **README.md** — fix counts (Metabase = 18, actions list, migrations 0001–0007),
  drop references to non-existent `fanout_specs` / `create_dev` actions and
  `spec.md.j2` / `dev.md.j2` prompts; add CHALLENGER to the pipeline diagram.
- **CLAUDE.md** — fix counts (12 actions, 14 verifier pairs, 7 migrations,
  17 ReqState × 27 Event); fix migration filenames; fix M-marker for checkers
  (drop "M3 admission / M11" — those are not checker files); add challenger to
  happy-path stage flow.
- **docs/architecture.md** — drop `spec-agent` / `dev-agent` rows from
  §5 role table (M16 cut them); drop refs to `spec.md.j2` / `dev.md.j2`; add
  CHALLENGER stage to mermaid + §6 stage table; bump header from
  "v0.2 + M14 + M15" to "v0.2 + M14–M18"; fix verifier prompt count "12" → "14
  stage_trigger pairs".
- **docs/state-machine.md** — add CHALLENGER_RUNNING / CHALLENGER_PASS /
  CHALLENGER_FAIL rows; correct counts (17 / 27); update mermaid to include
  CHALLENGER; drop stale `dev.done` event row.
- **docs/prompts.md** — replace `spec.md.j2` / `dev.md.j2` rows with current
  reality (analyze-agent and its sub-agents own spec + dev); list all 14
  verifier stage_trigger pairs (analyze, accept, challenger, dev_cross_check,
  pr_ci, spec_lint, staging_test) with `_audit.md.j2` partial added; remove
  references to non-existent `actions/fanout_specs.py` / `actions/create_dev.py`.
- **docs/api-tag-management-spec.md** — replace its Apifox content with the
  actual BKD-issue-tag spec the router relies on (intent / stage role /
  result / decision / parent / round patterns derived from `router.py`).
- **observability/sisyphus-dashboard.md** — section header "5 + 8 SQL → 13 个
  Question" → reflect that 18 SQLs ship now; document Q17 (dedup-retry-rate)
  which is currently undocumented; mention migrations 0006 / 0007 alongside
  0004 / 0005.
- **docs/observability.md** — update "migrations 0001~0005" → "0001~0007".

## Impact

- Affected specs: `docs` capability (added).
- Affected code: docs only. No `orchestrator/` source / Makefile / runner image
  changes.
- Risks: low — wholly documentation. spec_lint will pass once openspec scaffold
  is correct; dev_cross_check / staging_test will skip (no Go/Python source
  changes triggering ci-lint scope).
