# Proposal — BKD intent body status block

## Why

When sisyphus dispatches `start_intake` / `start_analyze`, it follow-ups a multi-page
prompt to the user-facing **BKD intent issue**. Today every status-relevant fact is
*scattered* across that wall of text:

| Fact | Where it surfaces (analyze.md.j2) |
|---|---|
| REQ id | line 15 (`REQ={{ req_id }}`) |
| Pod name | line 79, 102, 222 |
| Branch | line 152, 184 |
| Cloned repos | line 95 (conditional inline sentence) |
| BKD intent URL | line 259 (footer block, hard to find) |
| Linked PRs | nowhere |

Both **the agent** (who needs canonical REQ identity / branch / pod within the first
screenful so it can re-derive context after compaction) and **the human in BKD UI**
(who opens the issue to understand "what's running, where, what's linked") have to
hunt. The intake template is even worse — it has zero visible link back to the
intent issue, so a fan-out sub-agent reading the chat has no clickable anchor.

## What

Add a single canonical **`## REQ Status` markdown table** at the top of the BKD
intent stage prompts (`intake.md.j2` + `analyze.md.j2`). It consolidates the
identity / location / link facts the orchestrator already has in hand at dispatch
time:

```markdown
## REQ Status

| Field | Value |
|---|---|
| REQ | `REQ-foo` |
| Stage | `analyze` |
| Branch | `feat/REQ-foo` |
| Runner Pod | `runner-req-foo` |
| BKD intent issue | [open](https://bkd.example.com/projects/p/issues/iss-1) |
| Pre-cloned repos | `phona/sisyphus` |
| Linked PRs | [phona/sisyphus#123](https://github.com/phona/sisyphus/pull/123) |

---
```

Rows whose value is unset (e.g., no PRs at intake time) are omitted; the four
identity rows (REQ / Stage / Branch / Runner Pod) are always present because they
are computable from `req_id` alone.

The block is rendered by a new shared partial
`orchestrator/src/orchestrator/prompts/_shared/status_block.md.j2`, fed by a
helper `prompts.status_block.build_status_block_ctx(...)`. Both intake and analyze
templates `{% include %}` the partial **above** `tools_whitelist.md.j2` so it is
the first thing the agent reads.

## What it is NOT

- Not a new state, event, transition, or checker. The state machine is unchanged.
- Not pushed to other stage prompts (staging_test / pr_ci_watch / accept /
  challenger / done_archive / verifier / fixer). Those run in sisyphus-created
  sub-issues, not on the user-facing **intent** issue. Title says "intent body" so
  scope is intent only — extending to other stages is a separate REQ.
- Not posted to the BKD issue UI as a separate "metadata panel". BKD REST has no
  writable description / comments / notes endpoint (probed: `description` PATCH
  silently no-ops; `prompt` PATCH silently no-ops; `/notes`, `/comments`,
  `/messages` all 404). The only durable text surface is the prompt body that
  sisyphus follow-ups, so that is where the block must live.
- Does not remove the existing scattered references (`REQ={{ req_id }}` line 15,
  pod name occurrences, footer cross-link block). Those stay — the status block
  is additive context, not a refactor.

## Risk / blast radius

- Pure prompt-template addition. No behavioural change to checkers, state, BKD
  REST, or runner. Worst case: agent ignores the new block and reads the old
  scattered references like before.
- Tests: prompt regression tests (`test_prompts_sisyphus_label.py`,
  `test_prompts_repo_agnostic.py`) assert specific strings inside the rendered
  output but not "must NOT contain X", so adding a block above them is safe.
- Backwards compat for callers: render() continues to accept the same kwargs;
  callers that omit `status_block=` get a falsy include (template no-op).

## Acceptance

- `## REQ Status` header is the first non-blank section of rendered analyze and
  intake prompts.
- Always-on rows (REQ / Stage / Branch / Runner Pod) appear with values
  derived from `req_id`.
- Optional rows omit cleanly when their input is empty / None.
- `make ci-unit-test` passes.
