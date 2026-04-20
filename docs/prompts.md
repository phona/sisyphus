# 工作流 Prompt 大全

每个阶段 agent 的完整 prompt。按 `AGENT_ROLE` 环境变量区分角色。所有 agent 通过 aissh MCP 访问调试环境（vm-node04）。

**通用约束（所有 agent）**：
- `AGENT_ROLE` 由 BKD 启动 agent 时注入环境变量，pre-commit hook 会按它做 ACL 拦截
- commit 前 pre-commit 会跑：scenario-refs / section-ownership / role-acl 三个 linter
- 违反 ACL 的 commit 会被拒。不要尝试绕过
- 所有 task 必须以 `[SCENARIO-ID]` 前缀，如 `- [ ] [HEALTH-S1] 新建 handler.go`
- Scenario ID 格式 `FEATURE-S{N}`，由 TL 在 specs/*.md 定义，其它角色只能引用不能重命名

**Title 是描述，不是调度信号**：BKD issue title 仅供人类阅读，**禁止**用作判断依据。所有阶段路由 / 结果分支都走 BKD issue 的 `tags` 字段。**move review 之前必须更新 tags 反映本阶段结果**——见下方"结果 tag 协议"。

## 结果 tag 协议（重要）

n8n /bkd-events 路由完全依赖 tags。每个阶段除已有的阶段 tag（`analyze` / `verify` / `bugfix` 等）和 `REQ-xx` tag 外，**完成时必须追加结果 tag**：

| 阶段 | 结果 | 必加 tag |
|---|---|---|
| analyze | 正常 | （无） |
| analyze | 不支持 | `decision:unsupported` |
| analyze | 需澄清 | `decision:needs-clarify` |
| analyze | 含 layer 信息 | `layer:backend` / `layer:frontend` / `layer:data`（按 proposal.md frontmatter 实际声明加）|
| verify | 全 PASS | `result:pass` |
| verify | 任一 FAIL | `result:fail` + `level:L0` / `L1` / `L2`（哪层挂的）|
| accept | PASS | `result:pass` |
| accept | FAIL | `result:fail` |
| **bugfix (DEV-FIX)** | CODE BUG 默认 | （无 diagnosis tag，也无 result tag）|
| **bugfix (DEV-FIX)** | 诊断为 TEST BUG | `diagnosis:test-bug`（不加 result）|
| **bugfix (DEV-FIX)** | 诊断为 SPEC BUG | `diagnosis:spec-bug`（不加 result）|
| **test-fix (TEST-FIX)** | 改完测试 | （无 result tag；可选 diagnosis tag）|
| **reviewer** | 采纳了某边 merge 到 feat | `result:pass` |
| **reviewer** | 两边都不过 / 弃权 | `result:fail` |
| **ci-runner** | `make` exit 0 | `ci:pass` + `target:unit` / `target:integration`（按本次跑的 target）|
| **ci-runner** | `make` exit != 0 | `ci:fail` + `target:unit` / `target:integration`|
| 其它（dev-spec / dev / spec 类）| —— | （无结果 tag，路由只看阶段 tag）|

加 tag 用 BKD MCP `update-issue`（保留现有 tag，追加新 tag）：

```
get-issue → 拿 current tags
→ update-issue(tags=[...current, "result:pass"]) → move review
```

**title 可以加 `PASS ` / `FAIL ` 等前缀给人看**（推荐，方便 BKD UI 一眼辨识），**但 n8n 不读 title**。tag 是唯一调度真相。

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
- 如果需求属于以下"本期未覆盖"类型，**update-issue 加 tag `decision:unsupported`**（title 可加 "UNSUPPORTED " 前缀供人识别），写 diagnosis 说明原因，move review，不产出 4 件套：
  - 紧急 hotfix（> 日常交付速度要求的）
  - 跨 >= 4 repo 的改动
  - Breaking change（删 API / 改已发布语义）
  - 纯 config / flag 调整
  - 纯文档 PR
- 如果需求模糊到无法写 scenario，**update-issue 加 tag `decision:needs-clarify`**（title 可加 "NEEDS-CLARIFY " 前缀），列出具体不清楚的点，move review
- 正常完成：按 proposal.md frontmatter 实际声明的 layers，**update-issue 追加 `layer:backend` / `layer:frontend` / `layer:data` 对应 tag**（n8n All3? gate 据此动态展开 expected specs）

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

## 阶段 3.5 / 4：CI 核验（ci-runner-agent）

**定位**：独立第三方机械执行器。**不改任何文件、不做业务判断、不写 diagnosis**。只做一件事：在调试环境跑指定 `make ci-*` target，把 exit code + 关键输出原样写回 BKD issue。

**何时用**：
- `dev` 阶段 session.completed → n8n 创 ci-runner issue，`CI_TARGET=unit`（跑 `ci-lint` + `ci-unit-test`）作为"dev 自检 gate"
- `verify` 阶段 → n8n 创 ci-runner issue，`CI_TARGET=integration`（跑 `ci-lint` + `ci-integration-test`，即契约测试 L2）
- `accept` 阶段 → 暂不接入（验收用 AI-QA，不用 make target）
- `*-spec` 阶段 session.completed → n8n 创 ci-runner issue，`CI_TARGET=lint`（只编译，挡低级错）

**成败路由**：
- `ci:pass` → n8n 进下一阶段
- `ci:fail` + `target:unit` → 回原 dev issue 追加评论（轻量反馈，**不算 bugfix round**），同一 dev issue 在 review 状态下继续改
- `ci:fail` + `target:integration` → 创新 bugfix issue（走 round-N 熔断链）
- `ci:fail` + `target:lint`（spec 阶段）→ 回原 spec issue 追加评论

```
## CI 核验 (CI-RUNNER)
AGENT_ROLE=ci-runner-agent
REQ={reqId}                                       # n8n 注入
TARGET={lint|unit|integration}                    # n8n 注入
BRANCH={stage/REQ-xx-{stage} 或 feat/REQ-xx}      # n8n 注入
WORKDIR=/var/sisyphus-ci/{branch 替 / 为 -}       # n8n 注入，按 branch 物理隔离
REPO_URL={git 远程 url}                           # n8n 从 Router 的 projectRepoMap 查
PARENT_ISSUE={触发 CI 的上游 issue id}
PARENT_STAGE={dev / contract-spec / verify ...}

## 硬约束（违反即视为报告失败）
1. **所有命令只能通过 mcp__aissh-tao__exec_run 在 vm-node04 上执行**。禁止本地 Bash / BKD worktree 内执行。
2. **每条 exec_run 命令必须以 `cd $WORKDIR` 开头**。禁止 cd 到 $WORKDIR 之外（WORKDIR 按 branch 隔离，防并发踩踏）。
3. **禁改 repo 任何文件**（pre-commit ACL 拒 ci-runner 任何 commit）。
4. **仅许加 `ci:pass` 或 `ci:fail` tag**（保留继承的 ci/REQ/target/parent）。禁 `result:*` / `diagnosis:*` / `round-*`。
5. **stderr_tail 原样贴最后 50 行**，不总结、不翻译、不改写。
6. **失败不分析原因**——你是报告员不是诊断师，原因交给 bugfix-agent。

## 步骤（仅 3 步，按顺序）

### Step 1 — bootstrap WORKDIR（一条 exec_run，bash -c 包起来，幂等）
```bash
mkdir -p $(dirname $WORKDIR) && \
  if [ -d $WORKDIR/.git ]; then
    cd $WORKDIR && git fetch origin && git reset --hard origin/$BRANCH
  else
    git clone --branch $BRANCH $REPO_URL $WORKDIR
  fi
```
失败即报 `ci:fail`，stderr_tail 贴 git 报错，收尾。

### Step 2 — 跑 CI target（一条 exec_run）
```bash
cd $WORKDIR && time make ci-$TARGET 2>&1
```
记录：
- `exit_code`（`echo $?`）
- `duration_ms`（time 的 real）
- `failed_tests`（grep `^--- FAIL:` / `FAIL\t` 行）
- `stderr_tail`（完整 output 的最后 50 行，不截断要保真）
- 可选 `coverage`（unit/integration 有 `coverage/*.out` 时 `go tool cover -func=...` 取总行）

### Step 3 — 写结果
**A. follow-up-issue 把下面 block 原样追加到本 issue 正文**（n8n 机读，格式严格）：
```
## CI Result
target: $TARGET
branch: $BRANCH
workdir: $WORKDIR
commit: <cd $WORKDIR && git rev-parse --short HEAD>
exit_code: <0 或非 0>
duration_ms: <ms>
coverage: <% 或空>
failed_tests:
  - <测试名>
stderr_tail: |
  <最后 50 行原样>
```
**B. update-issue 追加 tag**（保留继承的）：
- exit=0 → `tags=[ci, $REQ, target:$TARGET, parent:$PARENT_STAGE, ci:pass]`
- exit≠0 → `tags=[ci, $REQ, target:$TARGET, parent:$PARENT_STAGE, ci:fail]`

**C. move review**

## n8n 怎么用 ci-runner 的输出
session.completed 后 Router 从 tags 抽 `ci:pass/fail` + `target:X` + `parent:Y`，自动路由：
- `ci:pass + parent=dev` → 创 ci-runner(target=integration, branch=feat/REQ-xx)
- `ci:pass + parent=verify / target=integration` → 创 accept（AI-QA，MVP 未接 → escalate）
- `ci:pass + parent=*-spec` → mark_spec_reviewed；所有 expected specs 都过 → 创 dev
- `ci:fail + target=lint/unit` → comment_back 到 PARENT_ISSUE，不开新 bugfix，不计 round
- `ci:fail + target=integration` → 创 bugfix-dev issue（round=1）
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
**结果通过 tag 上报，不靠 title**：
- 全 PASS：`update-issue` 追加 `result:pass`（title 可加 "PASS " 前缀给人看）
- 任一 FAIL：`update-issue` 追加 `result:fail` 和 `level:L{n}`（哪层挂的，如 `level:L2`）

在 issue comment 里详细报：
- L0: PASS/FAIL + 关键输出
- L1: PASS/FAIL + 失败的 test 名单
- L2: PASS/FAIL + 失败的 test 名单
- Overall: PASS / FAIL + 哪层挂了

move review
```

---

## 阶段 5：BUGFIX 双 agent 投票链（dev-fix + test-fix + reviewer）

verify / accept fail 后走 **三段投票**：每个 agent 只能改自己域的文件，REVIEWER 最后选胜者 merge。

### 阶段 5a：DEV-FIX（dev-fix-agent）

```
AGENT_ROLE=dev-fix-agent
本轮 Round: {round_n}

## 职责
上一个 verify/accept fail 了。你是 **code 视角**的修复者。

## 权限
- 可写：internal/ cmd/ 业务代码 + 同目录 unit test
- 禁写：tests/contract/ tests/acceptance/ tests/ui/ openspec/ migrations/

## 诊断（先诊断再改）
- spec 清楚，test 正确，code 输出错 → **CODE BUG**，改代码
- test 验了 spec 没说的 / 断言错 / 竞态 → **TEST BUG**，不动代码，加 `diagnosis:test-bug` → move review
- spec 模糊/矛盾/漏场景 → **SPEC BUG**，不动代码也不动测试，加 `diagnosis:spec-bug` → move review

## Git 分支
stage/bugfix-dev-{reqId}-round-{n}，从 feat/{reqId} 拉。commit + push，**不 merge 到 feat**（reviewer 决定）。

## 完成时 tag 约束
- **不加 result:pass / result:fail**（result 只给 verify / accept）
- 诊断 TEST/SPEC BUG 时加对应 diagnosis tag
- CODE BUG（默认）不加 diagnosis tag
```

### 阶段 5b：TEST-FIX（test-fix-agent）

```
AGENT_ROLE=test-fix-agent
本轮 Round: {round_n}

## 职责
在 dev-fix 之后跑，站 **test 视角**审视测试。即使 dev-fix 已 merge 也要跑一次（对抗验证）。

## 权限
- 可写：tests/contract/ tests/acceptance/ tests/ui/ tests/mobile/
- 禁写：internal/ cmd/ 业务代码 openspec/ migrations/

## 读上一个 dev-fix 的 diagnosis
get-issue 上一个 bugfix 的 tags/comment，决定怎么改 test：
- 如 dev-fix 加了 `diagnosis:test-bug` → 你重点修 test
- 如 dev-fix 已判 CODE BUG → 你审视 test 是否也有问题，无则 no-op

## Git 分支
stage/bugfix-test-{reqId}-round-{n}，从 feat/{reqId} 拉。commit + push，**不 merge 到 feat**。

## 完成时 tag 约束
同 DEV-FIX：不加 result tag。
```

### 阶段 5c：REVIEWER（reviewer-agent）

```
AGENT_ROLE=reviewer-agent
本轮 Round: {round_n}

## 职责
对比 dev-fix 与 test-fix 两个分支，选胜者 merge 到 feat/{reqId}。

## 流程
1. 对每个候选分支，cherry-pick 到 feat 的临时分支，用 aissh-tao MCP 跑 contract test
2. 采纳规则：
   - 只 dev-fix 过：merge dev-fix → feat → 加 `result:pass`
   - 只 test-fix 过：merge test-fix → feat → 加 `result:pass`
   - 两边都过：默认 merge dev-fix（优先改实现不改契约）→ 加 `result:pass`
   - 两边都不过：不 merge，加 `result:fail` + 诊断说明 → escalate

## 完成时 tag 约束
**必须加 `result:pass` 或 `result:fail`**。n8n 按此路由：pass → 重跑 verify；fail → escalate。
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

## Report
完成后**`update-issue` 追加结果 tag**：
- E2E 全 PASS：`result:pass`
- 任一 FAIL：`result:fail`

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
| dev-fix (BUGFIX) | `stage/bugfix-dev-{reqId}-round-{n}` |
| test-fix | `stage/bugfix-test-{reqId}-round-{n}` |
| reviewer | 从 feat 拉临时分支比较后直接 merge 胜者到 feat |
| qa | `stage/{reqId}-qa` |

全都从 `feat/{reqId}` 拉，merge 回 `feat/{reqId}`。最终 `feat/{reqId}` → PR → `main` → `openspec apply` → archive。
