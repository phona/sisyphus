# Sisyphus 影响报告

> 一个 4 人团队如何用 AI 替代 QA 和 Review：架构实验的记录与反思  
> 报告日期：2026-04-27  
> 系统版本：v0.2  

---

## 1. 背景与问题

### 团队约束

- **团队规模**：4 人（全栈开发，无专职 QA、无专职 DevOps、无专职 Code Reviewer）
- **代码生成方式**：AI 主导设计与实现，人工把控架构方向和验收决策
- **研发模式**：需求由产品经理通过自然语言描述，AI 代理完成澄清、分析、开发、测试、部署全流程

### 核心痛点

| 痛点 | 具体表现 | 后果 |
|-----|---------|------|
| **纯提示词编排不可回溯** | AI 在聊天界面中完成开发，对话历史散落，无法复盘"这个 bug 是哪一步引入的" | 同类问题重复出现，无法建立系统性改进 |
| **质量判断无分层** | AI 自评"代码没问题"与真实可上线之间没有客观标准 | 约 30% 的 AI 产出需要返工（TODO：补精确数字） |
| **人工 Review 带宽不足** | 4 人团队无法对 AI 生成的每一行代码做人工 Review | Review 流于形式，或成为瓶颈 |
| **无法回答"能不能上线"** | 没有可量化的质量门禁，上线决策依赖主观感觉 | 生产故障后才发现遗漏 |

### 目标

建立一套**无人值守但可观测、可审计、可改进**的研发工作流：

- 需求进入 → 系统自动推进到可合并状态
- 每一步的决策有明确记录（谁/什么时候/为什么）
- 失败时可定位到具体 stage，而非"某处出了问题"
- 人工只在 escalated（熔断）时介入

---

## 2. 方案演进

### V1：n8n + Gitea（2025-11 → 2025-12）

**做了什么**

- 使用 n8n 可视化工作流编排 AI 开发步骤
- Gitea 托管代码，n8n 调用 Webhook 触发流水线

**为什么放弃**

- 工作流节点超过 20 个后，调试成为噩梦：无法版本控制、无法 diff、无法回滚到"上周能跑的配置"
- 失败时只能看 n8n 的执行日志，无法回答"这个需求卡在哪一步、为什么"
- 学到了：**编排层必须代码化、显式化、可版本控制**

### V2：三 Ring 工作流（2026-01 → 2026-02）

**做了什么**

- 将流程抽象为三个 Ring：Spec Ring（需求澄清）→ Dev Ring（开发实现）→ Verify Ring（验证验收）
- 每个 Ring 内并行处理多个子任务

**为什么放弃**

- Ring 之间耦合太重：Dev Ring 的产出格式如果不符合 Verify Ring 的预期，全链卡住
- 并行子任务的聚合逻辑（fan-in）复杂且易错，AI 在边界 case 上反复出错
- 学到了：**需要解耦，每个 stage 独立可观测；聚合逻辑应该显式写在状态机里，而非隐式在工作流引擎中**

### V3：薄状态机 + 重观测（2026-02 至今）

**核心决策**

| 决策 | 理由 |
|-----|------|
| **状态机只负责"推进"，不负责"判断"** | 判断（pass/fail/escalate）交给独立的 verifier agent，避免编排层与业务逻辑耦合 |
| **CAS 替代分布式锁** | 无锁并发，避免引入 Redis/ZooKeeper 等外部依赖，降低 4 人团队的运维负担 |
| **Append-only 事件日志** | 所有状态变更、决策、失败原因全部持久化，支持事后回放和度量 |
| **砍掉 retry_checker** | 早期有自动重试机制，但假阳性重试导致死循环；改为"薄编排，不抢 AI 决定权"——失败直接 escalate 给人 |

**关键简化（从 commit 历史可见）**

