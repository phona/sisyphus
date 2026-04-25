# Tasks: REQ-self-accept-stage-1777121797

## Stage: contract / spec
- [x] 撰写 `specs/self-accept-stage/spec.md`（self-host fallback 解析、smoke scenario）
- [x] `openspec validate openspec/changes/REQ-self-accept-stage-1777121797 --strict` 过

## Stage: implementation — compose 栈
- [x] 新建 `deploy/accept-compose.yml`：postgres + orchestrator 服务定义
- [x] 新建 `scripts/sisyphus-accept-up-compose.sh`：build + up + 等 healthz + emit endpoint JSON
- [x] 新建 `scripts/sisyphus-accept-down-compose.sh`：`docker compose down -v` 幂等清
- [x] 顶层 `Makefile` 加 `ci-accept-env-up` / `ci-accept-env-down` target

## Stage: implementation — self-host 回退
- [x] `orchestrator/src/orchestrator/actions/create_accept.py`：抽 `_resolve_integration_dir()`，
      integration 优先 + source 单仓回退；emit 友好错误
- [x] `orchestrator/src/orchestrator/actions/teardown_accept_env.py`：复用 helper，env-down 走同一目录
- [x] `orchestrator/src/orchestrator/prompts/accept.md.j2`：spec.md 路径修成
      `/workspace/source/*/openspec/changes/<REQ>/specs/*/spec.md`

## Stage: implementation — config flip
- [x] `orchestrator/deploy/my-values.yaml`：`skip_accept: false` + 注释更新

## Stage: tests
- [x] `orchestrator/tests/test_create_accept_self_host.py`：
  - integration 优先（正常 helm 路径）
  - integration 空 + source 单仓有 ci-accept-env-up target → 回退
  - integration 空 + source 单仓无 target → fail
  - integration 空 + source 多仓 → fail（不强行选）
- [x] `orchestrator/tests/test_create_accept_self_host.py` 同时覆盖 teardown_accept_env 路径解析
- [x] `cd orchestrator && uv run pytest -m "not integration"` 全过

## Stage: PR
- [x] `git push origin feat/REQ-self-accept-stage-1777121797`
- [x] `gh pr create` —— title + body 写清楚动机 + 测试方案
