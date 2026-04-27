# Audit Report — landability of `ttpos-server-go#217` + `ttpos-arch-lab#10`

> Read-only audit. PASS / BLOCKED / RISK per scenario in
> `specs/pr-landability-audit/spec.md`. No business-repo changes filed
> here; fixes deferred to follow-up REQs.

Audit timestamp: **2026-04-26 23:50 UTC** (sisyphus runner pod
`runner-req-ttpos-biz-pr-landability-1777247423`).

## 0. PR-at-a-glance

| | `ttpos-server-go#217` | `ttpos-arch-lab#10` |
|---|---|---|
| Title | `feat(ci): accept-env-up/down via helm chart [REQ-server-go-accept-env-helm-1777200858]` | `feat(accept-lab): helm chart for ttpos-server-go lab` |
| REQ tag | `REQ-server-go-accept-env-helm-1777200858` | `REQ-arch-lab-helm-chart-1777200858` |
| Author | `phona` | `phona` |
| Head | `feat/REQ-server-go-accept-env-helm-1777200858` | `feat/REQ-arch-lab-helm-chart-1777200858` |
| Base | `release` | `main` |
| Repo default branch | `release` ✓ | `main` ✓ |
| Diff | `+935 / -2`, 22 files, 2 commits | `+2831 / -0`, 35 files, 41 commits ahead of `main` (3 from this REQ + 38 from in-flight `seed-json` etc. that haven't reached `main`) |
| `mergeStateStatus` (GH) | `UNSTABLE` | `CLEAN` |
| `mergeable` (GH) | `MERGEABLE` | `MERGEABLE` |
| Status checks | 3 failing + 6 skipped (all reject-before-start) | **0 checks** (repo has no `.github/workflows/`) |
| Predecessor PR | `#214` (Makefile-via-arch-lab approach) — **CLOSED unmerged** 2026-04-26 11:45 UTC | `#9` (cookbook skeleton) — **CLOSED unmerged** 2026-04-26 11:51 UTC |
| `sisyphus` GitHub label | not visible on PR | not visible on PR |

## 1. Per-PR findings — `ZonEaseTech/ttpos-server-go#217`

### LAND-S1 — branch tracks REQ tag · **PASS**

`feat/REQ-server-go-accept-env-helm-1777200858` matches
`feat/<REQ-id>` exactly. REQ tag in title `[REQ-...]`.

### LAND-S2 — base aligns with repo default · **PASS**

`base = release`. `gh repo view ZonEaseTech/ttpos-server-go` confirms
`defaultBranchRef = release` (the repo's release-track convention; not
`main`).

### LAND-S3 — PR has actionable CI signal · **BLOCKED**

The 3 "failing" checks (`Check Skip`, `claude-review`, `dispatch`) are
**all platform-rejected, not code-failed**. Annotation on each:

```
The job was not started because recent account payments have failed or
your spending limit needs to be increased. Please check the
'Billing & plans' section in your settings
```

The 6 downstream jobs (`Lint`, `Main Unit Tests`, `BMP Unit Tests`,
`Main Integration Tests`, `BMP Integration Tests`, `SonarQube
Analysis`) all show `SKIPPED` because they were gated on
`Check Skip`'s rollup.

**Net result: zero real CI signal on this PR.** The author's PR body
test plan asserts `openspec validate --strict`, `helm lint`,
`helm template`, `go vet`, `go test -run TestSGOH` all pass in the
sisyphus runner pod, but none of those are visible on
`statusCheckRollup` — they only count as "ran in the runner pod, took
the author's word for it."

**Blocker class: organisation-level GitHub Actions billing.** No code
fix on this PR will move it. Owner: `ZonEaseTech` org admin.

### LAND-S4 — Makefile contract targets present · **PASS**

Branch HEAD ships `ttpos-scripts/accept-env.mk` (included from
`Makefile`) defining `.PHONY: accept-env-up accept-env-down`. Existing
`ttpos-scripts/lint-ci-test.mk` (touched +9/-2) keeps `ci-lint`,
`ci-unit-test`, `ci-integration-test` intact (per
`REQ-audit-business-repo-makefile-1777125538` baseline).

### LAND-S5 — openspec change is structurally valid · **PASS** (asserted)

Branch ships `openspec/{config.yaml, project.md, AGENTS.md, specs/.gitkeep}`
(this is the repo's openspec init — was not present before) and
`openspec/changes/REQ-server-go-accept-env-helm-1777200858/` with
`proposal.md`, `design.md`, `tasks.md`, plus
`specs/accept-env-helm/{contract.spec.yaml, spec.md}` covering 4
scenarios `SGOH-S1..S4`. Author asserts
`openspec validate --strict REQ-server-go-accept-env-helm-1777200858`
passes (openspec 1.3.1 in runner pod).

> Audit did not re-run `openspec validate` against the PR branch
> because that is `sisyphus spec_lint`'s job — flagged here as
> "trust-but-verify; spec_lint will independently confirm."

### LAND-S6 — no semantic conflict with sibling / predecessor PRs · **RISK**

- **Predecessor `#214` (REQ-server-go-accept-env-makefile-1777195860)
  closed unmerged.** PR #217 properly retires it. ✓
- **Sibling `ttpos-arch-lab#10` ships a competing `accept-env-up`
  implementation** (`charts/accept-lab` in arch-lab repo, with MySQL +
  lab service). PR #217's own body is explicit on the architectural
  choice: "*A self-contained helm chart shipped inside this repo
  removes that coupling. The producer repo now owns both halves of the
  contract... no `$(MAKE) -C` indirection*."
- **Resolver-flip `REQ-flip-integration-resolver-source-1777195860`**
  has shipped (changes dir present in `phona/sisyphus`):
  `_integration_resolver._decide` now picks the **single source repo**
  carrying `accept-env-up:` over any explicit
  `/workspace/integration/<basename>` (per scenarios SDA-S4 / SDA-S10).
  After PR #217 lands and `ttpos-server-go` enters `involved_repos`,
  the source-first resolver will pick its `accept-env-up` directly —
  the arch-lab chart will only matter to non-sisyphus consumers (humans
  running `make accept-env-up` from arch-lab manually).

PR #217 itself is **landable on this axis** — the conflict points at
PR #10's relevance, not PR #217's.

### Verdict on `#217`

**LAND when GHA billing on `ZonEaseTech` is restored, modulo human +
sisyphus mechanical re-check.** No code change required on the PR.

| Check | Status |
|---|---|
| LAND-S1 branch tracks REQ tag | PASS |
| LAND-S2 base = repo default | PASS |
| LAND-S3 PR has CI signal | **BLOCKED — GHA billing** |
| LAND-S4 Makefile contract | PASS |
| LAND-S5 openspec validity | PASS (asserted) |
| LAND-S6 no semantic conflict | RISK (orphans `#10`, see §3) |

## 2. Per-PR findings — `ZonEaseTech/ttpos-arch-lab#10`

### LAND-S1 — branch tracks REQ tag · **PASS**

`feat/REQ-arch-lab-helm-chart-1777200858`. REQ tag in title implicit
via the body `REQ:` line; not in title-string. Minor — lint-pass.

### LAND-S2 — base aligns with repo default · **PASS**

`base = main`, `defaultBranchRef = main`.

### LAND-S3 — PR has actionable CI signal · **BLOCKED**

`statusCheckRollup` is **empty**. `gh api
repos/ZonEaseTech/ttpos-arch-lab/contents/.github/workflows` returns
HTTP 404 on **both** the PR branch and `main` — the repo has no
`.github/` directory at all. No workflow ran on this PR; nothing
gates merge other than `mergeStateStatus = CLEAN` (which only means
"no merge conflicts").

The author's test plan asserts `helm lint`,
`helm template` (4 variants), `openspec validate --strict`,
`check-scenario-refs.sh`, and `make accept-env-up` skeleton mode all
pass — again, none of these are visible to GitHub. Pure
runner-pod-runs-on-trust.

**Blocker class: missing GHA workflows in repo.** Distinct from
`#217`'s billing blocker — this is "no CI configured at all." Owner:
the arch-lab repo maintainer (or a follow-up REQ that ports the
sisyphus contract targets into a workflow).

### LAND-S4 — Makefile contract targets present · **PASS**

Verified by reading `Makefile` on
`origin/feat/REQ-arch-lab-helm-chart-1777200858`:

```make
.PHONY: ci-lint ci-unit-test ci-integration-test accept-env-up accept-env-down

ci-lint:                 # shellcheck + helm lint charts/accept-lab (skip if missing)
ci-unit-test:            # helm template charts/accept-lab × 2 profiles
ci-integration-test:     # bash tests/integration/accept_lab_contract_test.sh
accept-env-up:           # bash accept-env/env-up.sh
accept-env-down:         # bash accept-env/env-down.sh
```

All five sisyphus contract targets present and consistent with
`docs/integration-contracts.md` §2. Tools-missing-tolerated style
(`command -v helm >/dev/null && ...`) matches
`REQ-audit-business-repo-makefile-1777125538` recommendations.

### LAND-S5 — openspec change is structurally valid · **PASS** (asserted)

Ships
`openspec/changes/REQ-arch-lab-helm-chart-1777200858/{design.md,
proposal.md, tasks.md, specs/accept-lab/{contract.spec.yaml, spec.md}}`
plus `openspec/AGENTS.md`. Author asserts 12 ADDED Requirements with
19 scenario defs `ARLAB-S1..S12`,
`openspec validate --strict` and `check-scenario-refs.sh` both pass.

> Same caveat as LAND-S5 on `#217` — sisyphus `spec_lint` will
> independently confirm.

### LAND-S6 — no semantic conflict with sibling / predecessor PRs · **RISK**

Two coupled risks:

1. **Predecessor PR #9 (REQ-arch-lab-accept-env-cookbook-1777195098)
   was closed unmerged 2026-04-26 11:51 UTC** — but PR #10's branch
   was opened on top of `#9`'s branch and still carries those 3 PR-#9
   commits in its diff. PR #10's body acknowledges this:
   "*When PR #9 merges first, GitHub will rebase this PR down to just
   the helm chart additions.*" That premise is now violated — `#9`
   never merged.

   Concrete impact: `git log origin/main..origin/feat/...` shows 41
   commits ahead, of which only 3 are this REQ's work; the other 38
   are pre-existing in-flight branches (`feat/seed-json`,
   `fix/qr-h5-payment-redirect`, etc.) that haven't landed on `main`
   yet. GitHub still reports `mergeStateStatus = CLEAN`, so the PR
   *will* merge as a fat commit if accepted — but it'll bring along
   PR-#9's skeleton commit + the seed-json work as collateral.

   **Recommendation: rebase on `origin/main` so the PR diff matches
   only this REQ's work** (drop the inherited-from-#9 skeleton commit
   `d7d2108` if it should be subsumed, or land it as a separate PR
   first). Today, "what does `#10` actually merge?" is non-obvious from
   reading the PR.

2. **Sibling `ttpos-server-go#217` chooses the source-first
   `deploy/accept-env/` strategy and explicitly rejects the arch-lab
   coupling.** Combined with the resolver flip
   (`REQ-flip-integration-resolver-source-1777195860`), this means the
   arch-lab `charts/accept-lab` chart will **not be invoked by the
   sisyphus accept stage** for any REQ where `ttpos-server-go` is the
   source — the resolver picks `ttpos-server-go`'s in-repo chart and
   never reaches arch-lab.

   PR #10's body frames the chart as "*sisyphus accept stages have
   been promising via the accept-env-up/down contract*" — that primary
   consumer is now gone. The chart can still serve **manual / human
   `make accept-env-up`** in arch-lab and as a richer
   server-go + MySQL acceptance lab for separate use cases — but this
   intent is not stated in the PR.

   **Recommendation: PR #10's author needs to make an explicit call**:
   either reframe the PR's "why" (manual lab tool, not sisyphus
   plumbing) or close it as superseded by `#217`. As written, future
   readers will think the chart is what sisyphus calls when it isn't.

