# REQ-silence-runner-gc-rbac-1777123726: fix(runner_gc): truly silence repeated disk_check_rbac_denied warning

## 问题

REQ-orch-noise-cleanup-1777078500（PR #66）引入了进程级 flag `_DISK_CHECK_DISABLED`，
意图是 runner_gc 第一次拿到 K8s 403（namespace-scoped Role 没 cluster 级 nodes:list）
之后只 warn 一次，之后所有 GC tick 直接 short-circuit、不再 probe、不再 log。

但生产里 `runner_gc.disk_check_rbac_denied` **仍然是 warn 级别**，alert 看板 / loki
查询 `level=warning` 都会反复抓到它。原因不是 flag 失效（同进程内确实只发一次），
而是 orchestrator pod 在日常迭代中频繁滚动（helm upgrade、livenessProbe 重启、
deployment 配置变更），**每次新进程启动都会再 warn 一次**。从运维视角看就是：
"WARNING runner_gc.disk_check_rbac_denied" 反复出现 —— 跟 PR #66 fix 之前的体感
没差别。

操作员实际诉求：

- RBAC 缺 cluster 级 nodes:list 是 sisyphus 在 namespace-scoped Role 部署的**预期
  配置**（chart 的 ClusterRole 是可选的），不是异常。
- 这条 log 不该污染 warning 流，让真异常被淹没。
- 同时**保留**信息可观测性 —— 运维偶尔需要确认 disk-pressure emergency purge 是否
  被禁，以解释为什么磁盘吃紧时没触发紧急清理。

## 根因

之前的 fix 只在**单一进程生命周期**里去重；忽略了 pod 重启次数才是这条 log 的
真正放大器。WARNING 级别假设了"这是值得告警关注的事件"，跟实际语义（一次性配置
检测结果）不符。

## 方案

### 把 `runner_gc.disk_check_rbac_denied` 降级到 INFO

`orchestrator/src/orchestrator/runner_gc.py:gc_once` 里 403 分支：

```python
log.warning("runner_gc.disk_check_rbac_denied", ...)   # 旧
log.info("runner_gc.disk_check_rbac_denied", ...)      # 新
```

- INFO 级别下 alert 看板/loki 默认不报警，但 raw log 仍可 grep。
- 现有 `_DISK_CHECK_DISABLED` flag 行为不变：同进程内仍然只发一次。
- 跨进程重启的"看上去重复"问题：从 warning 流移除等价于"truly silenced"。

### 不拆 helper / 不引新 setting

考虑过的替代方案：

1. **持久化 flag 到 DB / 文件**：跨进程去重，重新部署也只 log 一次。代价高
   （引数据库依赖到 GC 路径），过度设计。
2. **降到 DEBUG**：完全消失，operator 失去"我的 disk-pressure 是不是被禁了"的可见
   性。
3. **用环境变量声明 RBAC mode 主动跳过 probe**：要求 helm/operator 配置变更，门槛
   太高，而且 sisyphus 自检比手配更可靠。

INFO 是最低代价：单行改动，行为兼容，运维仍能在 raw log 中确认。

### 同步更新合约和测试

- `openspec/specs/orch-noise-cleanup` ORCHN-S4：requirement / scenario 把 "warning"
  改为 "info"，措辞从 "MUST emit exactly one warning" 改为 "MUST emit at most
  one info-level log per process" 强调"info 级别 + 进程级 dedup"。
- `orchestrator/tests/test_contract_orch_noise_cleanup.py::test_orchn_s4_first_403_warns_and_disables`：
  断言改成查 `log_level == "info"`；test 名字改成 `test_orchn_s4_first_403_logs_info_and_disables`。
- `orchestrator/tests/test_runner_gc.py::test_disk_check_403_disables_after_first_warn`：
  无需断 level（只查 event 名在 stdout 中），test 名字保留或顺手改成 `..._first_log_disables`，
  不影响行为。

## 影响

- runtime: WARNING → INFO，不影响 GC 调度逻辑、不影响 disk-pressure emergency purge
  路径、不影响 retention-only fallback。
- alert 看板：少一条噪声 warning。
- 文档/运维：这条 INFO 出现表示 namespace-scoped Role 没 nodes:list；不出现表示
  RBAC 完整或 K8s 不可用。

## 验证

- `pytest orchestrator/tests/test_runner_gc.py orchestrator/tests/test_contract_orch_noise_cleanup.py -k disk` 全过。
- `openspec validate openspec/changes/REQ-silence-runner-gc-rbac-1777123726` 通过。
- 手动 staging：把 orchestrator ServiceAccount 的 ClusterRoleBinding 拔掉重启，确认
  `runner_gc.disk_check_rbac_denied` 出现在 INFO 级别且后续 GC tick 没再 log。
