# Tasks: REQ-thanatos-cleanup-v2-1777380936

## Stage: deletion

- [x] `rm -rf thanatos/`（src/、tests/、Dockerfile、pyproject.toml、uv.lock、README.md、docs/）
- [x] `rm .github/workflows/thanatos-ci.yml`
- [x] `rm -rf deploy/charts/thanatos/`（Chart.yaml、values.yaml、README.md、templates/）
- [x] `rm docs/thanatos.md`
- [x] `rm orchestrator/tests/test_contract_thanatos_ci.py`
- [x] `Makefile`: 删除 ci-lint 中的 thanatos full-scan 段 + thanatos_files 增量 lint 段
- [x] `Makefile`: 删除 ci-unit-test 中的 thanatos pytest 段
- [x] `rm -rf openspec/changes/REQ-thanatos-m0-scaffold-v6-1777283112/`
- [x] `rm -rf openspec/changes/REQ-ci-lint-test-thanatos-fix-1777338398/`
- [x] `scripts/example-reqs.yaml`: 更新【缺口-2】描述（过时 thanatos_block 引用）
- [x] 确认 `create_accept.py` 中无 thanatos_block 解析逻辑（已有 descope 注释，代码干净）

## Stage: verification

- [x] `make ci-lint` 通过
- [x] `make ci-unit-test` 通过
- [x] `grep -r "thanatos"` 仅命中历史文档/注释/配置（非实际代码/构建）

## Stage: PR

- [x] `openspec/changes/REQ-thanatos-cleanup-v2-1777380936/` created (proposal.md + tasks.md)
- [x] git push feat/REQ-thanatos-cleanup-v2-1777380936
- [x] gh pr create
