# Sisyphus

> AI-native CI 编排层。**调度 + 机械 checker + 度量** 三件套，让 agent-driven 研发流水线跑得起来、跑得稳、能被指标驱动改进。

权威架构：[docs/architecture.md](docs/architecture.md)
状态机权威：[docs/state-machine.md](docs/state-machine.md)
业务 repo 接入契约：[docs/integration-contracts.md](docs/integration-contracts.md)

## 核心哲学

- **薄编排，agent 决定** —— 路由 / 状态机 / checker 是 sisyphus；判 PR 内容、bug 该不该修是 agent。**永远不抢 AI 决定权**。
- **机械层 ≠ agent 层** —— 跑测试 / 轮 GHA / 校 schema 不绕 agent，sisyphus 是唯一裁判（4 个 checker：spec_lint / dev_cross_check / staging_test / pr_ci_watch）。
- **失败先验，再试错** —— stage fail 不直接 bugfix，verifier-agent 主观判 pass / fix / escalate（M14b/c，3 路）。
- **指标驱动改进** —— 每条决策入 `stage_runs` / `verifier_decisions`，18 条 Metabase SQL（Q1–Q18，跨 M7 + M14e + fixer-audit + silent-pass detector）回答"哪条 prompt 该改"。
- **生产用最强模型** —— 不做"失败升级模型"自适应；haiku 只用于测试加速。
- **runner = 只读 checker** —— K8s runner pod 只 clone 源、跑测试、跑 accept-env-*；**所有 GH 写操作（push / PR create / merge）都打回 BKD Coder workspace 执行**，由 Coder gh auth 处理，跟 runner secret 完全无关。runner GH_TOKEN 应是 fine-grained PAT, Contents: Read-only。详见 [docs/architecture.md §8](docs/architecture.md)。

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
| **机械 checker** | Python（runner pod 内 exec / GitHub REST） | 客观事实：openspec validate / lint 退码 / 测试退码 / CI 绿不绿 |
| **stage agent** | BKD agent + Jinja2 prompt | intake / analyze / challenger / accept / done-archive（M17 起 sisyphus 不再起 spec / dev BKD 子 agent，由 analyze-agent 自决拆 sub-issue） |
| **verifier-agent** | BKD agent + 14 对 verifier/{stage}\_{trigger}.md.j2（analyze / accept / challenger / dev_cross_check / pr_ci / spec_lint / staging_test 各 success+fail）+ `_audit` / `_decision` / `_header` 共享 partial | 主观决策：pass / fix / escalate（输出 decision JSON，3 路） |
| **fixer-agent** | BKD agent + bugfix.md.j2（过渡） | 改一类东西：dev fixer 改业务码 / spec fixer 改 spec |

## Stage 流（happy path 九段，含可选 INTAKING）

```
[intent:intake → intake(多轮 BKD chat 澄清 + finalized intent JSON)]  ← 可选，物理隔离 brainstorm
  ↓ intake.pass（新建 analyze issue）
intent:analyze → analyze(全责交付：写 spec + 业务码 + push feat/REQ-x + 开 PR；自决拆 sub-issue)
  → spec-lint(机械: openspec validate + check-scenario-refs.sh) → challenger(M18: 黑盒读 spec 写 contract test)
  → dev-cross-check(机械: BASE_REV make ci-lint) → staging-test(机械: kubectl exec runner make ci-unit-test && make ci-integration-test)
  → pr-ci-watch(机械: GitHub REST 轮 check-runs) → accept(make accept-env-up + agent 跑 FEATURE-A* + make accept-env-down 必跑) → archive → DONE
```

**入口选择**：`intent:intake` → INTAKING（推荐：不熟悉的仓）；`intent:analyze` → ANALYZING（跳过澄清）。

任何 stage（含 staging-test / pr-ci / accept）失败入 `REVIEW_RUNNING`，verifier-agent 3 路决策：
- `pass` → 推下一 stage
- `fix` + `fixer` → 起 dev / spec fixer，回 `REVIEW_RUNNING` 再判
- `escalate` → 终态 ESCALATED（**包括所有 flaky / 基础设施抖动**：sisyphus 不再机制性兜 retry，由人重起）

state 转移完整定义在 [orchestrator/src/orchestrator/state.py](orchestrator/src/orchestrator/state.py)（17 ReqState × 27 Event × 30+ transition）。

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
│   │   ├── actions/          # 12 个 stage 推进动作（create_*/start_*/done_archive/escalate/teardown_accept_env） + helper (_clone/_skip/_verifier/_integration_resolver)
│   │   ├── checkers/         # 4 个机械 checker：spec_lint / dev_cross_check / staging_test / pr_ci_watch
│   │   ├── prompts/          # stage agent (intake/analyze/challenger/accept/done_archive/staging_test/pr_ci_watch/bugfix) + verifier/* + _shared/
│   │   ├── k8s_runner.py / bkd.py / watchdog.py / runner_gc.py
│   │   └── store/            # req_state CAS / db pool / 各表写入
│   └── migrations/           # 0001_init / 0002_observability_views / 0003_artifact_checks / 0004_stage_runs / 0005_verifier_decisions / 0006_add_verifier_audit / 0007_add_event_seen_processed_at / 0009_artifact_checks_flake
├── runner/                   # Dockerfile (Flutter) + go.Dockerfile + entrypoint.sh
├── scripts/                  # ACL/scenario lint 脚本（runner 镜像挂这些）
├── observability/
│   ├── schema.sql / agent_quality.sql
│   ├── queries/sisyphus/     # 18 条 Metabase SQL (Q1-Q18; M7 + M14e + fixer-audit + silent-pass)
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
| [observability/sisyphus-dashboard.md](observability/sisyphus-dashboard.md) | 18 条 Metabase SQL + 看板布局（Q1–Q18：M7 + M14e + fixer-audit + silent-pass） |
| [docs/prompts.md](docs/prompts.md) | 各阶段 agent prompt 总览（按 role） |
| [docs/api-tag-management-spec.md](docs/api-tag-management-spec.md) | BKD issue tag 命名规范（router 依赖） |
| [docs/cookbook/](docs/cookbook/) | 按 lab 形态分给 `accept-env-up/down` 实现样板（mobile lab 见 `ttpos-arch-lab-accept-env.md`） |

## 开发规范

- 用中文交流
- 代码改完检查问题；测试能跑就跑
- 每个流程节点必须有存在的理由 —— 不搞花里胡哨
- 不抢 AI 决定权 —— 加新 stage / checker 之前先问"这真该 sisyphus 干，还是 agent 干"
- 接 BKD 走 REST，不走 MCP
- BKD 并行派 agent 必加 `useWorktree=True`（PR #18 起默认开）
- 目标是加速 agent-driven 开发，让"无人值守"真正可观测、可改进