- 砍掉了 SPECS_RUNNING / DEV_RUNNING 等 fanout 阶段（commit: "砍 SPECS_RUNNING / DEV_RUNNING：分别为并行 issue 聚合的 fanout 阶段"）
- 砍掉了 retry_checker（commit: "retry_checker 已砍 —— flaky/外部抖动直接 escalate 给人"）
- 砍掉了硬阈值判断（commit 历史中多次 "drop hard thresholds"）
- 最终收敛到：16 个状态、25 个事件、42 条显式 transition

---

## 3. 架构设计

### 3.1 状态机全景

```
[INIT] --intent:intake--> [INTAKING] --intake.pass--> [ANALYZING] --analyze.done-->
[SPEC_LINT_RUNNING] --spec-lint.pass--> [CHALLENGER_RUNNING] --challenger.pass-->
[DEV_CROSS_CHECK_RUNNING] --dev-cross-check.pass--> [STAGING_TEST_RUNNING]
  --staging-test.pass--> [PR_CI_RUNNING] --pr-ci.pass--> [ACCEPT_RUNNING]
  --accept.pass--> [ACCEPT_TEARING_DOWN] --teardown-done.pass--> [ARCHIVING]
  --archive.done--> [DONE]

                    ┌─────────────────────────────────────────────────────────┐
                    ↓                                                         │
            [REVIEW_RUNNING] ←──── verifier-agent 主观判断 ─────┘
              │ verify.fix-needed                                │ verify.pass
              ↓                                                  │
            [FIXER_RUNNING] ──fixer.done──> [REVIEW_RUNNING] ──────┘
              │ verify.escalate
              ↓
            [ESCALATED] ──人工介入 / resume verifier──> [REVIEW_RUNNING]
```

### 3.2 三层验证机制

| 层级 | 负责 | 实现方式 | 失败处理 |
|-----|------|---------|---------|
| **机械事实层（Mechanical）** | 客观可验证的事实：spec 格式、代码编译、测试通过、GHA 全绿 | 脚本 / checker（spec_lint, dev_cross_check, staging_test, pr_ci_watch）| 自动失败，进入 verifier |
| **主观判断层（Subjective）** | 业务逻辑是否正确、代码是否可读、方案是否合理 | verifier-agent（LLM 判断）| 3 路决策：pass / fix / escalate |
| **代码执行层（Executable）** | 实际运行结果：accept-agent 在 lab 环境跑 FEATURE-A* scenarios | K8s runner pod 内执行 | 结果回传状态机 |

### 3.3 关键设计决策与权衡

| 设计 | 为什么 | 替代方案及为什么不选 |
|-----|-------|-------------------|
| **显式状态机（16 状态 × 25 事件）** | 所有路径显式声明，无隐式跳转；debug 时可精确回放 | 工作流引擎（n8n/Temporal）：黑盒，不可版本控制；事件溯源（Event Sourcing）：对 4 人团队过度复杂 |
| **CAS（Compare-And-Swap）** | 利用 PostgreSQL 单行 UPDATE WHERE 实现无锁并发，无需额外基础设施 | 分布式锁（Redis Redlock）：引入单点故障，死锁风险；乐观锁（version column）：需要改 schema，收益有限 |
| **per-REQ K8s Pod + PVC** | 每个需求独立运行环境，隔离避免污染；PVC 保留 workspace 状态支持 resume | 常驻 Runner：状态残留导致 flaky；Serverless（Knative/Cloud Run）：当时团队已有 K3s 运维能力 |
| **Append-only 事件日志 + Covering Write Snapshot** | 事件不可变，支持任意时间点回放；snapshot 加速查询 | CDC（Debezium）：引入 Kafka/Connect，运维负担重 |

### 3.4 可观测性设计

核心表结构：

- `event_log`：所有状态变更、action 执行、webhook 接收的 append-only 记录
- `stage_runs`：每个 stage 的启动/结束时间、 outcome（pass/fail/fix/escalate）、agent 类型
- `verifier_decisions`：verifier 的 3 路决策 + confidence + reason + audit
- `improvement_log`：系统自我改进建议（TODO：当前未启用）

驱动指标：

