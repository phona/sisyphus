# Sisyphus 架构设计

> 契约驱动 + 测试先行 + 对抗验证 的 AI 无人值守开发平台。

## 核心哲学

- **契约驱动（CDD）**：OpenAPI + Scenario 为唯一真相源
- **测试先行（TDD）**：测试先写、LOCKED 后不可改
- **对抗验证**：开发 / 测试 / 验收分属独立 agent，角色边界靠 pre-commit 硬拦截
- **分层职责**：OpenSpec 管"规格如何演进"，GitHub Issues 管"运行时 bug 如何修"

```
有人                            无人
需求分析 → N 路并行 Spec → Dev → [转测] → verify ⇄ Bug 循环 → 验收 → apply/archive
         (按 layer 动态展开)    ↑ 分界 ↑                  ↑ 熔断 ↑
```

**关键分界**：**push 到 `feat/REQ-xx` 之前**的 FAIL 是"任务未完成"（checkbox 未勾），**push 之后**的 FAIL 是"bug"（GH issue 立案）。

## 真实 REQ 是跨层复合的

一个典型业务需求（如"用户头像上传"）往往同时改：
- **data** 层：DB migration（表/列变更）
- **backend** 层：HTTP API
- **frontend** 层：UI 组件 + 交互
- **跨层**：端到端用户旅程

所以流程里的"N 路并行 Spec"按 **proposal.md 声明的 layers** 动态展开，不是固定 3 路。

### proposal.md frontmatter 声明 layers

```yaml
---
req_id: REQ-10
category: feature
layers:
  - data        # 涉及 DB schema 变更
  - backend     # 涉及 HTTP API
  - frontend    # 涉及 UI
---

# REQ-10: 用户头像上传
...
```

### n8n 按 layers 动态展开 Spec 阶段

| Layer | 并行 Spec 阶段 | 交付物 | LOCKED |
|---|---|---|---|
| backend | Contract Test Spec | `tests/contract/*.go`（`//go:build contract`） | ✅ |
| frontend | UI Test Spec | `tests/ui/*.spec.ts`（Playwright）或 `tests/mobile/*.kt`（Android Espresso） | ✅ |
| data | Migration Spec | `migrations/NNN_xxx.{up,down}.sql` + `migrations/NNN_xxx.md`（dry-run + rollback 计划） | ✅ |
| 全 REQ | Accept Test Spec | `tests/acceptance/*.ts`（Playwright E2E）或 `*.go`（纯 API 时） | ✅ |
| 全 REQ | Dev Spec | tasks.md 的 `Stage: Dev` section | - |

**Gate 按 layers 算**：涉及哪些 layer 就等哪些 Spec 都 review：
- 只 backend：2 路（Contract + Dev Spec）
- backend + frontend：4 路（Contract + UI + Accept + Dev Spec）
- 全三层：5 路（Contract + UI + Migration + Accept + Dev Spec）

### Spec 文件也按 layer 切分

```
openspec/changes/REQ-10/
├── proposal.md
├── design.md
├── specs/
│   ├── avatar-api.md          ← backend layer 行为（HEALTH-S1、UPLOAD-S1...）
│   ├── avatar-ui.md           ← frontend layer 行为（UI-UPLOAD-S1...）
│   └── avatar-data.md         ← data layer 约束（DATA-S1 列非空、DATA-S2 索引）
├── contract.spec.yaml
├── tasks.md
└── reports/qa.md
```

Scenario ID 的 FEATURE prefix 可跨 layer 用不同命名（`UPLOAD-S*`, `UI-UPLOAD-S*`, `DATA-UPLOAD-S*`）或统一用 capability slug。

## 目录结构

```
openspec/
├── project.md                           ← 稳定项目约定（技术栈、代码规范、领域术语）
├── specs/health.md                      ← 权威 capability spec，apply 时追加 Change History
└── changes/
    ├── REQ-06/                          ← OpenSpec 原生 4 件套 + reports/
    │   ├── proposal.md                  ← OpenSpec 原生：需求 + Scenario 索引
    │   ├── design.md                    ← OpenSpec 原生：设计权衡
    │   ├── specs/health.md              ← OpenSpec 原生：spec-delta
    │   ├── tasks.md                     ← OpenSpec 原生：多 Stage section 分工
    │   ├── contract.spec.yaml           ← 非 OpenSpec，OpenAPI 3.0+ 契约
    │   └── reports/                     ← 非 OpenSpec，pipeline 产物
    │       └── qa.md                    ← 验收签收（仅 QA 阶段启用后才有）
    └── archive/REQ-05/                  ← OpenSpec 原生：已 apply 的 change

scripts/                                 ← sidecar linter（~200 行 bash）
├── check-scenario-refs.sh               ← Scenario ID 引用完整性
├── check-tasks-section-ownership.sh     ← tasks.md section 属主校验
├── pre-commit-acl.sh                    ← 按 AGENT_ROLE 拦截跨权限改动
├── bugfix-commit-hook.sh                ← （规划）bug fix commit 自动注入 issue 引用
└── check-qa-evidence.sh                 ← （规划）qa.md 自包含审计检查
```

