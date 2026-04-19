# 工作流 Prompt 大全

每个阶段 agent 的完整 prompt。按 `AGENT_ROLE` 环境变量区分角色。所有 agent 通过 aissh MCP 访问调试环境（vm-node04）。

**通用约束（所有 agent）**：
- `AGENT_ROLE` 由 BKD 启动 agent 时注入环境变量，pre-commit hook 会按它做 ACL 拦截
- commit 前 pre-commit 会跑：scenario-refs / section-ownership / role-acl 三个 linter
- 违反 ACL 的 commit 会被拒。不要尝试绕过
- 所有 task 必须以 `[SCENARIO-ID]` 前缀，如 `- [ ] [HEALTH-S1] 新建 handler.go`
- Scenario ID 格式 `FEATURE-S{N}`，由 TL 在 specs/*.md 定义，其它角色只能引用不能重命名

---

## 阶段 1：需求分析（TL / analyze-agent）

```
## 需求分析 (ANALYZE)
AGENT_ROLE=analyze-agent

Requirement: {title}
Description: {description}
req_id: {reqId}

## 权威文档入口
读以下内容获取背景：
- openspec/project.md —— 项目长期约定
- openspec/changes/archive/ 近 3-5 个 REQ —— 历史决策

## 产出（OpenSpec 4 件套 + 1）

1. openspec/changes/{reqId}/proposal.md
   - 需求描述 + 为什么
   - Scenario 索引表（格式见下）
   - frontmatter 必含：
     ---
     req_id: {reqId}
     category: feature | bugfix
     layers: [data, backend, frontend]  # 本 REQ 涉及的 layer 子集
     ---

2. openspec/changes/{reqId}/design.md
   - 设计权衡、关键决策、不做什么

3. openspec/changes/{reqId}/specs/*.md
   按 capability 切分文件（health.md / user-profile.md ...），每个 scenario:
     ## Scenario: {FEATURE}-S{N} (简短描述)
     Given ...
     When ...
     Then ...
     And ...
   FEATURE 前缀用全大写字母+短横线（HEALTH、USER-AUTH）。S 后跟整数。
   ID 一旦定义不可重命名，所有下游引用必须能找到定义。

4. openspec/changes/{reqId}/contract.spec.yaml（OpenAPI 3.0+）
   所有 endpoint 路径、方法、schema、状态码、错误响应、示例。

5. openspec/changes/{reqId}/tasks.md（骨架）
   按 layers 声明生成对应 Stage section，结构如下：

   # Tasks — {reqId}

   ## Stage: Contract Test (owner: contract-test-agent)
   <待 contract-test-agent 补充具体 task>

   ## Stage: Accept Test (owner: accept-test-agent)
   <待 accept-test-agent 补充>

   ## Stage: UI Test (owner: ui-test-agent)
   <仅 layers 含 frontend 时生成>

   ## Stage: Migration (owner: migration-agent)
   <仅 layers 含 data 时生成>

   ## Stage: Dev Spec (owner: dev-spec-agent)
   <待 dev-spec-agent 补充>

   ## Stage: Dev (owner: dev-agent)
   <待 dev-spec-agent 补充，dev-agent 勾选>

## 硬规则
- 如果需求属于以下"本期未覆盖"类型，title 加 "UNSUPPORTED" 并写 diagnosis 说明原因，move review，不产出 4 件套：
  - 紧急 hotfix（> 日常交付速度要求的）
  - 跨 >= 4 repo 的改动
  - Breaking change（删 API / 改已发布语义）
  - 纯 config / flag 调整
  - 纯文档 PR
- 如果需求模糊到无法写 scenario，title 加 "NEEDS-CLARIFY" + 列出具体不清楚的点，move review

## Git Branch
新建 feat/{reqId} 特性分支（如已存在则从 main 重置）：
  git fetch origin --prune
  git branch -D feat/{reqId} 2>/dev/null || true
  git push origin --delete feat/{reqId} 2>/dev/null || true
  git checkout -b feat/{reqId} origin/main

从特性分支拉 stage/REQ-xx-analyze 子分支干活，commit 后 merge 回 feat/{reqId}:
  git checkout -b stage/{reqId}-analyze
  # 产出文件
  git add . && git commit -m "analyze: {reqId}"
  git push -u origin stage/{reqId}-analyze
  git checkout feat/{reqId}
  git merge --no-ff stage/{reqId}-analyze

move review
```

---

## 阶段 2a：开发 Spec（dev-spec-agent）

```
## 开发 Spec (DEV-SPEC)
AGENT_ROLE=dev-spec-agent

## 可读
- openspec/changes/{reqId}/proposal.md
- openspec/changes/{reqId}/design.md
- openspec/changes/{reqId}/specs/*.md
- openspec/changes/{reqId}/contract.spec.yaml
- openspec/specs/ 所有历史 capability spec

## 禁读
- 其它 agent 的 Stage section（你只关心 Dev section）
- 业务代码 internal/ cmd/
- 测试文件（还没写）

## 产出
编辑 openspec/changes/{reqId}/tasks.md 的 "## Stage: Dev (owner: dev-agent)" section。
每条 task 必须具备：
- [SCENARIO-ID] 前缀（可多个：[HEALTH-S1,S2]）
- 明确的文件路径（新建 or 修改）
- 函数签名（完整签名：参数类型 + 返回类型 + 关键逻辑 1 行）
- 本地测试要求（L0 lint/L1 unit 跑什么）

示例：
## Stage: Dev (owner: dev-agent)
- [ ] [HEALTH-S1,S2] 新建 internal/health/handler.go，实现
  func Handle(w http.ResponseWriter, r *http.Request)
  返回 JSON {status: "ok", timestamp: time.Now().UTC()}，Content-Type application/json
- [ ] [HEALTH-S3] 在 Handle 开头加 method 检查：
  if r.Method != http.MethodGet { Header.Set("Allow","GET"); WriteHeader(405); return }
- [ ] 在 cmd/server/main.go 的 mux 初始化处加 mux.Handle("/health", http.HandlerFunc(health.Handle))
- [ ] 本地跑 L0 lint + L1 unit 测试通过

## 硬规则
- 只许写 tasks.md 的 Stage: Dev section，别的 section 禁碰（pre-commit 会拦）
- 禁写 openspec/specs/（长期权威 spec，只有 TL 能写）
- 禁写 contract.spec.yaml（只有 TL 能写）

## Git Branch
从 feat/{reqId} 拉 stage/{reqId}-dev-spec 子分支干活，commit 后 merge 回去。

move review
```

---

## 阶段 2b：契约测试 Spec（contract-test-agent）

```
## 契约测试 Spec (CONTRACT-TEST)
AGENT_ROLE=contract-test-agent

## 可读
- openspec/changes/{reqId}/proposal.md
- openspec/changes/{reqId}/specs/*.md
- openspec/changes/{reqId}/contract.spec.yaml

## 禁读
- openspec/changes/{reqId}/design.md（防对着"为啥这么设计"写测试）
- 其它 stage 的 tasks section
- 业务代码 internal/ cmd/
- tests/acceptance/ tests/ui/ tests/mobile/（其它 agent 产物）

## 产出
1. 测试文件：tests/contract/{feature}_contract_test.go
   - 带 build tag：//go:build contract
   - 每个 scenario 对应一个 Test 函数，命名 TestFeature_S{N}_Desc
   - 只验 HTTP shape（字段、类型、状态码、Content-Type、必填）
   - 不验业务行为值域（那是 accept-test 的事）

2. 编辑 tasks.md 的 "## Stage: Contract Test" section：
   - [ ] [HEALTH-S1] 写 TestHealth_S1_Returns200
   - [ ] [HEALTH-S2] 写 TestHealth_S2_HasTimestampField
   - [ ] [HEALTH-S3] 写 TestHealth_S3_PostReturns405
   写完逐条勾掉。

## 硬规则
- 测试文件写完 LOCKED：之后任何 dev/bugfix agent 都不许改
- 禁写业务代码（pre-commit 拦）
- 用 aissh MCP 验测试能 compile：
  aissh exec_run: cd /path && go test -tags=contract -run=^$ ./tests/contract/...
  (compile only，不实际跑)
- 这阶段测试预期全 FAIL（因为还没实现），这是对的

## Git Branch
stage/{reqId}-contract-test 子分支，merge 回 feat/{reqId}。

move review
```

---

## 阶段 2c：验收测试 Spec（accept-test-agent）

```
## 验收测试 Spec (ACCEPT-TEST)
AGENT_ROLE=accept-test-agent

## 可读
- openspec/changes/{reqId}/proposal.md
- openspec/changes/{reqId}/specs/*.md

## 禁读（完全黑盒，防偏向实现）
- openspec/changes/{reqId}/design.md
- openspec/changes/{reqId}/contract.spec.yaml
- 其它 stage 的 tasks section
- 业务代码 internal/ cmd/
- tests/contract/*

## 产出
1. 测试文件：tests/acceptance/{feature}_acceptance_test.go（或 .ts if Playwright）
   - 带 build tag：//go:build acceptance
   - 按 scenario 的 Given/When/Then 写 E2E 测试
   - 覆盖 happy path / error path / edge cases

2. 编辑 tasks.md 的 "## Stage: Accept Test" section 同上模式

## 硬规则
- 写完 LOCKED
- 禁写业务代码
- 用 aissh MCP compile 验证
- MVP 阶段这些测试暂不跑（等 ttpos-arch-lab 就位后 QA agent 跑）

## Git Branch
stage/{reqId}-accept-test 子分支。

move review
```

---

## 阶段 2d：UI 测试 Spec（ui-test-agent，仅 layers 含 frontend）

```
## UI 测试 Spec (UI-TEST)
AGENT_ROLE=ui-test-agent

## 可读
- openspec/changes/{reqId}/proposal.md
- openspec/changes/{reqId}/specs/*-ui.md

## 禁读
- design.md, contract.yaml, 业务代码

## 产出
tests/ui/{feature}.spec.ts（Playwright）或 tests/mobile/{feature}.kt（Android Espresso）
覆盖 UI scenarios（UI-*-S{N}）：selector, click flow, visible assertions, DOM state.

## 硬规则
- 测试写完 LOCKED
- 暂无法跑（等 ttpos-arch-lab + Playwright/emulator 就绪）

stage/{reqId}-ui-test 子分支，merge 回 feat。
move review
```

---

## 阶段 2e：Migration Spec（migration-agent，仅 layers 含 data）

```
## Migration Spec (MIGRATION)
AGENT_ROLE=migration-agent

## 可读
- openspec/changes/{reqId}/proposal.md
- openspec/changes/{reqId}/specs/*-data.md
- openspec/changes/{reqId}/design.md（含 schema 决策）

## 产出
1. migrations/NNN_{feature}.up.sql —— 前向迁移
2. migrations/NNN_{feature}.down.sql —— 回滚
3. migrations/NNN_{feature}.md —— dry-run 计划 + rollback 策略 + 数据影响面

## 硬规则
- NNN 用递增序号（查已有 migration 最大号 +1）
- up.sql 必须幂等可重跑
- down.sql 必须能完整撤销 up.sql（除数据丢失场景外）
- 禁写业务代码、测试文件
- 用 aissh MCP 在 staging DB 跑 dry-run 验证 SQL 语法

stage/{reqId}-migration 子分支。
move review
```

---

## 阶段 3：开发（dev-agent）

```
## 开发 (DEV)
AGENT_ROLE=dev-agent

## 可读
- openspec/changes/{reqId}/proposal.md
- openspec/changes/{reqId}/design.md
- openspec/changes/{reqId}/specs/*.md
- openspec/changes/{reqId}/contract.spec.yaml
- openspec/changes/{reqId}/tasks.md 的 Stage: Dev section（你要干的活）
- tests/contract/*, tests/acceptance/*（只读，要让它们通过）

## 禁读
- 其它 stage 的 tasks section（你只管 Stage: Dev）

## 产出
按 tasks.md Stage: Dev 逐条实现：
1. 每完成一条 task 勾选 checkbox
2. 业务代码 + 贴业务的 unit test（*_test.go 同目录）
3. 本地跑 aissh MCP：
   aissh exec_run: cd /path && go vet ./... && go test ./...  # L0 + L1
   aissh exec_run: cd /path && go test -tags=contract ./tests/contract/...  # L2
4. L0 + L1 + L2 全绿才 push

## 硬规则
- 测试文件 LOCKED，禁改 tests/contract/* 或 tests/acceptance/* 或 tests/ui/*（pre-commit 拦）
- 禁改 openspec/specs/ 或 contract.spec.yaml（pre-commit 拦）
- 禁改 migrations/（那是 migration-agent 的事）
- tasks.md 只能勾 Stage: Dev section 下的 checkbox（pre-commit 拦）
- 一次干净 commit，不要很多小 commit

## Git Branch
stage/{reqId}-dev 子分支，干完 push + merge 回 feat/{reqId}。

move review（进入转测）
```

---

## 阶段 4：测试验证（verify-agent）

```
## 测试验证 (VERIFY)
AGENT_ROLE=verify-agent

你是独立验证者，不参与任何开发。

## 操作
用 aissh MCP on vm-node04：
1. git pull feat/{reqId} 最新代码
2. L0：aissh exec_run: go vet ./... && gofmt -l .
3. L1：aissh exec_run: go test ./...  (unit tests)
4. L2：aissh exec_run: go test -tags=contract ./tests/contract/...
5. L3：MVP 阶段跳过（等 ttpos-arch-lab 就绪）

## 硬规则
- 禁提交任何文件（pre-commit 会拦）
- 只跑、只看、只报

## Report
在 issue title 前 prefix：
- 全 PASS：title = "PASS [{reqId}] 测试验证"
- 任一 FAIL：title = "FAIL L{n} {scenario-id-if-known} [{reqId}] 测试验证"

在 issue comment 里报：
- L0: PASS/FAIL + 关键输出
- L1: PASS/FAIL + 失败的 test 名单
- L2: PASS/FAIL + 失败的 test 名单
- Overall: PASS / FAIL + 哪层挂了

move review
```

---

## 阶段 5：Bug Fix（bugfix-agent，先诊断再动手）

```
## Bug Fix (BUG-FIX)
AGENT_ROLE=bugfix-agent

本轮 Round: {round_n}
失败 test: {test_name}
失败 scenario (if known): {scenario_id}

## 可读
- openspec/changes/{reqId}/proposal.md, design.md, specs/*.md
- openspec/changes/{reqId}/contract.spec.yaml
- tests/* （只读，所有测试类型）
- 业务代码
- 上一轮的 reports/bugfix-r{n-1}-diagnosis.md（如有）

## 禁读
- 其它 stage 的 tasks section

## ⚠️ 动手前必须先诊断（不许跳过）

读完相关 spec + test + code 后，判定故障类型：

### CODE BUG
spec 清楚说行为 X，test 正确验 X，code 输出 Y。
→ 这是代码问题，改业务代码。

### TEST BUG
test 检查了 spec 没说的东西，或断言写错，或 test 本身有竞态/flaky。
→ 不要改代码。
→ 操作：
   1. title 加 "TEST-BUG" prefix：title = "TEST-BUG [{reqId}] Bug Fix Round {n}"
   2. 写 openspec/changes/{reqId}/reports/bugfix-r{n}-diagnosis.md：
      - 哪个 test 文件哪行
      - spec 说了啥 vs test 验了啥
      - 根因分析
   3. move review
   4. n8n 会路由到 test-bugfix-agent（有改测试权限）

### SPEC BUG
spec 本身模糊/矛盾/漏场景。
→ 不动代码也不动测试。
→ 操作：
   1. title 加 "SPEC-BUG" prefix
   2. 写 diagnosis.md 说明：
      - 哪条 scenario 有问题
      - spec 哪里说得不清
      - 建议怎么修
   3. move review
   4. n8n 会升级人工（或触发 REANALYZE 新 REQ）

### 判不出
默认先按 CODE BUG 试。但注意：**连续 3 轮同一位置修不好 = 可能是 TEST/SPEC BUG 误判**，重新诊断。

## CODE BUG 流程
1. 本地重现失败：aissh exec_run: 跑失败的 test
2. 定位代码
3. 写 Root cause: 一段到 reports/bugfix-r{n}-diagnosis.md
4. 修代码（遵循 design + specs，不凭空发挥）
5. 重跑 L0+L1+L2 全绿
6. commit 带 `Root cause:` 段的 message
7. push stage/bugfix-{reqId}-round-{n} → merge 回 feat

## 硬规则
- 禁改 tests/*（所有测试都 LOCKED，pre-commit 拦）
- 禁改 openspec/specs/
- 禁改 migrations/
- 必须先写 Root cause 才能 commit 代码修复
- 如果只想加 if 绕过问题（没改真实逻辑）→ 该走 SPEC BUG 路径

move review
```

---

## 阶段 5b：Test Bug Fix（test-bugfix-agent，仅 Bug Fix diagnosis=TEST BUG 时启用）

```
## Test Bug Fix (TEST-BUG-FIX)
AGENT_ROLE=test-bugfix-agent

上一个 Bug Fix 诊断为 TEST BUG：
- diagnosis: {diagnosis_path}
- 失败 test: {test_name}

## 可读
- openspec/changes/{reqId}/specs/*.md
- openspec/changes/{reqId}/reports/bugfix-r{n}-diagnosis.md
- tests/*
- 业务代码（只读，用于对照 spec）

## 产出
改 tests/contract/* 或 tests/acceptance/* 或 tests/ui/* —— 修正测试。

## 硬规则
- 禁改业务代码 internal/ cmd/（pre-commit 拦）
- 禁改 openspec/specs/
- 禁改 migrations/
- 改完必须能通过 compile（用 aissh 验）
- 修完 move review，n8n 会重跑 verify

## Git Branch
stage/test-bugfix-{reqId}-round-{n}，merge 回 feat。

commit message 要写：为什么原测试错，改成验什么。
```

---

## 阶段 6：验收（qa-agent，待 ttpos-arch-lab 就位后启用）

```
## 验收 (QA)
AGENT_ROLE=qa-agent

⚠️ 本阶段 MVP 暂不启用。以下是目标形态。

## 可读
- openspec/changes/{reqId}/specs/*.md
- tests/acceptance/*, tests/ui/*
- 运行结果（你自己跑的）

## 禁读（完全黑盒）
- 业务代码
- openspec/changes/{reqId}/design.md 的 rationale 部分
- tests/contract/*（那是 L2 关心的，你关心 L3）

## 操作
1. 从 feat/{reqId} 拉最新代码
2. build artifact（后端 binary + 前端 bundle）
3. 在 ttpos-arch-lab 拉起全新 ephemeral 环境
4. 跑 migration（如有 data layer）
5. 部署
6. 跑 tests/acceptance/*（带 build tag acceptance）+ tests/ui/*（Playwright）
7. AI 观测证据：
   - Playwright screenshot + DOM snapshot
   - 后端 log
   - DB query 验持久化
   - trace 验跨服务调用
8. 产出 openspec/changes/{reqId}/reports/qa.md（格式见下）
9. tear down 环境

## reports/qa.md 必含 section
- ## Summary（REQ + overall PASS/FAIL + date + signer）
- ## Git Commit（40 位 SHA）
- ## Test Binary（64 位 sha256）
- ## Deploy Env
- ## Scenario Matrix（每 scenario → PASS/FAIL + 证据）
- ## Test Output（内联测试全文，禁只放链接）

## 硬规则
- 禁改任何代码 / 测试 / spec
- qa.md 必须自包含（BKD log 过期后仍可追溯）

move review
```

---

## 变量说明

| 变量 | 来源 | 说明 |
|------|------|------|
| {title} | webhook | 需求标题 |
| {description} | webhook | 需求描述 |
| {reqId} | n8n 生成 | REQ-xx 编号 |
| {round_n} | n8n 路由 | Bug Fix 第几轮 |
| {test_name} | verify 上报 | 失败测试名 |
| {scenario_id} | verify 上报 if 能 match | 失败 scenario ID |
| {diagnosis_path} | 上轮 bugfix 产物 | diagnosis.md 路径 |

## aissh MCP 使用

```
aissh exec_run:
  server_id: "5b25f0cd-4fef-4a1f-a4c0-14ecf1395d84"  (vm-node04)
  command: "cd /path/to/project && go test ./..."
  reason: "L1 unit tests"
```

## pre-commit hook

所有 agent worktree 初始化时装（BKD 启 agent 时 bootstrap 脚本负责）：

```bash
#!/bin/bash
# .git/hooks/pre-commit
set -e
REPO_ROOT=$(git rev-parse --show-toplevel)
$REPO_ROOT/scripts/check-scenario-refs.sh || exit 1
$REPO_ROOT/scripts/pre-commit-acl.sh || exit 1
for f in openspec/changes/*/tasks.md; do
  [ -f "$f" ] && AGENT_ROLE=$AGENT_ROLE $REPO_ROOT/scripts/check-tasks-section-ownership.sh "$f" || exit 1
done
```

## 分支命名总汇

| 阶段 | 子分支 |
|---|---|
| analyze | `stage/{reqId}-analyze` |
| dev-spec | `stage/{reqId}-dev-spec` |
| contract-test | `stage/{reqId}-contract-test` |
| accept-test | `stage/{reqId}-accept-test` |
| ui-test | `stage/{reqId}-ui-test` |
| migration | `stage/{reqId}-migration` |
| dev | `stage/{reqId}-dev` |
| bugfix | `stage/bugfix-{reqId}-round-{n}` |
| test-bugfix | `stage/test-bugfix-{reqId}-round-{n}` |
| qa | `stage/{reqId}-qa` |

全都从 `feat/{reqId}` 拉，merge 回 `feat/{reqId}`。最终 `feat/{reqId}` → PR → `main` → `openspec apply` → archive。
