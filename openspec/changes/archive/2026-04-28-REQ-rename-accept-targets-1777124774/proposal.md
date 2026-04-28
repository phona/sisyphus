# REQ-rename-accept-targets-1777124774: rename ci-accept-env-up/down → accept-env-up/down

## Why

REQ-accept-contract-docs-1777121224 (PR #87) renamed the integration-repo
Makefile targets from `accept-up` / `accept-down` to `ci-accept-env-up` /
`ci-accept-env-down` to align with the ttpos-ci `ci-*` family. In retrospect
the `ci-` prefix is misleading: these targets are NOT part of the per-PR CI
scope (`ci-lint` / `ci-unit-test` / `ci-integration-test` / `ci-build`) — they
are the **accept stage** lab boundary, invoked once per REQ during the accept
transition by `create_accept.py` / `teardown_accept_env.py`, never by GitHub
Actions and never on the PR-CI hot path.

The mismatch shows up in:

- `SISYPHUS_STAGE=accept-env-up` / `SISYPHUS_STAGE=accept-teardown` — the
  env-var values already drop the `ci-` prefix. Only the target names carry it.
- The ttpos-ci `ci-*` family is a CI gate; these are stage-boundary infra
  primitives.
- Integrators reading `docs/integration-contracts.md §2.3` see a `ci-` prefix
  and reasonably assume "this is run by the CI checker family" — confusing.

Drop the prefix everywhere. New canonical names:

- `make accept-env-up` (was `make ci-accept-env-up`)
- `make accept-env-down` (was `make ci-accept-env-down`)

The `ci-lint` / `ci-unit-test` / `ci-integration-test` family is unaffected.
The `accept-env-up.fail` event name (state.py) is unaffected.
The `SISYPHUS_STAGE` env-var values are unaffected.

## What Changes

### Code (sisyphus repo, self-host integration)

- Top-level `Makefile`: `.PHONY` line + recipe headers + comments.
- `orchestrator/src/orchestrator/actions/create_accept.py`: command string
  `cd <dir> && make accept-env-up`.
- `orchestrator/src/orchestrator/actions/teardown_accept_env.py`: command
  string `cd <dir> && make accept-env-down`.
- `orchestrator/src/orchestrator/actions/_integration_resolver.py`: the
  `grep -E '^ci-accept-env-up:'` matcher → `grep -E '^accept-env-up:'`,
  plus all docstring + log references.
- `orchestrator/tests/test_create_accept_self_host.py`: assertion strings
  on the rendered shell command.
- `scripts/sisyphus-accept-up-compose.sh` / `sisyphus-accept-down-compose.sh`:
  comment headers naming the calling Makefile target.
- `deploy/accept-compose.yml`: comment header.
- `orchestrator/deploy/my-values.yaml`: `skip_accept` comment.
- `orchestrator/src/orchestrator/prompts/analyze.md.j2` /
  `orchestrator/src/orchestrator/prompts/accept.md.j2`: prose mentions.

### Specs (openspec)

- `openspec/specs/self-accept-stage/spec.md`: RENAME the heading of the
  "顶层 Makefile MUST 提供 …" Requirement and rewrite scenarios SDA-S1 / SDA-S2
  + the `create_accept` fallback Requirement scenarios SDA-S4..S7 and body
  to use the new target names. Other Requirements in the capability are
  untouched (compose service shape, skip_accept default, accept-agent prompt
  per-repo path).

### Docs

- `docs/integration-contracts.md`: §1 / §2.3 / §3 / §4.2 / §4.2.2 / §5 / §8
  prose, tables, and code blocks.
- `docs/architecture.md`: §2 happy-path mermaid + §5 / §6 / §7 / §8 / §13
  rows.
- `README.md`: 当前架构 mermaid + 接入新业务 repo 表 + 接入清单.
- `CLAUDE.md`: Stage 流 one-liner.
- `orchestrator/docs/V0.2-PLAN.md`: lab/integration table rows.
- `orchestrator/docs/sisyphus-integration.md`: contract section.

### Prior in-flight change folder (REQ-accept-contract-docs-1777121224)

That change documented the previous (`ci-accept-env-up/down`) naming and is
**not yet archived** — its tasks/proposal/spec live in `openspec/changes/`.
Updating its body to the new naming keeps the eventual `openspec apply`
consistent with the merged main branch. The capability folder is renamed in
place from `accept-env-target-naming` → `accept-env-target-naming` (kept for
continuity) but every assertion is rewritten to the new target names. Its
`tasks.md` already shows `[x]`; updating the assertions reflects the post-
rename truth.

## Impact

- **Affected specs**: `self-accept-stage` (this REQ), `accept-env-target-naming`
  (prior REQ's pending change folder, body update).
- **Affected code**: top-level `Makefile`, three orchestrator action modules,
  one test module, two scripts, one deploy yaml, one helm values yaml, two
  prompt templates.
- **Affected docs**: 6 markdown documents.
- **Behavior**: zero runtime behavior change — the targets do the same thing,
  only the name changes. Any external integration repo that already shipped
  `ci-accept-env-up:` will need to rename to `accept-env-up:` (currently the
  only such integration repo is sisyphus itself, self-hosting; no external
  consumers exist).
- **Risk**: low. The grep matcher in `_integration_resolver.py` is the one
  runtime touchpoint that must move in lock-step with the Makefile recipe
  header — both updated atomically in this REQ.
