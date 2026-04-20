# 当前工作流状态

## 系统组件

```
n8n (vm-node04 K3s)
  ├── /v2           — 入口 webhook (7 节点)
  └── /bkd-events   — BKD 事件路由 (55 节点)

BKD (Coder Workspace)
  ├── webhook → n8n /bkd-events (session.completed/failed)
  └── projects/workflowtest (ubox-crosser 仓库)

调试环境 (vm-node04)
  └── aissh MCP 远程控制
```

## 完整流程

```
用户创建需求 → /v2 触发

阶段一：需求分析（串行）
  n8n 创建 [REQ-xx] 需求分析 issue
  Agent: /opsx:propose → openspec artifacts + contract.spec.yaml
  完成 → BKD webhook → n8n

阶段二：N 路并行 Spec（按 analyze 输出的 layers 动态展开）
  当前实现：固定 3 路（开发Spec / 契约测试Spec / 验收测试Spec）
  目标：按 layers fan-out
    layers 含 backend  → +契约测试Spec
    layers 含 frontend → +UI测试Spec（节点未实现）
    layers 含 data     → +Migration Spec（节点未实现）
    全 REQ 必出       → 开发Spec + 验收测试Spec
  每个完成 → BKD webhook → All3? gate（按 expected 全 review 才放行）
    是 → 创建 开发 issue
    否 → 等待其他 spec 完成

阶段三：开发
  n8n 创建 [REQ-xx] 开发 issue
  Agent: TDD 小步快跑，用 aissh 在调试环境验证
  完成 → BKD webhook → n8n

阶段四：测试验证
  n8n 创建 [REQ-xx] 测试验证 issue
  独立 Agent: 用 aissh 在调试环境跑 L0-L3
  结果:
    PASS → 创建验收 issue
    FAIL → 创建 Bug Fix issue → 修完 → 再创建测试验证 → battle 循环

阶段五：验收
  n8n 创建 [REQ-xx] 验收 issue
  独立 Agent: 部署 + 跑 acceptance_test
  结果:
    PASS → Done
    FAIL → 创建 Bug Fix → battle
```

## BUGFIX 双 agent 投票链（新）

verify/accept fail 后**不再由单个 bugfix-agent 决定改 code 还是 test**，改成对抗投票：

```
verify fail (result:fail) 或 accept fail
    ↓ CB Tripped? false
    ↓
[BUGFIX] (DEV-FIX role)  ← 只能改 code，禁改 test；诊断 SPEC/TEST BUG 加对应 tag
    ↓ 完成 (routeKey=bugfix)
    ↓
Is SPEC-BUG? → true: Escalate（spec 模糊）
             → false ↓
    ↓
[TFIX] (TEST-FIX role)   ← 只能改 test，禁改 code
    ↓ 完成 (routeKey=test-fix)
    ↓
[RVW] (REVIEWER role)    ← 对比 dev-fix / test-fix 两个分支的 diff
    ↓ 完成 (routeKey=reviewer) → resultKey=?
      ├── pass (reviewer 选了一边 merge 到 feat): 重跑 [VERIFY]
      └── 其它 (两边都不过 / abstain): Escalate
```

各角色 git 分支：
- DEV-FIX → `stage/bugfix-dev-{reqId}-round-N`
- TEST-FIX → `stage/bugfix-test-{reqId}-round-N`
- REVIEWER 最终决定 merge 哪个分支到 `feat/{reqId}`

## n8n /bkd-events 路由逻辑

**title 完全不参与调度**（仅供人类阅读）。**所有判定走 BKD issue 的 tags 字段**：阶段 tag → routeKey；结果 tag → resultKey。

```
收到 BKD webhook (session.completed)
  → Ctx 从 webhook.body.tags 计算两个 key：

  routeKey =
    test-bugfix > bugfix > verify > accept > dev > spec(*-spec) > analyze > unknown

  resultKey =
    decision:unsupported   → unsupported
    decision:needs-clarify → needs-clarify
    diagnosis:test-bug     → test-bug
    diagnosis:spec-bug     → spec-bug
    result:pass            → pass
    result:fail            → fail
    （无）                 → ""

  → IF 链（全部基于 routeKey / resultKey 精确比较，**无任何 title 引用**）:

  routeKey == 'accept'         → AcceptPass?(resultKey=='pass') → Done / 创 Bug Fix
  routeKey == 'bugfix' (DEV-FIX 完成) → Is SPEC-BUG?
                                          → true: Escalate
                                          → false: 创 [TFIX]
  routeKey == 'test-fix' (TEST-FIX 完成) → 创 [RVW]
  routeKey == 'reviewer' (REVIEWER 完成) → [RVW] Pass?
                                          → true (result:pass): 创 新一轮 [VERIFY]
                                          → false: Escalate
  routeKey == 'verify'         → Pass?(resultKey=='pass') → 创 验收 / 熔断检查 → 创 Bug Fix
  routeKey == 'dev'            → 创 测试验证
  routeKey == 'spec'           → 查 BKD 检查 layers expected specs 都 review？
                                    是 → 创 开发
                                    否 → 等其他 spec 完成
  routeKey == 'analyze'        → Is UNSUPPORTED?(resultKey ∈ {unsupported, needs-clarify})
                                    是 → escalate
                                    否 → 按 layers fan-out N 路 Spec
```