## OpenSpec 原生 vs 自定义约定

### 原生（OpenSpec 直接支持）

| 文件 | 用途 | 写者 |
|---|---|---|
| `changes/REQ-xx/proposal.md` | 需求描述 + Scenario 索引 | TL (ANALYZE agent) |
| `changes/REQ-xx/design.md` | 设计权衡 | TL |
| `changes/REQ-xx/specs/*.md` | spec-delta | TL |
| `changes/REQ-xx/tasks.md` | 任务清单 | 各 Stage agent 协作 |
| `openspec/specs/*.md` | 长期权威 capability spec | apply 流程 |
| `openspec/project.md` | 稳定项目约定 | TL（项目演进时） |
| `openspec/changes/archive/` | 已完成 change 归档 | archive 流程 |

### 自定义约定（OpenSpec 不认）

| 约定 | 强制手段 | 作用 |
|---|---|---|
| Scenario ID 命名 `FEATURE-S{N}` | `check-scenario-refs.sh` | 跨阶段产物统一标识 |
| Task 必须以 `[SCENARIO-ID]` 前缀 | `check-scenario-refs.sh` | 每条 task 追溯到需求 |
| `tasks.md` 的 `## Stage: X (owner: Y)` section | `check-tasks-section-ownership.sh` | 多 agent 协作边界 |
| `contract.spec.yaml`（OpenAPI）放在 change 目录 | pre-commit-acl.sh（仅 TL 可写） | Contract Test 机读 schema |
| `reports/qa.md` 自包含 | `check-qa-evidence.sh`（规划） | 验收审计 BKD log 失效后仍可追溯 |
| 文件级 read/write ACL 按角色 | `pre-commit-acl.sh` | 对抗验证边界 |
| Push 到 feat 分支 = 转测分界线 | n8n workflow 规则 | 区分"任务未完成" vs "bug" |
| Bug 走 GH Issues 单一事实源 | n8n 主动创建/关闭（规划） | 与 OpenSpec change 解耦 |
| 回归问题 = 新 REQ 不回滚 | n8n ANALYZE 规则 | OpenSpec 演进 append-only |

## 阶段与交付物

