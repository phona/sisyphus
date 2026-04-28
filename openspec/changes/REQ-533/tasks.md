## Stage: contract / spec
- [x] author openspec/changes/REQ-533/proposal.md
- [x] author openspec/changes/REQ-533/specs/obs-schema-auto-apply/spec.md
- [x] author openspec/changes/REQ-533/tasks.md

## Stage: implementation
- [x] 新建 orchestrator/src/orchestrator/obs_schema.py（apply_obs_schema + _resolve_schema_path）
- [x] 修改 orchestrator/src/orchestrator/main.py（startup 插入 await apply_obs_schema()）
- [x] 新建 orchestrator/tests/test_obs_schema.py（7 个单元测试）
- [x] 本地 pytest 通过

## Stage: PR
- [ ] git push feat/REQ-533
- [ ] gh pr create
