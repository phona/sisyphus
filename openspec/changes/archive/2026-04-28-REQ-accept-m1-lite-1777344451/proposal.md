# REQ-accept-m1-lite-1777344451: Minimal Accept Stage (descope thanatos MCP)

## Problem

PR #170 (REQ-415: thanatos MCP wire accept stage) changed 1296 lines and has been
blocked for a long time. Meanwhile the accept stage is effectively a no-op: it emits
`accept.pass` without actually verifying any business behavior, causing staging_test
passes that are vacuously true.

The accept stage needs to do real work before the full thanatos MCP wiring ships.

## Proposal

Replace the v0.2 BKD-agent-dispatching `create_accept` with a minimal mechanical
implementation (v0.3-lite) that:

1. Iterates `/workspace/source/*/` (the cloned repos from `ctx.cloned_repos`)
2. Per repo, runs a three-phase shell script:
   - **Phase 1**: `make accept-env-up` (if target exists; skip+warn if missing)
   - **Phase 2**: `sleep accept_smoke_delay_sec` (default 30s, configurable)
   - **Phase 3**: `make accept-smoke` (if target exists; skip+warn if missing)
   - **Phase 4**: `make accept-env-down` (best-effort cleanup, idempotent)
3. Emits `accept.pass` if all repos pass; `accept.fail` with `fail_repos` list if any fail
4. Stores `accept_result` in ctx so `teardown_accept_env` can route correctly without
   depending on BKD agent tags

## Scope

**In scope:**
- `create_accept.py` rewrite (v0.3-lite): per-repo script, no BKD agent
- `teardown_accept_env.py`: add `ctx.accept_result` fallback (backward-compatible)
- `config.py`: `accept_smoke_delay_sec: int = 30`
- Unit tests for the 4 key scenarios

**Out of scope:**
- thanatos MCP wiring (independent REQ)
- `accept-smoke` Makefile target in business repos (each repo adds it when ready)
- Changes to done_archive or downstream stages

## Impact

- accept stage now does real work: env-up → smoke → env-down per repo
- Repos without `accept-env-up` target get a fail-open skip (not a failure)
- Empty `cloned_repos` → vacuous pass (preserves skip_accept semantics)
- `teardown_accept_env` remains backward-compatible with legacy BKD-agent flow