### Verdict on `#10`

**RISK on landing as-is.** Two distinct blockers (no CI in repo +
broken stacking premise) plus a strategic question (does the chart
have a real consumer post-resolver-flip). Mergeable per GitHub
mechanics, but landing without addressing the §3 coupling discussion
ships a chart whose stated purpose no longer holds.

| Check | Status |
|---|---|
| LAND-S1 branch tracks REQ tag | PASS |
| LAND-S2 base = repo default | PASS |
| LAND-S3 PR has CI signal | **BLOCKED — repo has no `.github/workflows/`** |
| LAND-S4 Makefile contract | PASS |
| LAND-S5 openspec validity | PASS (asserted) |
| LAND-S6 no semantic conflict | **RISK — broken stacking + post-flip orphan** (see §3) |

## 3. Cross-PR coupling discussion

The two REQs share timestamp suffix `1777200858` — they were created
as a coordinated pair the same minute. Reading their proposals
side-by-side:

| | `ttpos-server-go#217` | `ttpos-arch-lab#10` |
|---|---|---|
| Where does `accept-env-up` live? | inside `ttpos-server-go/Makefile` | inside `ttpos-arch-lab/Makefile` |
| Where does the helm chart live? | `ttpos-server-go/deploy/accept-env/` (1 pod + 1 svc, no DB) | `ttpos-arch-lab/charts/accept-lab/` (server + MySQL + lab svc) |
| `helm upgrade --install` target | `$(SISYPHUS_NAMESPACE)` w/ contract `--set` flags | `$NAMESPACE` w/ marker ConfigMap + GHCR pull secret |
| Lab shape | minimal — server-go binary on a port | richer — server-go + ephemeral MySQL backing |
| Coupling to the other repo | **none** (self-contained) | none (chart is self-contained inside arch-lab) |

