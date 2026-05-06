# Dispatch 契约 —— 派一条 REQ 进 sisyphus 用户必须声明什么

> **目的**：让"派 REQ 进 sisyphus"这件事**只有一个 source of truth = `intent` JSON**。
> 所有环境初始化要素（涉及哪些仓 / base branch / 验收 / ...）由 `intent` 这一份 typed payload 承载，
> 在两条入口产生：
>
> - `intent:intake` 入口 → intake-agent 跟用户聊出来 → 输出 `intent` JSON
> - `intent:analyze` 入口 → 用户**自己提前准备好** `intent` JSON 贴进 issue 描述
>
> 两条入口最后**汇聚到同一形状**，下游 stage 一律读它，不再从 helm settings / ctx / `.sisyphus/env.yaml`
> 兜底猜元数据。
>
> **现阶段定位**：用文档契约把"上下游依赖"显化。还没决定 BKD project 元数据怎么落、需求池怎么连
> —— 先用人写人读的 schema 把 footgun 堵掉，落地优先。
>
> 相邻文档：
> - [api-tag-management-spec.md](api-tag-management-spec.md) —— 所有 tag 命名规范（router 视角）
> - [integration-contracts.md](integration-contracts.md) —— 业务仓 Makefile / token / secret 接入契约
> - 本文档 —— **dispatch 那一刻** + **intake/analyze 衔接面**用户/agent 必须填什么

---

## 0. 心智模型

```
   ┌─ intent:intake ─→ intake-agent (BKD chat, 无 runner) ─┐
   │                                                        │
   │                                                        ▼
BKD issue                                          finalized intent JSON
   │                                                        ▲
   │                                                        │
   └─ intent:analyze ─→ 用户在 issue 描述里贴 ```intent JSON``` ─┘
                                                            │
                                                            ▼
                              analyze stage 起 runner，按 intent.repos clone
                                          │
                                          ▼
                              spec-lint / challenger / dev-cross / staging-test / pr-ci / accept
                                          (沿途 ctx 只读 intent，不再做兜底猜测)
```

**核心原则**：
- 元数据**显化在 intent JSON 里**，不藏在 ctx 裸 dict / helm 全局 default / 猜测链里
- 两条入口对称，schema 共用
- sisyphus 不"管"元数据 —— 只**读** intent JSON，**校验**必填字段在不在
- 缺字段 / 解析失败 → fail-fast 转 ESCALATED，reason 引用本文档对应小节

---

## 1. dispatch 必填 tag

派一条 REQ 进 sisyphus，BKD issue 上必带：

| tag | 例 | 必填 | 语义 |
|---|---|---|---|
| `intent:intake` 或 `intent:analyze` | `intent:analyze` | ✅ | 入口选择（详见 §2） |
| `base:<branch>` 或 `base:<repo>=<branch>` | `base:feat/develop-hwt` | 选填 | 单 REQ 基线分支 override（覆盖 intent 里的 `base_branches`） |

> `REQ-<slug>` tag 不在此列 —— router 找不到时自动用 `REQ-<issueNumber>` 兜，不是用户契约的一部分。

**没了**。`source-repo:` / `involved-repos:` 这两个 tag 从契约**删除** —— 它们的语义全部移进
`intent` JSON（见 §3 schema），不再让用户/agent 在 tag 层重复写。

---

## 2. 两条入口的差异

### 2.1 `intent:intake` 入口（推荐：不熟悉的仓 / 需求模糊）

```
BKD issue（intent:intake tag + 标题 + 一段需求描述）
       │
       ▼
intake-agent 多轮 BKD chat 跟用户澄清
       │
       ▼
chat 最后一条 message 贴 ```json``` block = finalized intent JSON
       │
       ▼
orch 解析 → 创建 analyze issue（继承 intent JSON）→ 进 ANALYZING
```

intake 阶段**不起 runner / 不 clone 任何仓** —— 它的产出（intent JSON）才决定后续 runner 要 clone 什么。

### 2.2 `intent:analyze` 入口（适合 trivial REQ / 用户已知元素）

```
BKD issue
  ├─ intent:analyze tag
  ├─ 标题
  └─ 描述里夹一段 fenced ```json ... ``` block（schema 同 intake 输出，见 §3）
       │
       ▼
orch start_analyze 提取 intent JSON → 直接进 ANALYZING
```

**强制**：analyze 直入的 issue 描述里**必须**含一段合法 intent JSON block。
缺失 / 解析失败 → escalate，reason：