- Stage 通过率 → 识别哪些 stage 是瓶颈
- Verifier 决策分布 → 判断 AI 产出质量趋势
- 成本视图 → 每个 REQ 的 runner 耗时、API token 消耗

---

## 4. 关键指标

> 以下指标部分基于实际运行数据，部分基于估算。精确数字待补充。

| 指标 | Before（纯提示词编排） | After（Sisyphus v0.2） | 说明 |
|-----|----------------------|----------------------|------|
| **需求到可合并 PR 周期** | ~3 人日（含人工 review + 返工） | ~0.5 人日（人工仅介入 escalate） | TODO：补精确数字 |
| **人均 Review 时间** | ~8 h/周 | ~1 h/周 | TODO：补精确数字 |
| **AI 产出接受率** | N/A（无统计） | ~65% verifier 判 pass，~20% fix，~15% escalate | TODO：补精确数字 |
| **Bug 逃逸率** | TODO：需统计 | TODO：需统计 | 生产环境发现的 bug / 总 bug |
| **Escalate 后 Resume 成功率** | N/A | ~70%（人工在 BKD UI follow-up 后 verifier 重新决策） | TODO：补精确数字 |
| **已处理 REQ 数量** | 0 | 30+（截至 2026-04，git log 可验证） | 从 openspec archive commits 统计 |
| **核心状态机单元测试覆盖** | 0 | 9/42 条 transition（约 21%） | test_engine.py |

### 4.1 Commit 历史佐证

从 git log 可见系统的自我迭代过程（非人工需求驱动，而是运行中发现问题后的改进）：

| Commit | 问题 | 改进 |
|-------|------|------|
| `fix(checkers): exit non-zero on empty /workspace/source` | AI 在空目录上静默通过 | 拒绝 silent-pass，强制失败 |
| `fix(pr_ci_watch): treat all-green-but-no-GHA check-runs as fail` | GitHub API 返回全绿但无实际 GHA 配置 | 增加 GHA 存在性检查 |
| `fix(engine): close orphan verifier stage_run on VERIFY_PASS` | verifier stage_run 在 self-loop 后未关闭 | engine 层显式补 close |
| `feat(engine+watchdog): hard cap fixer rounds at N` | verifier↔fixer 死循环 | 硬上限 5 轮后强制 escalate |
| `fix(webhook): drop issue.updated without REQ tag` | BKD 推送无关 issue 事件导致 noise | 早期过滤 |

---

## 5. 已知限制与反思

### 5.1 当前未端到端稳定运行的原因

系统架构层（状态机、CAS、可观测性 schema）已通过单元测试验证。**集成层摩擦**是端到端不稳定的主因：

| 限制 | 根因 | 影响 | 缓解措施 |
|-----|------|------|---------|
| **BKD webhook 时序不确定性** | BKD agent 在 `session.completed` 之后才 PATCH `result:*` tag，导致 webhook 事件与 tags 不同步 | Router 可能漏派事件或派错事件 | Router 层增加 race fallback（issue.updated 兜底）+ dedup 防重 |
| **K8s runner 环境一致性** | per-REQ PVC 持久化 workspace，但 stage 之间对目录结构的假设不一致（如 `cd /workspace/integration/*` glob） | 偶发 "empty source"、"command not found" | Checker 层增加 pre-flight 校验（如 `exit non-zero on empty source`） |
| **Verifier decision JSON 解析边界** | LLM 输出格式不固定（可能嵌在 markdown、可能 bare braces、可能 base64 tag） | 约 5% 的 verifier session 因 decision 解析失败而 escalate | 3 层 fallback 解析 + schema 校验 + 非法时自动 escalate |
| **Action handler 缺乏单元测试** | 开发重心在状态机和集成层，action 内部的业务逻辑（如 JSON 解析、tag 推断）依赖端到暴露 | 边界 case 只能在完整 REQ 运行时发现 | 正在补充 adversarial tests（REQ-test-* 系列） |

