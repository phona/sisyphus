# Tasks

## Stage: contract / spec
- [x] author `openspec/changes/REQ-feat-orch-pattern-resolver-1777819864/proposal.md`
- [x] author `openspec/changes/REQ-feat-orch-pattern-resolver-1777819864/design.md` 含数据模型 + REQ ctx 装配代码段 + known scope gap
- [x] author spec delta `specs/feat-cross-repo-env-orchestration/spec.md` —— ADDED 实现 R12 impl 注释 + scenarios IMPL-S1..S6（impl-side 自检 scenario，区别于 EPCA spec 检查）

## Stage: implementation
- [x] `cross_repo_env.py`：加 `EmitPattern` dataclass + `Manifest.emit_patterns` 字段
- [x] `cross_repo_env.py`：`parse_manifest` 接 pattern-form `emits` entry + 单键 dict 校验
- [x] `cross_repo_env.py`：placeholder discovery + vars/sisyphus-ref 校验（EPCA-S3 拒未声明）
- [x] `cross_repo_env.py`：`PreResolveError(failed_phase, failed_layer)` exception
- [x] `cross_repo_env.py`：`pre_resolve_endpoint_bundle(topology, manifest_loader, req_context)` 纯函数
- [x] `actions/create_accept.py`：拓扑结束后调用 pre-resolve；persist 到 `stage_runs.context.endpoint_bundle_pre_resolved`
- [x] `actions/create_accept.py`：`PreResolveError` ⇒ `ACCEPT_ENV_UP_FAIL` + `failed_phase=pre_resolve` 记入 attribution
- [x] `actions/create_accept.py`：bundle 用 pre-resolved 起点；per-layer JSON parse 跳过 pattern-form emits

## Stage: tests
- [x] challenger `test_contract_endpoint_pattern_amendment_challenger.py` 全绿（EPCA-S1..S10）
- [x] `test_cross_repo_env.py`：加 `parse_manifest` pattern-form 单测（EPCA-S1 / S2 / S3）
- [x] `test_cross_repo_env.py`：加 `pre_resolve_endpoint_bundle` 边界单测（fetch fail / unresolved sisyphus var / mixed bare+pattern）
- [x] `test_create_accept_*.py` 不需要新 mock（pure-logic tests 已覆盖；create_accept wiring 走 spec_lint + pr_ci_watch）

## Stage: PR（推之前必须全绿）
- [x] git push feat/REQ-feat-orch-pattern-resolver-1777819864
- [x] `make ci-lint` → 全绿
- [x] `make ci-unit-test` → 全绿
- [x] `make ci-integration-test` → 全绿（无 PG 视为 pass）
- [x] gh pr create --label sisyphus + sisyphus cross-link footer