```
analyze direct entry requires `intent` JSON block in issue body.
see docs/dispatch-contract.md §2.2 / §3
```

> 不动现有 `intent:intake` / `intent:analyze` tag 设计，只把"analyze 直入要带 intent JSON"这条
> 契约固化。两条入口最终汇聚到 §3 同款 schema。

---

## 3. `intent` JSON schema（intake 输出 / analyze 直入贴）

> Schema 实现：[orchestrator/src/orchestrator/schemas/intent.py](../orchestrator/src/orchestrator/schemas/intent.py)
> （pydantic FinalizedIntent，见本仓后续 PR）。本文档 = 形状约定，pydantic = 运行时校验。

```jsonc
{
  // ─── 环境初始化要素（runner / stage 直接消费）────────────────
  "repos": [                           // 必填，非空。runner pod 启动按这个 list git clone
    "ZonEaseTech/ttpos-flutter",
    "ZonEaseTech/ttpos-server-go"
  ],
  "base_branches": {                   // 选填。per-repo 基线 branch；缺 = origin/HEAD
    "ttpos-flutter": "feat/develop-hwt",
    "ttpos-server-go": "feat/develop-hwt"
  },

  // ─── 业务理解 / 验收（agent 消费）────────────────────────
  "business_behavior": "...",          // 必填。用户视角的行为描述，一两句话
  "data_constraints": "...",           // 必填。字段 / endpoint / 错误格式 / 命名约定
  "edge_cases": "...",                 // 必填。边界 / 错误 / 不能
  "do_not_touch": "...",               // 必填。防止 agent 顺手重构撞坏的范围
  "acceptance": "..."                  // 必填。怎么算实现完，验收命令
}
```

### 3.1 字段消费方

| 字段 | 谁读 | 用来做啥 |
|---|---|---|
| `repos` | runner pod entrypoint / `_clone.py` | clone 这些仓 |
| `base_branches` | runner pod entrypoint / `_clone.py` | per-repo `git checkout` |
| `business_behavior` / `data_constraints` / `edge_cases` / `do_not_touch` | analyze / challenger / verifier prompts | 业务理解 + 写 spec + 写 test |
| `acceptance` | accept stage agent / verifier | 验收命令 + pass 判定 |

### 3.2 哪些事**不**进 intent JSON

明确不进 schema 的字段（防止 schema 膨胀）：

- "哪个是 source repo" / "主仓是谁" —— **agent 自判**（看业务码 diff / `.sisyphus/env.yaml` 在哪个仓）
- accept-env-up 怎么起 —— `.sisyphus/env.yaml` 自带（业务仓自描述）
- runner image / k8s namespace / token —— sisyphus 部署级（helm values）
- `verifier_issue_id` / `cloned_repos` / `source_sha` 等 stage 跑出来的运行时状态 —— ctx 内部状态，不是契约

### 3.3 现状 schema 跟未来 schema 的差异

现状 intake-agent 输出 6 字段：`involved_repos` / `business_behavior` / `data_constraints` / `edge_cases` / `do_not_touch` / `acceptance`（见 [orchestrator/src/orchestrator/prompts/intake.md.j2](../orchestrator/src/orchestrator/prompts/intake.md.j2)）。

按本契约：
- `involved_repos` → rename `repos`（同义；旧名兼容期保留）
- 新增 `base_branches`（选填）

迁移策略：pydantic schema 同时接受 `repos` / `involved_repos`（alias），warn log 旧字段名 6 个月后删除。

---

## 4. 跟相邻系统的边界

### 4.1 `.sisyphus/env.yaml`（业务仓自带）

**只管** accept stage 起 lab 环境（lab chart / `needs` / `inputs` / `emits`）。
**不**声明：
- 哪个仓是 source（→ agent 自判）
- 这条 REQ 涉及哪些仓（→ `intent.repos`）
- base branch 是什么（→ `intent.base_branches` 或 `base:*` tag）

### 4.2 helm values（部署级）

**只放 sisyphus 自身基础设施**配置：DB / BKD endpoint / runner image / token / agent_model / mcp_capability_* 等。

**业务/项目元数据全部撤出**。以下字段标记 deprecated，留兼容期不再被读：

| Deprecated 字段 | 原意 | 替代 |
|---|---|---|
| `default_involved_repos` | 全局 fallback 涉及哪些仓 | `intent.repos`（消息层） |
| `default_base_branch` | 全局 fallback base | `intent.base_branches` 或 `base:*` tag |
| `default_base_branches` | per-repo fallback base | 同上 |

