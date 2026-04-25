# Tasks: REQ-orch-noise-cleanup-1777078500

## Stage: spec

- [x] `openspec/changes/REQ-orch-noise-cleanup-1777078500/proposal.md`
- [x] `openspec/changes/REQ-orch-noise-cleanup-1777078500/tasks.md`
- [x] `openspec/changes/REQ-orch-noise-cleanup-1777078500/specs/orch-noise-cleanup/spec.md`
- [x] `openspec/changes/REQ-orch-noise-cleanup-1777078500/specs/orch-noise-cleanup/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/config.py`：新增 `snapshot_exclude_project_ids: list[str]`
- [x] `orchestrator/src/orchestrator/snapshot.py`：`sync_once` 在 `project_ids` 上应用 exclude 过滤
- [x] `orchestrator/src/orchestrator/runner_gc.py`：把 `ApiException(status==403)` 单独识别为 RBAC 拒绝；进程级 flag short-circuit 后续 disk-check
- [x] `orchestrator/helm/values.yaml`：把 `77k9z58j` 写到默认 exclude 列表
- [x] `orchestrator/helm/templates/configmap.yaml`：注入 `SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS` env

## Stage: tests

- [x] `orchestrator/tests/test_snapshot.py`：新 case `test_sync_once_filters_excluded_projects` 验证 exclude
- [x] `orchestrator/tests/test_runner_gc.py`：新 case 验证 403 第一次 warn 后 short-circuit；非 403 异常仍走 debug

## Stage: PR

- [x] git push feat/REQ-orch-noise-cleanup-1777078500
- [x] gh pr create
