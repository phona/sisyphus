# Sisyphus

> AI-native CI 编排层。**调度 + 机械 checker + 度量** 三件套，让 agent-driven 研发流水线跑得起来、跑得稳、能被指标驱动改进。

权威架构：[docs/architecture.md](docs/architecture.md)
状态机权威：[docs/state-machine.md](docs/state-machine.md)
业务 repo 接入契约：[docs/integration-contracts.md](docs/integration-contracts.md)

## 核心哲学

- **薄编排，agent 决定** —— 路由 / 状态机 / checker 是 sisyphus；判 PR 内容、bug 该不该修是 agent。**永远不抢 AI 决定权**。
- **机械层 ≠ agent 层** —— 跑测试 / 轮 GHA / 校 schema 不绕 agent，sisyphus 是唯一裁判（M1 staging-test / M2 pr-ci-watch / M3 admission）。
- **失败先验，再试错** —— stage fail 不直接 bugfix，verifier-agent 主观判 pass / fix / escalate（M14b/c，3 路）。
- **指标驱动改进** —— 每条决策入 `stage_runs` / `verifier_decisions`，13 张 Metabase 看板（M7 + M14e）回答"哪条 prompt 该改"。
- **生产用最强模型** —— 不做"失败升级模型"自适应；haiku 只用于测试加速。

## 跟相邻系统的层级

| | 干啥 |
|---|---|
| sisyphus | **研发组织层**：编排 analyze → spec → dev → staging-test → pr-ci → accept → archive |
| GitHub Actions | **脚本 CI 层**：lint / unit / integration / sonar / image-publish。**互补不替代** —— pr-ci-watch 直接轮它的 check-runs |
| Pure prompt skill | **IDE 内 turbo dev tool**：Claude Code 单 turn 内的高级 prompt。不同层；sisyphus 在它之上做组织 |

## 分工

| 角色 | 实现 | 职责 |
|---|---|---|
| **orchestrator** | Python（K8s Deployment） | 状态机 + 路由 + watchdog + GC + 指标采集 |
| **机械 checker** | Python（runner pod 内 exec / GitHub REST） | 客观事实：测试退码 / CI 绿不绿 |
| **stage agent** | BKD agent + Jinja2 prompt | analyze / spec / dev / accept / done-archive |
| **verifier-agent** | BKD agent + 12 个 verifier/{stage}\_{trigger} 模板 | 主观决策：pass / fix / escalate（输出 decision JSON，3 路） |
| **fixer-agent** | BKD agent + bugfix.md.j2（过渡） | 改一类东西：dev fixer 改业务码 / spec fixer 改 spec |

## Stage 流（happy path 八段，含可选 INTAKING）

```
[intent:intake → intake(多轮 BKD chat 澄清 + finalized intent JSON)]  ← 可选，物理隔离 brainstorm
  ↓ intake.pass（新建 analyze issue）
intent:analyze → analyze(写 proposal/design/tasks) → specs(×2 并行) → dev(1~N 并行, push feat/REQ-x + 真开 PR)
  → staging-test(机械: kubectl exec runner make ci-unit-test && make ci-integration-test) → pr-ci-watch(机械: GitHub REST 轮 check-runs)
  → accept(make accept-env-up + agent 跑 FEATURE-A* + make accept-env-down 必跑) → archive → DONE
```

**入口选择**：`intent:intake` → INTAKING（推荐：不熟悉的仓）；`intent:analyze` → ANALYZING（跳过澄清）。

任何 stage（含 staging-test / pr-ci / accept）失败入 `REVIEW_RUNNING`，verifier-agent 3 路决策：
- `pass` → 推下一 stage
- `fix` + `fixer` → 起 dev / spec fixer，回 `REVIEW_RUNNING` 再判
- `escalate` → 终态 ESCALATED（**包括所有 flaky / 基础设施抖动**：sisyphus 不再机制性兜 retry，由人重起）

state 转移完整定义在 [orchestrator/src/orchestrator/state.py](orchestrator/src/orchestrator/state.py)（13 ReqState × 18 Event × 30+ transition）。

## 技术栈

- **orchestrator**: Python 3.12+，async（asyncpg + httpx + kubernetes asyncio）
- **持久化**: Postgres（req_state 行级 CAS / event_log / stage_runs / verifier_decisions / artifact_checks）
- **runner**: K8s Pod (`sisyphus-runners` namespace) + per-REQ PVC，privileged + DinD + fuse-overlayfs
- **AI agent**: BKD（≥0.0.65）跑 Claude Agent。**走 REST 不走 MCP**（PR #1）
- **prompt**: Jinja2 模板（`orchestrator/src/orchestrator/prompts/`）
- **观测**: Postgres + Metabase（不引 Prometheus / OTel —— 数据形状是事件不是 metric）
- **代码托管**: GitHub（pr-ci-watch checker 直接走 REST API）
- **部署**: Helm chart on K3s

## 项目结构

```
sisyphus/
├── orchestrator/             # 核心 Python 服务
│   ├── src/orchestrator/
│   │   ├── state.py / router.py / engine.py / webhook.py
│   │   ├── actions/          # 15 个 stage 推进动作
│   │   ├── checkers/         # M1/M2/M3/M11 机械 checker
│   │   ├── prompts/          # stage agent + verifier/* + _shared/
│   │   ├── k8s_runner.py / bkd.py / watchdog.py / runner_gc.py
│   │   └── store/            # req_state CAS / db pool / 各表写入
│   └── migrations/           # 0001_init / 0002_views / 0003_artifact_checks / 0004_stage_runs / 0005_verifier_decisions
├── runner/                   # Dockerfile (Flutter) + go.Dockerfile + entrypoint.sh
├── scripts/                  # ACL/scenario lint 脚本（runner 镜像挂这些）
├── observability/
│   ├── schema.sql / agent_quality.sql
│   ├── queries/sisyphus/     # 13 条 Metabase SQL (Q1-Q13)
│   └── sisyphus-dashboard.md
├── docs/                     # 见下方文档索引
└── values/                   # helm values（postgresql / metabase）
```

## 文档索引

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 架构权威：哲学、角色分工、流程图、stage 契约（含 mermaid） |
| [docs/state-machine.md](docs/state-machine.md) | 状态机权威：state / event / transition 表 + stateDiagram |
| [docs/integration-contracts.md](docs/integration-contracts.md) | sisyphus ↔ 业务 repo 契约（Makefile target、env、JSON 输出） |
| [docs/observability.md](docs/observability.md) | 观测设计哲学（Postgres + Metabase） |
| [observability/sisyphus-dashboard.md](observability/sisyphus-dashboard.md) | 13 张 Metabase 看板 + SQL（M7 + M14e） |
| [docs/prompts.md](docs/prompts.md) | 各阶段 agent prompt 总览（按 role） |
| [docs/api-tag-management-spec.md](docs/api-tag-management-spec.md) | BKD issue tag 命名规范（router 依赖） |

## 开发规范

- 用中文交流
- 代码改完检查问题；测试能跑就跑
- 每个流程节点必须有存在的理由 —— 不搞花里胡哨
- 不抢 AI 决定权 —— 加新 stage / checker 之前先问"这真该 sisyphus 干，还是 agent 干"
- 接 BKD 走 REST，不走 MCP
- BKD 并行派 agent 必加 `useWorktree=True`（PR #18 起默认开）
- 目标是加速 agent-driven 开发，让"无人值守"真正可观测、可改进
