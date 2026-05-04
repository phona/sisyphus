# Proposal: runner pod self-heal (lazy recreate, PVC reused)

Closes #394.

## 背景

2026-05-04 v5 dogfood staging_test 卡住的根因：earlier ops session 跑过
`kubectl delete pod --all -n sisyphus-runners`（清 secret cache），把
`runner-req-chore-d-dogfood-v5-...` 一起删了。PVC `workspace-...` 还在，但
orchestrator 假设 runner pod 一直 alive，下一次 stage 推进时 staging_test
checker 直接调 `rc.exec_in_runner(...)`，K8s API 返 404 → checker `result:fail`
→ verifier escalate。

`k8s_runner.RunnerController.ensure_runner` 是 idempotent (409 = exists)，
能复用 PVC 重建 Pod，但只在 `start_intake / start_analyze / create_accept`
等 *进入新 stage* 的 action 里被调；checker 路径（spec_lint / dev_cross_check /
staging_test / analyze_artifact_check）和复用 runner 的辅助 action
(`create_pr_ci_watch._discover_repos_from_runner` / `teardown_accept_env`)
没有 ensure 步骤，pod 不在就直接 fail。

## 范围

只动 `phona/sisyphus`。新增轻量自愈，不改状态机、不动 retry policy：

1. **`orchestrator/src/orchestrator/actions/_runner.py`（新）**：
   `ensure_runner_alive(req_id) -> bool` helper：先 `get_runner_status` 读 pod
   phase，alive 直接返回 True；NotFound / Failed / Succeeded 时 lazy recreate
   （Failed/Succeeded 先 `pause` 删旧 pod，再 `ensure_runner(wait_ready=True)`
   重建。PVC 不动，`/workspace` 内容 + clone + go cache 全部保留）。

2. **wire 进入所有 reuse-runner 的 stage 路径**，在第一次 `exec_in_runner` 之前
   调 `ensure_runner_alive`：
   - `checkers/spec_lint.run_spec_lint`
   - `checkers/dev_cross_check.run_dev_cross_check`
   - `checkers/staging_test.run_staging_test`
   - `checkers/analyze_artifact_check.run_analyze_artifact_check`
   - `actions/create_pr_ci_watch._discover_repos_from_runner`
   - `actions/teardown_accept_env._run_single_layer_teardown` / `_run_multi_layer_teardown`

   `start_intake` / `start_analyze` / `start_analyze_with_finalized_intent` /
   `start_challenger` 已经显式 `ensure_runner(wait_ready=True)`，不动；
   `create_accept._ensure_runner_pod_ready` 已是 lazy 路径，不动。

3. **测试**：
   - 单测 `orchestrator/tests/test_actions_runner_self_heal.py` 覆盖
     RSH-S1..S4：alive skip / NotFound recreate / Failed recreate / no controller。
   - 不写 integration test —— 全用 fake controller mock K8s API。

## 不在范围内

- watchdog / runner_gc 的策略调整（本 REQ 不动）。
- multi-pod / 跨 REQ pod 共享 PVC（架构改动，留给后续 REQ）。
- pod recreate 后的 clone 重做：`/workspace/source` 在 PVC 上，pod 重启后
  内容仍在；不需要重 clone。

## 影响

| 依赖 | 验证方式 |
|---|---|
| 现有 start_* action | 继续走原 `ensure_runner` 路径，未触及 |
| ensure_runner 409 idempotency | 已有，本 REQ 仅复用 |
| stage_runs / verifier_decisions schema | 不变，新增日志事件 `runner.lazy_recreate` |
| watchdog / runner_gc | 不变；recreate 跟它们正交 |
