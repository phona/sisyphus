# Flip integration resolver to source-first

## Why

`_integration_resolver.resolve_integration_dir` was authored back when
`/workspace/integration/<lab-repo>` was treated as the canonical accept-env home
(REQ-self-accept-stage-1777121797). Its decision tree therefore checked
`/workspace/integration/*` first and only fell back to `/workspace/source/*`
when that came up empty.

In the M15→M17 world the picture flipped:

- `scripts/sisyphus-clone-repos.sh` (the only thing that ever populates
  workspace) only writes to `/workspace/source/<basename>/`. Nothing the
  orchestrator owns ever lands repos under `/workspace/integration/`.
- The "integration" path is therefore almost always empty, so the
  integration-first branch is dead code in practice — every real REQ
  (sisyphus self-host, ttpos-flutter + ttpos-arch-lab, etc.) hits the
  fallback "exactly-one-source-with-target" branch.
- The `analyze.md.j2` workspace contract documents only
  `/workspace/source/*` as the layout the analyze-agent must produce.
  Resolver semantics that pretend `/workspace/integration/*` is the
  primary home contradict that contract.

The fix is to **flip the priority**: scan `/workspace/source/*` first and
treat `/workspace/integration/*` as a legacy override for the rare case
where someone manually stages a separate lab clone there. This keeps the
back-compat door open without making the dead branch the default.

## What Changes

- **MODIFIED** `_integration_resolver._decide` policy: source candidates
  now win when exactly one source repo carries `accept-env-up:`. An
  explicit `/workspace/integration/<basename>` only wins when there is
  no unambiguous source candidate (zero sources, or multiple sources).
- **MODIFIED** SDA-S4 / SDA-S5 / SDA-S7 scenarios to reflect the new
  source-first policy. Add a new SDA-S10 covering "multiple sources +
  explicit integration repo → integration breaks the tie".
- **MODIFIED** existing unit tests in
  `orchestrator/tests/test_create_accept_self_host.py` that assert the
  old integration-first ordering.

No behavior change for the two most-common paths:

- Single-repo self-host (sisyphus, single source carries `accept-env-up:`)
  — already returned the source dir, still does.
- Multi-repo with one lab carrying the target — still returns that one
  source dir.

## Impact

- Affected specs: `self-accept-stage` (delta on the resolver requirement +
  scenarios SDA-S4 / SDA-S5 / SDA-S7 / new SDA-S10).
- Affected code: `orchestrator/src/orchestrator/actions/_integration_resolver.py`
  (`_decide` body + module docstring).
- Affected tests: `orchestrator/tests/test_create_accept_self_host.py`
  `TestDecide` + `test_resolve_returns_integration_when_present`.
- No DB migration, no prompt churn, no runner image rebuild.
