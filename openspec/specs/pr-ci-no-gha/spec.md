# pr-ci-no-gha Specification

## Purpose
TBD - created by archiving change REQ-pr-ci-no-gha-1777105576. Update Purpose after archive.
## Requirements
### Requirement: pr_ci_watch SHALL fail when all check-runs are green but none come from GitHub Actions

The system SHALL classify the verdict for a PR as `no-gha` when **all** check-runs are
`status="completed"` with conclusion in the success set (`success` / `neutral` / `skipped`)
**and** none of those runs have `app.slug == "github-actions"`. The `watch_pr_ci` polling
loop MUST treat a `no-gha` verdict as a failure for that repo: it MUST return a
`CheckResult` with `passed=False`, `exit_code=1`, and a `stdout_tail` segment of the form
`<repo>: no-gha-checks-ran (only non-CI signals: <name>=<conclusion> ...)` enumerating
the actual non-GHA check-runs observed (so the verifier-agent and humans can see exactly
which review-only signals reported success).

A check-run that lacks an `app` field, or whose `app.slug` is anything other than
`"github-actions"`, MUST NOT count toward the "GHA produced a check-run" condition. This
keeps third-party review bots (e.g. `claude-review` from `anthropic-claude`,
Codecov, etc.) from masquerading as a real CI green.

#### Scenario: PRCINOGHA-S1 全绿但只有 review-only check-run → no-gha 判 fail

- **GIVEN** PR head SHA 上只有一条 `app.slug="anthropic-claude"` 的 `claude-review` check-run，conclusion=`success`
- **WHEN** `_classify` 评估这批 runs
- **THEN** 返 `no-gha`；`watch_pr_ci` 返 `passed=False, exit_code=1`，`stdout_tail` 包含 `no-gha-checks-ran` 和实际 check-run 名 `claude-review`

#### Scenario: PRCINOGHA-S2 至少一条 GHA + review-only 全绿 → pass

- **GIVEN** PR head SHA 上有 `app.slug="github-actions"` 的 `lint` 报 success **以及** `app.slug="anthropic-claude"` 的 `claude-review` 报 success
- **WHEN** `_classify` 评估这批 runs
- **THEN** 返 `pass`（review bot 不污染正常 CI 通过判定）

#### Scenario: PRCINOGHA-S3 check-run 缺 app 字段 → 保守按非 GHA

- **GIVEN** PR head SHA 上一条 check-run 缺 `app` 字段（仅 `name/status/conclusion`），conclusion=`success`
- **WHEN** `_classify` 评估这批 runs
- **THEN** 返 `no-gha`（未知来源不能撑 pass）

### Requirement: pending check-runs SHALL override no-gha so GHA workflows can still arrive

The system SHALL prefer `pending` over `no-gha` whenever any check-run is not yet `completed` (`status="in_progress"` / `"queued"`), even if zero GHA check-runs have been observed so far — the GHA workflow may still arrive (webhook propagation delay, `workflow_run` queued behind another commit). Only after **every** check-run is completed and **none** is from GitHub Actions does `_classify` commit to a `no-gha` verdict. The empty-runs case (`runs == []`) MUST continue to return `pending` (existing behavior — GHA hasn't even reported a queued run yet) and MUST NOT be promoted to `no-gha`; the polling loop's existing `exit_code=124` timeout remains the safety net for "GHA never showed up at all."

#### Scenario: PRCINOGHA-S4 有 pending check-run → 不下断 no-gha

- **GIVEN** PR head SHA 上一条 review-only check-run 已 completed=success，**另一条** review-only check-run 仍 `status="in_progress"`
- **WHEN** `_classify` 评估这批 runs
- **THEN** 返 `pending`（不当下断 no-gha；继续等 GHA workflow 上车）

#### Scenario: PRCINOGHA-S5 0 条 check-run → 仍判 pending（不变）

- **GIVEN** PR head SHA 的 check-runs 列表为空 `[]`
- **WHEN** `_classify` 评估
- **THEN** 返 `pending`（与改动前行为一致，等 GHA 触发；最终超时走 exit_code=124）