**title 撒谎也不会路由错**：harness `case_title_lies` 验证了 title="PASS [REQ] 验收" 但 tags=[accept, result:fail] 时仍按 tags 路由（创 Bug Fix）。

## Issue 命名和 Tag 规范

**title 是描述，纯展示**。可以加 `PASS ` / `FAIL ` 前缀方便人看，但 n8n 一概不读。
**tags 是调度的唯一真相**：阶段 tag（`analyze` / `verify` 等）+ REQ tag + 结果 tag（`result:pass` / `diagnosis:test-bug` 等）。

| 阶段 | 必含 tag | 完成时追加结果 tag |
|---|---|---|
| 需求分析 | `analyze`, `REQ-xx` | 正常完成：`layer:backend` / `layer:frontend` / `layer:data`（按 proposal.md 实际 layers）<br>不支持：`decision:unsupported`<br>需澄清：`decision:needs-clarify` |
| 开发 Spec | `dev-spec`, `REQ-xx` | （无，路由只看阶段 tag）|
| 契约测试 Spec | `contract-spec`, `REQ-xx` | （无）|
| 验收测试 Spec | `accept-spec`, `REQ-xx` | （无）|
| UI 测试 Spec | `ui-spec`, `REQ-xx` | （无）|
| Migration Spec | `migration-spec`, `REQ-xx` | （无）|
| 开发 | `dev`, `REQ-xx` | （无）|
| 测试验证 | `verify`, `REQ-xx` | 全 PASS：`result:pass`<br>任一 FAIL：`result:fail`, `level:L0/L1/L2` |
| Bug Fix (DEV-FIX) | `bugfix`, `REQ-xx`, `round-N` | CODE BUG（默认）：（无 diagnosis tag）<br>TEST BUG：`diagnosis:test-bug`<br>SPEC BUG：`diagnosis:spec-bug`<br>**不加 result:* tag** |
| Test Fix (TEST-FIX) | `test-fix`, `REQ-xx`, `round-N` | 同上诊断 tag（可选）<br>**不加 result:* tag** |
| Reviewer | `reviewer`, `REQ-xx`, `round-N` | 采纳了：`result:pass`<br>两边都不过：`result:fail` |
| 验收 | `accept`, `REQ-xx` | PASS：`result:pass`<br>FAIL：`result:fail` |

**追加 tag 怎么做**：用 BKD MCP `update-issue` —— get-issue 拿当前 tags → 拼接新 tag → update-issue(tags=[...all]) → move review。

**注意**：title 前缀（`PASS `/`FAIL `/`TEST-BUG `）只是给 BKD UI 一眼辨识用，**不是路由依据**。即使忘了加 title 前缀，只要 tag 加了，n8n 路由就正确。

## 已知问题

1. ~~**并行 Spec 可能创建重复 Dev**~~ ✅ All3?+CB Count 用 SSE→JSON 解析 + tags 精确算
2. ~~**测试验证 PASS/FAIL 判断依赖 title**~~ ✅ 切到 `result:pass`/`result:fail` tag，title 退出调度
3. ~~**完成阶段 issue 卡 review**~~ ✅ Mark Prev Done 节点自动转 done
4. ~~**ACCEPT/VERIFY 漏 result tag 误创 BUGFIX**~~ ✅ HasFail? 节点：resultKey 空 → escalate
5. ~~**ANALYZE 重发触发重复 fan-out**~~ ✅ SpecsExist gate 防重
6. ~~**BKD session.failed 没处理**~~ ✅ Is Failed Session? 节点直接 escalate
7. ~~**ACCEPT pass 后没 archive/PR**~~ ✅ DONE 阶段 (Cr/Id/Fu/St [DONE]) 跑 `openspec apply` + `gh pr create`
8. **父 issue 状态未自动更新** — BKD webhook payload 不含 parentId。Done 节点目前只 follow-up 当前验收 issue。
9. **aissh-tao MCP 未在 BKD agent 加载** — agent log 自报 "I don't see aissh-tao MCP tool"。已写 .mcp.json 在 ubox-crosser 项目根，但需 user commit 到 master 才能跨 worktree 生效（sandbox 拦了 token push）
10. ~~**BUGFIX 当前单 agent**~~ ✅ 已实现双 agent 投票（DFIX + TFIX + RVW 三链）
11. **凭证硬编码** — Coder-Session-Token 在 workflow JSON / harness 里裸串
12. **layer/契约形态自由** — analyze prompt 已改成 "形态自由（HTTP=OpenAPI / DB=SQL / Flutter=md / cron=schedule）"，但 layer tag 命名 agent 还是不规范（cosmetic，不影响调度）
13. **跨 repo 一 BKD project N 仓库** — 设计就绪，prompt 已加多 repo 意识，未实测