启动时若发现这些字段非空 → orch warn log；读取它们的代码路径全部走 fail-fast（缺 `intent` 字段直接 escalate，不再 fallback）。

### 4.3 BKD project 元数据

现阶段 sisyphus **不依赖** BKD project 自带元数据 —— 所有"涉及哪些仓"信息只走 intent JSON。

未来如果 BKD project 加"涉及哪些仓"字段，可以演进为：
- intake-agent 从 BKD project 元数据预填 `intent.repos` 草案
- analyze 直入从 BKD project 元数据兜底（仍 fail-fast 时报缺什么）

但这是**未来**，本契约不依赖。

---

## 5. sisyphus 自动加的 tag（用户不要碰）

下面这些是 sisyphus / stage agent 自己写的状态 tag，用户 dispatch 时**不应**带：

- `verify:<stage>` / `trigger:<...>` / `decision:<...>` / `result:<...>`
- `pr:<owner>/<repo>#<n>`（analyze stage agent 推 PR 后自己挂）
- `parent-id:<...>`（sub-issue 关联）
- `escalated` / `reason:<...>`（escalate 时 orch 写）

详见 [api-tag-management-spec.md](api-tag-management-spec.md)。

---

## 6. 派单示例

### 6.1 intake 入口（推荐）

```bash
python3 scripts/bkd-cli.py inline \
  --slug feat-member-points-redeem \
  --title "会员积分兑换" \
  --prompt-file prompt.md \
  --intent intake
```

intake-agent 跟用户聊 → 输出 finalized intent JSON（含 `repos` / `base_branches` / 业务字段）。

### 6.2 analyze 直入（要素已就位）

`prompt.md` 里描述需求 + **末尾贴 intent JSON block**：

````markdown
# 需求描述
... (人能读的描述)

```intent
{
  "repos": ["ZonEaseTech/ttpos-flutter"],
  "base_branches": {"ttpos-flutter": "feat/develop-hwt"},
  "business_behavior": "...",
  "data_constraints": "...",
  "edge_cases": "...",
  "do_not_touch": "...",
  "acceptance": "..."
}
```
````

```bash
python3 scripts/bkd-cli.py inline \
  --slug fix-cart-checkout-stuck \
  --title "购物车结算卡住" \
  --prompt-file prompt.md \
  --intent analyze
```

orch 解析 issue 描述里的 ` ```intent ``` ` block → 进 ANALYZING。

### 6.3 单 REQ 基线 override（hotfix 场景）

intent JSON 里 base_branches 写 `feat/develop-hwt`，但本条 hotfix 要从 `release` 切：

```bash
... --tag base:release
# 或 per-repo
... --tag base:ttpos-flutter=release
```

`base:*` tag 优先级 > `intent.base_branches`。

---

## 7. 现阶段不做 / 后面再说

- BKD project 元数据接入（"这 project 涉及哪些仓"自动来源）—— 等需求池 ↔ BKD 方案定了再设计
- 多 BKD project 监听 —— orch 仍单 project，跨 project 不在本契约范围
- intent JSON schema 引入 langgraph / 其它 workflow 框架 —— 自家 pydantic + state.py 够用，详见与用户讨论

**目标是先把 sisyphus pipeline 落地，不是把它设计完美**。
撞到的痛点先在本文档加一行说明 + 让 orch fail-fast，**不要静默兜底**。

---

## 8. 跟现有 footgun issue 的关系

| issue | 根因 | 本契约怎么堵 |
|---|---|---|
| #441 | `source-repo` tag 没机制性入口 | §1 删除该 tag；source 由 agent 自判（§3.2） |
| #462 | `_resolve_source_repo` 4 层猜，cloned_repos[0] 选错 | 同上：source 不进契约，agent 自判 |
| #464 / #466 | `default_involved_repos: [phona/sisyphus]` 全局 default 跨场景污染 | §4.2 helm 业务元数据撤出；改读 `intent.repos`，fail-fast |
| #467 | 元数据散 4 层无 source of truth | 本文档 = 唯一 source of truth；helm / ctx / env.yaml 各归各位（§4） |

---

## 9. 修订纪律

- 加新 intent JSON 字段前 → 先在本文档 §3 加一行说明（schema doc-first），再改 pydantic + intake prompt
- 加新 fallback 默认值前 → 先想清楚是不是又在偷塞"项目元数据" —— 99% 答案是 fail-fast 转 escalate
- 不在本文档列出的 tag → orch 不识别，用户也别用
- helm values 加新业务/项目相关字段 → 拒绝。helm 只放基础设施配置
