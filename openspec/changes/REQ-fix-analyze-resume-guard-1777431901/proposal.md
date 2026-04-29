# Proposal: Add RESUME GUARD to analyze prompt to prevent duplicate execution

## Problem

When analyze-agent completes its work, BKD issue status synchronization may race or the agent may receive a SIGKILL. In such cases:

1. The BKD issue can briefly stay in "review" status (or a user may add a follow-up message to a done issue).
2. If the user wakes the agent again, it inherits the full history and restarts analyze work.
3. Meanwhile sisyphus `req_state` has already advanced to later stages (spec_lint, dev_cross_check, staging_test, etc.).

This wastes agent tokens, runner resources, and risks overwriting already-pushed feat branches or re-creating PRs.

## Solution

Add a **RESUME GUARD** self-check section at the top of `analyze.md.j2` (before Part A). The guard instructs the agent to verify objective facts via git/GitHub:

- `git ls-remote --heads origin feat/{REQ}` — branch pushed?
- `gh pr list --head feat/{REQ} --state open` — PR opened?
- `git show origin/feat/{REQ}:openspec/changes/{REQ}/proposal.md` — openspec artifact exists?

If any check succeeds, the agent MUST refuse duplicate work and output a guard-triggered message.

The guard is self-contained — the agent checks only git/GitHub facts, never queries sisyphus state.

## Scope

- `orchestrator/src/orchestrator/prompts/analyze.md.j2` — add RESUME GUARD section

## Out of scope

- State machine transitions (not touched)
- Actions or webhook logic (not touched)
- Other prompt templates (not touched)
