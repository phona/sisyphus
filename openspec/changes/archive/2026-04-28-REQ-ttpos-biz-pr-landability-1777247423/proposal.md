# REQ-ttpos-biz-pr-landability-1777247423: audit(read-only) — landability of `ttpos-server-go#217` + `ttpos-arch-lab#10`

## Why

Two coordinated REQs landed feat branches against the ttpos biz repos in
parallel late on 2026-04-26:

- `ttpos-server-go#217` — `feat(ci): accept-env-up/down via helm chart
  [REQ-server-go-accept-env-helm-1777200858]` (base `release`)
- `ttpos-arch-lab#10` — `feat(accept-lab): helm chart for ttpos-server-go lab
  [REQ-arch-lab-helm-chart-1777200858]` (base `main`)

Both PRs implement halves of the sisyphus accept-env contract
(`accept-env-up` / `accept-env-down` + JSON endpoint line on stdout — see
`docs/integration-contracts.md` §3) and both are sitting "open + ready" by
their authors' test plans. Before sisyphus' done-archive stage merges them,
we need an external read of:

- whether each PR is actually mergeable from a CI / reviewer-signal stance
  (independent of what the PR body claims), and
- whether the two PRs interact with each other and with the recently-flipped
  source-first accept-env resolver
  (`REQ-flip-integration-resolver-source-1777195860`) in a way that makes
  one of them dead-on-arrival or in conflict.

This REQ is a **read-only audit**. It does not modify the two ttpos biz
repos and does not ship behavior changes to sisyphus. The only deliverables
are an audit report + a reusable PR-landability checklist sunk back into
sisyphus' specs so the next "are these two PRs ready to merge?" question
can be answered by running the same six checks.

## What Changes

- New capability `pr-landability-audit` with 6 ADDED Requirements
  (LAND-S1..LAND-S6) covering branch naming, base alignment, CI signal,
  Makefile contract, openspec validity, and cross-PR conflict — the
  checklist any future PR-pair audit can follow.
- New audit deliverable
  `openspec/changes/REQ-ttpos-biz-pr-landability-1777247423/audit-report.md`
  applying that checklist to `ttpos-server-go#217` and `ttpos-arch-lab#10`,
  with PASS / BLOCKED / RISK per scenario and a recommendation per PR.
- No changes to `ZonEaseTech/ttpos-server-go` or `ZonEaseTech/ttpos-arch-lab`
  (both PRs stay open as-is — fixes belong in follow-up REQs filed against
  those repos).
- No behavioral changes to sisyphus orchestrator code.

## Task nature

**Read-only audit.** Findings and recommendations only. Concrete fixes for
the blockers identified in `audit-report.md` (e.g. resolving the GitHub
Actions billing rejection that's failing all checks on `#217`, or the
"zero `.github/workflows/`" gap on `ttpos-arch-lab`, or the architectural
overlap between the two `accept-env` chart implementations) are deferred
to independent follow-up REQs.

## Why a checklist, not a one-shot doc

The pair `(server-go-accept-env-helm, arch-lab-helm-chart)` is the second
time sisyphus has shipped two coordinated PRs whose `accept-env-up`
implementations partially overlap (the first being PR #214 ↔ PR #9, both
now closed-unmerged). The answer "can these two PRs land together?" keeps
recurring; pinning the criteria as a reusable spec means the next pair
audit doesn't have to re-derive them.

## Out of scope

- **Resolving the GHA billing-rejection blocker on `ZonEaseTech` org.**
  That's an org-level admin task, not a REQ-level deliverable. The audit
  flags it; a human or a follow-up REQ owns the fix.
- **Closing or rebasing the two PRs themselves.** This audit produces
  recommendations; the actual close / rebase / re-push decisions are made
  by whoever owns each repo.
- **Bringing CI online in `ttpos-arch-lab`.** The repo currently has zero
  `.github/workflows/` and PR #10 inherits that gap. A separate REQ should
  port the sisyphus contract checks (`make ci-lint` etc.) into a workflow.
- **Choosing the "winning" `accept-env` implementation.** PR #217's
  self-contained `deploy/accept-env/` chart and PR #10's external
  `charts/accept-lab` represent two architectural strategies; the audit
  surfaces the conflict and the resolver-flip impact, but the
  pick-one-and-deprecate-the-other call needs a human or a design REQ.
