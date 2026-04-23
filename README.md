# Sisyphus

> AI-native CI 编排层 —— 给 agent-driven 研发流水线做调度、兜底与度量。

Sisyphus 不写代码、不审内容、不抢 AI 决定权。它是一层薄薄的"调度 + 机械 checker + 度量"，让 analyze / spec / dev / accept 这些 agent 各自把活干完，并且**跑得起来、跑得稳、能被指标驱动改进**。

## 三个核心价值

1. **自愈** —— stage 失败不直接停，verifier-agent 主观判 pass / fix / retry / escalate；fixer agent 自动闭环；watchdog 兜底卡死。
2. **范式强制** —— admission gate 卡 manifest schema、PR 必须有、useWorktree 默认开；mechanical checker（staging-test / pr-ci / accept env）独立于 agent 报告，sisyphus 是唯一裁判。
3. **观测指导改进** —— 每个 stage 入 `stage_runs`、每条 verifier 决策入 `verifier_decisions`，13 张 Metabase 看板回答"哪条 prompt 该改 / 哪个 stage 钻牛角尖 / 哪类 fixer 修不动"。

## 跟相邻系统的关系

| | sisyphus | GHA | Pure prompt skill (Claude Code 原生) |
|---|---|---|---|
| 层级 | 研发组织层 AI 流水线 | 脚本 CI 层 | IDE 内 turbo dev tool |
| 决策权 | 调度 + 路由 + 度量 | 跑命令 + 报红绿 | agent 自己 |
| 关系 | **跟 GHA 互补**：pr-ci-watch 直接轮 GHA 结果 | sisyphus 的"无脑跑测试"工具箱 | sisyphus 在它之上做组织 |

## 当前架构（v0.2 + M14）

```
人 ──intent:analyze──▶ analyze-agent
                         │
                         ▼
            specs (contract + acceptance, 并行)
                         │
                         ▼  ← admission: manifest schema 卡死
                       dev-agent (开 PR + 写 manifest.pr.number)
                         │
                         ▼
        ┌── staging-test (sisyphus kubectl exec runner pod 跑 manifest.test.cmd)
        │            │
        │   pass ────┘
        │            │
        ▼            ▼
     verifier ◀── pr-ci-watch (sisyphus 直调 GitHub REST 轮 check-runs)
        │            │
        │   pass ────┘
        │            ▼
        │         accept-agent (sisyphus 先 ci-accept-env-up，agent 跑 FEATURE-A*)
        │            │
        │   pass ────┘
        ▼            ▼
       fixer       teardown (ci-accept-env-down，必跑)
        │            │
        └────────────▶ archive (合 PR + 关 issue)
```

详见 [docs/architecture.md](docs/architecture.md)（含完整 mermaid 流程 + 角色分工）和 [docs/state-machine.md](docs/state-machine.md)（state 转移表）。

## 目录结构

```
sisyphus/
├── orchestrator/             # Python orchestrator（核心）
│   ├── src/orchestrator/
│   │   ├── state.py          # 状态机 + 事件枚举
│   │   ├── router.py         # webhook → Event 翻译
│   │   ├── engine.py         # 事件分发 + action 执行
│   │   ├── webhook.py        # BKD webhook 入口
│   │   ├── actions/          # stage 推进动作（start_analyze / fanout_specs / create_dev / ...）
│   │   ├── checkers/         # 机械 checker（staging_test / pr_ci_watch / manifest_validate）
│   │   ├── prompts/          # Jinja2 模板（含 12 个 verifier/{stage}_{trigger}）
│   │   ├── schemas/          # manifest.json (draft-07)
│   │   ├── k8s_runner.py     # 每 REQ 一个 Pod + PVC 的 controller
│   │   ├── bkd.py            # BKD 客户端（REST 默认，MCP 兜底）
│   │   ├── watchdog.py       # M8 卡死兜底
│   │   ├── runner_gc.py      # M10 即时 cleanup
│   │   └── store/            # req_state CAS / db pool / verifier_decisions
│   └── migrations/           # Postgres schema (0001 - 0005)
├── runner/                   # per-REQ runner 镜像
│   ├── Dockerfile            # Flutter SDK 全家桶（~5GB）
│   ├── go.Dockerfile         # 纯 Go 精简版
│   └── entrypoint.sh         # DinD 启动
├── scripts/                  # runner 内挂的合约脚本
│   ├── validate-manifest.py  # manifest schema 校验（admission 用）
│   ├── check-scenario-refs.sh
│   └── check-tasks-section-ownership.sh
├── observability/
│   ├── schema.sql
│   ├── queries/sisyphus/     # 13 条 Metabase SQL（M7 + M14e）
│   └── sisyphus-dashboard.md
├── docs/                     # 见下方文档索引
└── values/                   # helm values（postgresql / metabase）
```

## 快速上手

orchestrator 自托管 + per-REQ runner pod：

```bash
# 1. 部署 orchestrator + Postgres + Metabase（K3s 集群）
helm upgrade --install sisyphus charts/sisyphus -f values/my-values.yaml

# 2. 触发一个 REQ（在 BKD 项目内开 issue + 打 intent:analyze tag）
#    BKD 发 webhook → orchestrator router → start_analyze action
#    后续全自动，直到 done 或 escalated

# 3. 看进度
kubectl logs -n sisyphus deploy/sisyphus-orchestrator -f
```

详细部署 / 故障排查见 [docs/architecture.md](docs/architecture.md) 和 [observability/README.md](observability/README.md)。

## 接入新业务 repo

业务 repo 需要提供一组 `make` target 跟 sisyphus 对齐 ——
所有契约见 [docs/integration-contracts.md](docs/integration-contracts.md)。最小集：

| target | 谁调 | 用途 |
|---|---|---|
| `make ci-unit-test` / `ci-integration-test` | staging-test checker | dev 推完跑 |
| `make ci-accept-env-up` | sisyphus pre-accept | helm install lab |
| `make ci-accept-env-down` | teardown_accept_env | accept 完必跑 |

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 架构权威：哲学、角色分工、流程图、stage 契约 |
| [docs/state-machine.md](docs/state-machine.md) | 状态机权威：state / event / transition 表 + 可视化 |
| [docs/integration-contracts.md](docs/integration-contracts.md) | sisyphus ↔ 业务 repo 契约（Makefile target、env、JSON 输出） |
| [docs/observability.md](docs/observability.md) | 观测设计哲学（Postgres + Metabase） |
| [observability/sisyphus-dashboard.md](observability/sisyphus-dashboard.md) | 13 张 Metabase 看板 + SQL（M7 + M14e） |
| [docs/prompts.md](docs/prompts.md) | 各阶段 agent prompt 总览（按 role） |
| [docs/api-tag-management-spec.md](docs/api-tag-management-spec.md) | BKD issue tag 命名规范（router 依赖） |
