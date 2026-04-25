# REQ-orch-noise-cleanup-1777078500: chore(orch): drop stale 77k9z58j project + silence runner_gc disk_check 403

## 问题

orchestrator 后台 task 在生产 INFO 日志里持续刷两条无意义噪声：

1. **`snapshot.list_failed project_id=77k9z58j`** —— 每 `snapshot_interval_sec`
   (默认 300s) 一次 WARN。`req_state` 里曾经写过 project `77k9z58j` 的行，但
   该 BKD project 已停用 / 已删 / 已迁移。snapshot loop 走 `SELECT DISTINCT
   project_id FROM req_state` 取项目列表，无脑去 BKD 拉 → 拿 4xx/网络错 →
   `log.warning("snapshot.list_failed", ...)` 反复打。

2. **`runner_gc` 磁盘探测 403** —— `node_disk_usage_ratio` 调 `core_v1.list_node`
   是 cluster-scoped 的 `nodes:list`，但 orchestrator 的 RBAC（runner-rbac.yaml）
   只在 `sisyphus-runners` namespace 内授 pod / pvc Role。每次 GC 周期
   (`runner_gc_interval_sec` 默认 900s) 都吃 `ApiException(403)`。当前已 catch
   到 `log.debug` 应当被 INFO 滤掉，但实测 dev (`log_level: DEBUG`) 时把日志带
   noise；以及 INFO 下 *kubernetes* 客户端底层会经 stderr/urllib3 log
   出 forbid 信息（部署日志 grep 出来过）。

两条都不影响功能，只是 churn。

## 方案

### 1. snapshot 加项目排除清单（config-driven）

新增 setting `snapshot_exclude_project_ids: list[str]`（env 名
`SISYPHUS_SNAPSHOT_EXCLUDE_PROJECT_IDS`，逗号分隔）。`sync_once` 把
`req_state` 里取出的 `project_id` 列表先过 exclude 集合，再发 BKD list-issues。

helm `values.yaml` 默认值放 `["77k9z58j"]`，把已知历史死项目排除掉。
将来再有死项目，运维加一个 env 即可，不动代码。

为啥不直接 SQL 删 `req_state` 行：
- `req_state` 是审计/state-machine truth；不删
- 死项目 row 永远不会再 transition；保留无害
- 排除只发生在 snapshot loop 这一处

### 2. runner_gc 把"RBAC 缺权限"识别为 first-class，silent 跳过

在 `gc_once` 内：
- catch `kubernetes.client.ApiException` 单独
- `e.status == 403`：判定为 RBAC 拒绝，记一个**进程级** flag
  `_DISK_CHECK_DISABLED`。第一次 hit 用 `log.warning` 打一句明显
  ("runner_gc.disk_check_rbac_denied …")，之后 GC tick 直接 short-circuit
  跳过 disk-check (`disk_pressure=False`)，不再发 list_node 请求、不再 log。
- 其他异常 (500 / 网络抖) 维持原来 `log.debug`，不卡 GC 主流程。

收益：
- 生产唯一一次明显告警，让 ops 知道"这个集群没给 nodes:list 权限，
  disk-pressure 兜底自动失活"
- 之后日志安静；不再每 15 min 一次刷
- 不再消耗 K8s API 配额做注定失败的请求

### 不做

- 不动 RBAC 给 nodes:list —— 这是 ops 政策选择，不在编排层范围
- 不引入"cluster-RBAC mode 开关"配置 —— first-call probe 已经够用
- 不删 `req_state` 历史行 —— 见上

## 取舍

- exclude list 设计成 list 而非"严格白名单"：白名单要求新项目接入前
  改 helm values 才能 snapshot；体验差。黑名单只解决"已知死项目"问题。
- 进程级 flag 不持久化：orchestrator 重启就重新 probe 一次。这是 feature
  not bug —— 重启时 ops 能感知 RBAC 是否补上。
- 进程级 flag 用模块级变量（不是 settings），因为运行时才知道是不是 403。
