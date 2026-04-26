# Tasks: gh-incident-open on escalate

## Stage: contract / spec
- [x] author specs/gh-incident-open/contract.spec.yaml — config knobs, request payload, idempotency
- [x] author specs/gh-incident-open/spec.md scenarios — disabled, success, failure, idempotent

## Stage: implementation
- [x] add `gh_incident_repo` + `gh_incident_labels` to orchestrator/src/orchestrator/config.py
- [x] new module orchestrator/src/orchestrator/gh_incident.py with `open_incident(...)`
- [x] modify orchestrator/src/orchestrator/actions/escalate.py: call `open_incident` in
      "real escalate" branch, persist `gh_incident_url` to ctx, append `github-incident`
      tag to BKD intent issue
- [x] surface helm knobs in orchestrator/helm/values.yaml + templates/configmap.yaml

## Stage: unit test
- [x] tests/test_gh_incident.py — disabled (no repo / no token), success returns html_url,
      HTTP failure returns None, request body contains REQ id + reason + intent issue id
- [x] tests/test_actions_smoke.py — extend escalate tests:
      - real escalate path calls `open_incident` with right args
      - idempotency: ctx.gh_incident_url already set → no second POST
      - GH disabled (default) → escalate still works; no exception raised
      - tags merged include `github-incident`

## Stage: PR
- [x] git push feat/REQ-impl-gh-incident-open-1777173133
- [x] gh pr create

owner: analyze-agent (solo, no fan out — single repo, ~80 LoC change, tightly coupled)