After the resolver flip, sisyphus' accept stage will pick whichever
source repo carries `accept-env-up:` (or fall back to integration
only if no source has it). This means:

- **If only `#217` lands:** sisyphus accept stage runs the
  server-go-internal chart (no MySQL). Works for any REQ whose
  acceptance scenarios don't need a DB.
- **If only `#10` lands:** arch-lab will be picked up only if
  `ttpos-arch-lab` is added to `involved_repos` and is the sole source
  with `accept-env-up:`. (As of the audit, `default_involved_repos =
  [phona/sisyphus]`; arch-lab is not in any default.)
- **If both land:** when both repos appear in `involved_repos` for a
  REQ that touches both, the resolver hits scenario SDA-S7 ("multiple
  sources with `accept-env-up:` and no integration → returns None")
  and accept stage **cannot resolve**. SDA-S10 only breaks the tie if
  there's an explicit `/workspace/integration/<basename>` clone, which
  `sisyphus-clone-repos.sh` never produces.

**That last row is the latent landing-order bug.** Landing both PRs
without disambiguating leaves any future REQ that touches *both*
ttpos-server-go and ttpos-arch-lab with an unresolvable accept stage.

## 4. Recommendations

### For `ttpos-server-go#217`

1. **Block on GHA billing.** Org admin restores spending limits /
   payment method. No code change. Once unblocked, retrigger
   `Check Skip` workflow on the PR (pushing an empty commit or
   re-requesting the check); confirm `claude-review` and `dispatch`
   complete, downstream tests run.
2. **Optional: explicitly note in the PR body that PR `#214` is
   superseded.** The body says it does, but adding `Closes #214` (PR
   #214 is already closed-unmerged, so this is documentation only)
   removes ambiguity for future readers.
3. **No spec / chart / test changes recommended.** The PR is
   structurally clean.

### For `ttpos-arch-lab#10`

1. **Decide intent first.** Either:
   - **(A) Close the PR** as superseded by `#217` (the architectural
     direction explicitly chosen on the server-go side), and file a
     follow-up REQ if a richer-with-MySQL lab is genuinely wanted as
     a parallel artefact for human use.
   - **(B) Reframe the PR** to "manual / multi-stack acceptance lab"
     scope, drop language about sisyphus accept stages, and document
     when one would invoke `make accept-env-up` from arch-lab manually
     vs let sisyphus call server-go's.
2. **If keeping the PR (option B), rebase on `origin/main`.** Currently
   the diff carries 38 unrelated commits from in-flight branches
   (`feat/seed-json`, `fix/qr-h5-payment-redirect`) that haven't
   reached `main`. `git rebase --onto origin/main d7d2108^
   feat/REQ-arch-lab-helm-chart-1777200858` (or interactive equivalent)
   to drop the predecessor-PR-#9 skeleton commit and the inherited
   work. Force-push the cleaned branch.
3. **Open a follow-up REQ to add minimal CI to the arch-lab repo:**
   port the sisyphus contract checks (`make ci-lint`,
   `make ci-unit-test`) into a `.github/workflows/ci.yml` so future
   PRs don't depend on author-asserted runner-pod runs alone. The
   sisyphus repo's own `.github/workflows/` is a near-drop-in template.
4. **Independent of (1)-(3): if both `#217` and `#10` are intended to
   coexist long-term, file a separate REQ in `phona/sisyphus` to
   handle SDA-S7** (multiple sources with `accept-env-up:` and no
   integration). Either deterministic ordering rules, or an explicit
   `accept_env_repo` field on the REQ, would prevent the latent
   resolver deadlock.

### For sisyphus repo (this audit)

1. Land this audit report + the `pr-landability-audit` checklist spec.
2. Future PR-pair landability questions: re-run the six LAND-Sx
   scenarios mechanically against the new pair.

## 5. Items intentionally not audited

- **Per-line code review** (correctness of helm templates, shell
  scripts, Go code). Out of scope — that's claude-review +
  human-review territory, not landability.
- **Whether the helm charts actually deploy a working stack on K3s.**
  That's accept-stage's job once the PRs land + sisyphus runs
  `make accept-env-up` for a real REQ. Audit only checks that the
  Makefile + chart files exist and pass static lint.
- **Spec-vs-code drift inside each PR's openspec change.** Authors
  assert validity; sisyphus `spec_lint` will independently re-run
  `openspec validate --strict` and `check-scenario-refs.sh` once the
  PRs go through the pipeline.
- **Performance / cost of the two lab shapes.** Architectural choice
  question; not landability.