| # | 阶段 | 触发条件 | 输入 | 交付物 | LOCKED | OpenSpec 认 |
|---|---|---|---|---|---|---|
| 1 | **需求分析** (TL) | 新 REQ 进入 | `project.md` + 近期 archive | proposal（含 layers 声明）/ design / specs/ / contract.spec.yaml / tasks.md 骨架 | - | ✅ (前 4) |
| 2a | **开发 Spec** | 所有 REQ | specs + contract + design | tasks.md `Stage: Dev` section（每条 task 含文件路径+函数签名） | - | ✅ |
| 2b | **契约测试 Spec** | `layers` 含 backend | contract.yaml + specs | `tests/contract/*.go` | ✅ | ❌ |
| 2c | **UI 测试 Spec** | `layers` 含 frontend | specs/*-ui.md | `tests/ui/*.spec.ts`（Playwright）或 `tests/mobile/*.kt` | ✅ | ❌ |
| 2d | **Migration Spec** | `layers` 含 data | specs/*-data.md | `migrations/NNN_*.up.sql`, `.down.sql` + `.md`（dry-run 计划） | ✅ | ❌ |
| 2e | **验收测试 Spec** | 所有 REQ | specs（禁 contract.yaml + 禁代码） | `tests/acceptance/*.ts`（Playwright E2E）或 `*.go`（纯 API 时） | ✅ | ❌ |
| 3 | **开发** | Gate 通过后 | design + specs + contract + tasks Dev section + LOCKED 测试（只读） | 业务代码 + unit test + tasks 勾选完成 | - | 业务代码 ❌，tasks ✅ |
| 3.5 | **CI 核验 (dev 自检)** | Dev session.completed | dev 分支代码 | BKD issue `## CI Result` block + `ci:pass/fail` + `target:unit` tag | - | ❌ |
| 4 | **测试验证 (Verify / CI integration)** | CI 自检 pass 后 | feat/REQ 分支 | `make ci-integration-test` output + `ci:pass/fail` + `target:integration` tag | - | - |
| 5 | **Bug Fix Round N** | Verify/QA FAIL | Bug tracker + design + specs + 代码 + 测试（只读） | 改业务代码（**先诊断 CODE/TEST/SPEC bug**，见下） | - | - |
| 5b | **Test Bug Fix** | Bug Fix 诊断为 TEST BUG | 原失败测试 + specs | 改测试 | - | - |
| 6 | **验收 (QA)**（待 ttpos-arch-lab 就位） | Verify PASS 后 | specs + acceptance 测试（禁代码 + 禁 design rationale） | 部署临时环境（含 Playwright/Android emulator）→ 跑 E2E → `reports/qa.md` → tear down | - | ❌ |

### Scenario ID 约定

`{FEATURE}-S{N}`：FEATURE 大写字母+数字+短横线（如 `HEALTH`, `USER-AUTH`），S 后跟整数。

- ANALYZE 在 `specs/*.md` 定义：`## Scenario: HEALTH-S1 (简短描述)`
- 其它阶段只能引用，不能重命名
- pre-commit + CI 跑 `check-scenario-refs.sh` 保证每个引用都能找到定义

### tasks.md 结构

```markdown
# Tasks — REQ-06

## Stage: Contract Test (owner: contract-test-agent)
- [ ] [HEALTH-S1] 写 TestHealth_S1_Returns200
- [ ] [HEALTH-S2] 写 TestHealth_S2_HasTimestampField

## Stage: Accept Test (owner: accept-test-agent)
- [ ] [HEALTH-S1] 写 TestHealth_S1_E2E
- [ ] [HEALTH-S4] 写 TestHealth_S4_ConcurrentP99

## Stage: Dev Spec (owner: dev-spec-agent)
- [ ] [HEALTH-S1,S2] 设计 handler.go 签名
- [ ] [HEALTH-S3] 设计 method 检查

## Stage: Dev (owner: dev-agent)
- [ ] [HEALTH-S1,S2] 实现 Handle()
- [ ] [HEALTH-S3] 实现 method 拒绝
- [ ] 路由注册
- [ ] 本地 L0/L1 通过
```

**约束**（pre-commit 强制）：
- 每条 task 必须以 `[SCENARIO-ID]` 前缀（一条 task 可覆盖多：`[HEALTH-S1,S2]`）
- Section heading 必须含 `(owner: <agent-role>)`
- 每个 agent 只能勾自己 section 下的 checkbox
- 所有 task 勾完 + 本地 L0/L1 绿 → push → 进转测

### 阶段 4: 测试验证执行层级

- **L0** lint/compile
- **L1** unit test（贴业务代码的 `*_test.go`）
- **L2** contract test（`//go:build contract`，需 local HTTP listener 或 mock）
- **L3** acceptance test（`//go:build acceptance`，需 ttpos-arch-lab ephemeral env）

**MVP 阶段**：Verify 只跑 L0-L2。L3 + QA 阶段等 ttpos-arch-lab 数据裁剪 + 环境拉起能力就绪后接入。

### 阶段 5: Bug Fix 先诊断再动手

Bug Fix agent 必须先判故障类型，不能直接改代码：

| 类型 | 判定 | 处理 |
|---|---|---|
| **CODE BUG** | spec 说 X，test 正确验 X，code 输出 Y | Dev Bug Fix agent 改代码（禁改测试） |
| **TEST BUG** | test 检查了 spec 没说的东西；test 断言写错；test 有竞态 | title 加 `TEST-BUG`，写 `reports/bugfix-r{n}-diagnosis.md`，move review → n8n 路由到 **Test Bug Fix agent**（有改测试权限） |
| **SPEC BUG** | spec 本身模糊/矛盾/漏场景 | title 加 `SPEC-BUG`，写 diagnosis，move review → n8n 升级人工（或触发 REANALYZE 新 REQ） |
| **判不出** | 先按 CODE BUG 试；连续 N 轮同一位置不收敛 → 强制重新诊断 | 按 CODE BUG 默认 |

Prompt 硬性要求动手前走完这个诊断流程，pre-commit 仍然拦改测试（保证 TEST BUG 路径必须绕到 Test Bug Fix）。

### 阶段 6: QA（规划中，多层 E2E 验收）

QA 不是"签字"，是**真正的独立端到端验收**，借助环境里的观察工具（Playwright / Android emulator / log / DB query）跨层验证：

```
1. 从 feat/REQ-10 拉最新代码
2. build artifact（后端 binary + 前端 bundle）
3. 在 ttpos-arch-lab 拉起全新 ephemeral 环境
   包含：DB + 后端 + 前端 + Playwright / Android emulator runner
4. 跑 migration（如有 data layer）
5. 部署本次构建到该环境
6. 跑 tests/acceptance/*（E2E 跨层）
7. AI 观测（按 layer 组合）：
   - Playwright screenshot + DOM snapshot → 判页面对不对
   - 后端 log → 判请求链路
   - DB query → 判数据持久化
   - trace → 判跨服务调用
8. 记录证据到 qa.md（内联全文、截图 base64、DOM diff、不引用外部 log）
9. tear down 环境
10. 签收 qa.md → move review
```

`reports/qa.md` 必含字段（`check-qa-evidence.sh` 强制）：
- `## Summary`（REQ + overall PASS/FAIL + date + signer）
- `## Git Commit`（40 位 SHA）
- `## Test Binary`（64 位 sha256）
- `## Deploy Env`
- `## Scenario Matrix`（每条 scenario → PASS/FAIL + 证据）
- `## Test Output`（内联测试全文，禁链接）

## 角色读写权限矩阵

| Agent | 可写 | 禁写（硬拦截） | 禁读（软约束） |
|---|---|---|---|
| **TL (ANALYZE)** | 全 openspec/ | — | — |
| **Dev Spec** | tasks.md 的 Dev section | 测试、业务代码、openspec/specs/ | — |
| **Contract Test** | `tests/contract/*` + tasks Contract Test section | 业务代码、acceptance 测试、openspec/specs/ | — |
| **UI Test** | `tests/ui/*` / `tests/mobile/*` + tasks UI Test section | 业务代码、其它测试、openspec/specs/ | — |
| **Migration** | `migrations/*.sql`, `migrations/*.md` + tasks Migration section | 业务代码、测试、openspec/specs/ | — |
| **Accept Test** | `tests/acceptance/*` + tasks Accept Test section | 业务代码、其它测试、openspec/specs/ | `contract.spec.yaml` / 业务代码 / design.md |
| **Dev** | 业务代码 + unit test + tasks Dev 勾选 | 测试 LOCKED、openspec/specs/ | — |
| **Verify** | 无（不 commit） | 全部 | — |
| **CI Runner** | 无（不 commit，只写 BKD issue） | 全部 repo 文件 | — |
| **Bug Fix** (code) | 业务代码 | 测试 LOCKED、openspec/specs/ | — |
| **Test Bug Fix** | `tests/contract/*` / `tests/acceptance/*` / `tests/ui/*` | 业务代码、openspec/specs/ | — |
| **QA**（规划） | `reports/qa.md` | 业务代码、测试、openspec/specs/ | 业务代码、design.md rationale |

**硬拦截**靠 `pre-commit-acl.sh`（CI 也跑）。**软约束**靠 prompt 明令。未来可加 git sparse-checkout 物化禁读。

## 分支策略

```
main
└── feat/REQ-06                              ← REQ 特性分支
    ├── stage/REQ-06-analyze                 ← TL 子分支
    ├── stage/REQ-06-dev-spec                ← dev-spec agent
    ├── stage/REQ-06-contract-test           ← contract-test agent
    ├── stage/REQ-06-accept-test             ← accept-test agent
    ├── stage/REQ-06-dev                     ← dev agent
    └── bugfix/REQ-06-round-N                ← 每轮 Bug Fix 独立分支
```

每个 agent：从 `feat/REQ-06` 拉子分支 → 干活 → merge 回特性分支。rebase 冲突自己解，复杂冲突升级。验收 PASS → PR → merge `main` → `openspec apply` → archive。

## n8n 编排

**无状态路由器**，按**大类 workflow** 分入口（每类 workflow 内部再按 layer 动态展开 Spec 阶段）：

- `/v2` — Feature 入口：新增 / 扩展能力（可跨 data/backend/frontend 任意组合）
- `/v2-hotfix` — Hotfix 入口（规划）：紧急修复，精简 pipeline，不走完整 Spec 阶段
- `/v2-maintenance` — 运维入口：压缩归档、基础设施变更，禁动 `specs/`
- `/bkd-events` — BKD `session.completed` 路由（共享）

**大类 workflow**（入口）定行为总体 shape；**layers 声明**（proposal.md）决定内部 Spec 阶段怎么动态展开。这两个正交。

### 共享 vs 变体

```
n8n workflows/
├── shared/                      ← 所有 workflow 共用
│   ├── bug-fix-diagnose.json    ← Bug Fix 三类诊断子流程
│   ├── circuit-breaker.json     ← 熔断
│   ├── escalation.json          ← 升级人工
│   └── openspec-apply.json      ← apply + archive
└── variants/
    ├── v3-feature-entry.json    ← /v2（已实现）
    ├── v3-hotfix-entry.json     ← /v2-hotfix（规划）
    ├── v3-maintenance-entry.json← /v2-maintenance（已实现）
    └── v3-bkd-events.json       ← /bkd-events 路由器
```

路由器实现见 `charts/n8n-workflows/v3-events.json`。harness 在 `testcases/test-events-harness.sh`。

### 熔断

Bug Fix 轮次 ≥ 3 → escalate（加 label + assign 人工 + 停自动 Bug Fix）。

- **MVP**：按 BKD issue 的 `round-N` tag 数累计
- **GH 集成后**：按 `gh issue list --state all --label REQ-xx scenario:XX-S{n}` 数累计

## 转测后 Bug 流程（规划，待 GH 集成）

1. verify/accept FAIL → n8n 调 `gh issue create`，label: `bug,REQ-06,scenario:HEALTH-S3,round:N`
2. n8n 写 `.bugfix-context` 到 worktree（含 issue 号），创 BKD "Bug Fix" issue
3. bugfix agent 启动，commit 时 `bugfix-commit-hook.sh` 自动 append `Bugfix-Issue: N`
4. push 后 n8n 重跑 verify
5. PASS → n8n 主动 `gh issue close #N`（不信 commit message）
6. 累计 round 触发熔断

**MVP 阶段**：仍用 BKD issue 做 bug tracker（title 里带 round-N 标签），等 GH 集成就位后迁。

## 回归问题 ≠ 回滚

`openspec apply` 是 append-only，历史不撤销。发现回归/漏场景 → 开新 REQ（REQ-06b）修正 spec → 走完整 pipeline。

## Scope 与不支持的场景

**当前 MVP 支持**：新增 / 扩展**业务能力**类 REQ，可跨 data / backend / frontend 任意 layer 组合（前提是各 layer 的测试工具已就位：后端 Go + HTTP ✅、前端 Playwright 🚧、Android emulator 🚧、DB migration 🚧）。

**ANALYZE agent 判"不支持"立即 abort 的场景**：
- **紧急 hotfix**：要求 5 分钟内上线的修复 → 走 `/v2-hotfix`（规划中）或人工
- **跨服务协调**（一个 REQ 改多个 repo / 微服务）→ 当前不支持
- **Breaking change / API v2**：需要版本迁移 + 兼容层 → 当前不支持，拆 REQ 或人工
- **纯 config/flag 调整**：不需要 test 阶段 → 走 PR
- **纯文档 PR**：不进 pipeline → 走 PR
- **实验性 feature flag / 灰度 / A/B**：需要 flag 管理和 metrics 观察 → 当前不支持

## 已知未解问题

1. **并发 REQ 对同一 capability spec 冲突**：REQ-06 / REQ-07 都改 `health.md`，PR 合入顺序决定谁 rebase。目前依赖人工 serialize。
2. **ttpos-arch-lab ephemeral 环境 + Playwright/Android 工具链**：数据裁剪中，还没 ready。
3. **Token 成本监控**：n8n 暂未汇总 agent token 消耗。
4. **Regression suite 持续化**：archive 的测试不会自动纳入以后的全仓 regression suite。

## 未来扩展点（按优先级）

1. **ttpos-arch-lab 临时环境 + QA agent 接入**（最急，带来真 e2e 验收）
   - 含 Playwright / Android emulator / DB / 后端容器
   - AI 能 driver UI（点击、输入、观察）+ 读 DB + 读 log
2. **Migration / UI Test / 多 layer Spec 阶段**（workflow + linter 配套）
3. **`/v2-hotfix` 入口**：按 hotfix 模式精简 pipeline（跳 3-spec 等）
4. **GH Issues bug 流程替换 BKD bug tag**（需 GitHub token + gh CLI 在 worktree 可用）
5. **sparse-checkout 物化 read ACL**（对抗强度上限）
6. **NFR spec 分层 `specs/*.nfr.md`**（有 NFR 需求出现时）
7. **design.md 拆 constraints / rationale**（Test agent 偷看 rationale 造成 bug 时）
8. **diff 不相交熔断**（相比简单计数更智能，有足够样本再调）