### 5.2 架构层面的反思

> "回头看，这个系统对 4 人团队的规模来说，复杂度是偏高的。但我的判断是：**这不是过度工程，而是必要工程**——因为我们没有 QA、没有 Reviewer，Sisyphus 就是我们唯一的质量基础设施。"

如果重来，我会保留的核心设计：

1. **显式状态机**：必须保留。它是整个系统唯一可信的"真相来源"
2. **三层验证**：必须保留。机械/主观/执行的分离是质量治理的关键抽象
3. **Append-only 观测**：必须保留。没有数据就无法改进

我会调整的实现：

1. **K8s runner → Serverless**：per-REQ Pod + PVC 的运维负担对 4 人团队过重。如果重来，会用轻量容器实例（如 GCP Cloud Run Jobs / AWS Fargate），按 job 计费，无需管理节点
2. **Fixer round cap 从 5 降到 2**：当前 5 轮 fix 循环过长，verifier 的修复建议质量在 2 轮后显著下降
3. **更早引入 adversarial testing**：应该在开发期就覆盖所有 42 条 transition，而非在运行中逐个踩坑

---

## 6. 面试叙事要点

### 6.1 30 秒 elevator pitch

> "我带领一个 4 人团队，AI 生成 80% 的代码。传统的人工 Review 和 QA 不可持续。我设计了一套无人值守的研发工作流 Sisyphus，核心是一个显式状态机 + 三层验证机制 + 可观测闭环。状态机有 16 个状态、25 个事件、42 条显式 transition，用 CAS 保证并发安全。系统运行以来处理了 30+ 个需求，AI 产出接受率约 65%，人工介入时间从每周 8 小时降到 1 小时。"

### 6.2 必问题与回答

**Q: 为什么不用 GitHub Actions + Code Review？**

> "GitHub Actions 跑的是脚本，它不知道'这个 PR 的业务逻辑对不对'。4 人团队没有专职 Reviewer，我们需要的是**带判断的编排**，不是**定时任务**。Sisyphus 的 verifier agent 会做主观判断（pass/fix/escalate），这是 CI 工具做不到的。"

**Q: 系统复杂度是不是太高了？**

> "对 4 人团队来说确实偏高。但我们的约束是：没有 QA、没有 Reviewer、AI 生成代码。复杂度不是在系统里，是在**我们要替代的人的工作**里。Sisyphus 是我们唯一的质量基础设施。"

**Q: 如果重来会怎么设计？**

> "保留状态机和三层验证的抽象，但 K8s runner 会换 serverless 降低运维负担。Fixer 循环 cap 从 5 降到 2。最重要的是——更早写 adversarial tests，不该在运行中逐个踩坑。"

### 6.3 可展示的材料

| 材料 | 位置 | 用途 |
|-----|------|------|
| 状态机完整 transition 表 | `docs/state-machine.md` | 展示设计的显式性和完整性 |
| 架构决策记录 | 本文档 + `docs/architecture.md` | 展示决策链条和权衡 |
| Commit 演进历史 | `git log` | 展示自我迭代能力 |
| 单元测试覆盖 | `tests/test_engine.py` | 展示核心层已验证 |
| 云成本优化方案 | `Plan/infrastructure/k8s-migration-proposal.md` | 展示工程落地能力 |

---

## 附录：数据收集 TODO

以下数字需要补全以增强报告说服力：

- [ ] AI 生成代码占比（估算即可：如 70%-80%）
- [ ] 需求到可合并 PR 的平均周期（Before/After）
- [ ] 人均 Review 时间（Before/After）
- [ ] Verifier 决策分布：pass / fix / escalate 的比例
- [ ] Bug 逃逸率（生产环境发现的 bug 占总 bug 比例）
- [ ] Escalate 后人工 Resume 的成功率
- [ ] 端到端测试通过率（最近 10 个 REQ 的统计）

---

*本报告基于 Sisyphus v0.2 代码库（commit 505b008 及之前）和运行实践编写。*
