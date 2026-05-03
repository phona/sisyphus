# Tasks: REQ-fix-chart-snapshot-exclude-1777808452

## Stage: contract / spec
- [x] Author `specs/helm-snapshot-exclude-project-ids/spec.md` mirroring
      the `helm-default-involved-repos` spec shape (3 ADDED Requirements,
      scenarios CSEPI-S1..S3).

## Stage: implementation
- [x] `orchestrator/helm/templates/configmap.yaml`: change `join "," .` →
      `toJson .` for `SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS`; add
      JSON-vs-csv warning comment.
- [x] `orchestrator/helm/values.yaml`: change default
      `snapshot_exclude_project_ids` from `[77k9z58j]` to `[]` (dead
      project; workflow-test archived).
- [x] `orchestrator/src/orchestrator/config.py`: fix misleading docstring
      that claims csv works; document JSON-only contract and reference
      issue #343.
- [x] `orchestrator/tests/test_contract_helm_snapshot_exclude_project_ids.py`:
      new contract test (CSEPI-S1..S3) — values default, configmap
      template shape, Settings JSON parse round-trip.

## Stage: PR (推之前必须全绿)
- [x] git push feat/REQ-fix-chart-snapshot-exclude-1777808452
- [x] make ci-lint → 全绿
- [x] make ci-unit-test → 全绿（新增 3 个 CSEPI-S* 测试通过）
- [x] make ci-integration-test → 全绿 / no PG 自动跳过
- [x] gh pr create --label sisyphus
