# Tasks: REQ-gh-incident-per-involved-repo-1777180551

Single-repo REQ: only `phona/sisyphus` touched. Solo execution (no fan-out).

## Stage: contract / spec

- [x] author `openspec/changes/REQ-gh-incident-per-involved-repo-1777180551/proposal.md`
- [x] author `openspec/changes/REQ-gh-incident-per-involved-repo-1777180551/design.md`
- [x] author `openspec/changes/REQ-gh-incident-per-involved-repo-1777180551/specs/gh-incident-open/spec.md` (delta: MODIFIED + ADDED)
- [x] `openspec validate openspec/changes/REQ-gh-incident-per-involved-repo-1777180551`
- [x] `check-scenario-refs.sh` clean

## Stage: implementation

- [x] `gh_incident.open_incident`: add explicit `repo: str` kwarg; remove read of `settings.gh_incident_repo`; keep `github_token` empty-guard
- [x] `actions/escalate.py`: import `_clone.resolve_repos`; add `_resolve_incident_repos` helper layering `gh_incident_repo` as layer 5
- [x] `actions/escalate.py`: replace single-call POST with per-repo loop; idempotency on `ctx.gh_incident_urls`; legacy `ctx.gh_incident_url` stays populated (first URL)
- [x] unit test refresh `tests/test_gh_incident.py` (GHI-S1..S5 add `repo=`, GHI-S6..S10 use new ctx shape)
- [x] unit test refresh `tests/test_contract_gh_incident_open.py` (same)
- [x] unit tests added: GHI-S11 per-involved-repo loop, GHI-S12 partial failure isolation, GHI-S13 idempotency on per-repo dict, GHI-S14 fallback to `gh_incident_repo` when involved_repos empty

## Stage: PR

- [x] `make ci-lint BASE_REV=$(git merge-base HEAD origin/main)` clean
- [x] `make ci-unit-test` clean
- [x] `make ci-integration-test` clean (no integration tests in scope; exit 5 → pass)
- [x] `git push origin feat/REQ-gh-incident-per-involved-repo-1777180551`
- [x] `gh pr create` against `main`
