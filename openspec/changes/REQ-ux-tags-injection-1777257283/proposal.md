# REQ-ux-tags-injection-1777257283: feat(orch): forward user-hint BKD intent tags into sub-issues

## 问题

人在 BKD intent issue 上挂的 hint tag —— 比如 `repo:phona/foo`、`spec_home_repo:phona/x`、`ux:fast-track`、`priority:high`、`team:platform` —— 是给整条 REQ 流水线看的"用户上下文"。当前 sisyphus 把这些 tag **完整丢失**：

1. `start_intake` PATCH intent issue 时硬编码 `tags=["sisyphus", "intake", req_id]` —— 用户 hint 全被覆盖
2. `start_analyze` PATCH intent issue 时硬编码 `tags=["analyze", req_id]` —— 同样覆盖
3. `start_analyze_with_finalized_intent` 创建新 analyze issue 用 `tags=["analyze", req_id]`
4. `start_challenger` 创建新 challenger issue 用 `tags=["challenger", req_id, f"parent-id:{src}", *pr_tags]`

**实证后果**：

- `repo:owner/name` tag 是 `_clone.py` 的 multi-layer fallback 第 3 层。第一次 dispatch 时 tag 还在（直接读 webhook body.tags），但**第一次 PATCH 之后就被擦掉了**。后续任何 stage / verifier / fixer / accept 看不到，要么 fall back 到 `settings.default_involved_repos`，要么彻底空手。
- BKD 仪表盘按 hint tag 切片观察"哪类 REQ 卡得多"完全瞎 —— sub-issue 上没那些 tag。
- agent 上下文丢失：challenger / verifier 看不到用户的"ux:fast-track" 之类提示，只能把 intent issue 重读一遍硬猜。

## 根因

每个 stage action 创建 / 更新 sub-issue 时都在写"权威 tag set"，但只关心 sisyphus 自己 flow-control 的子集（role / REQ-id / sisyphus 标识 / pr-link / verifier decision 等）。**没人负责把"非 sisyphus 管的 tag"原样转发**。

`docs/api-tag-management-spec.md §8` 已经说过 "不要在 PATCH tags 时只传新 tag 导致原 REQ-xxx / role tag 被覆盖丢失"，但只针对 sisyphus 自己的 tag。用户 hint tag 没被写进规范。

## 方案

### 新模块：`orchestrator/src/orchestrator/intent_tags.py`

定义"sisyphus 管的 tag"白名单（前缀 + exact 集合），输出小工具：

- `is_sisyphus_managed_tag(tag) -> bool` —— 判断单 tag 是否归 sisyphus 管理（flow-control / pipeline-injected / REQ identity）
- `filter_propagatable_intent_tags(tags) -> list[str]` —— 滤掉 sisyphus 管的，剩下"用户 hint" + 保留顺序 + 去重

**Sisyphus 管的 tag**（不传播）：

- exact：`sisyphus`、`intake`、`analyze`、`challenger`、`verifier`、`fixer`、`accept`、`staging-test`、`pr-ci`、`done-archive`
- 前缀：`intent:`、`result:`、`pr-ci:`、`verify:`、`trigger:`、`decision:`、`fixer:`、`parent:`、`parent-id:`、`parent-stage:`、`target:`、`round-`、`pr:`
- pattern：`^REQ-[\w-]+$`（REQ id —— 各 callsite 自己显式注入）

**用户 hint**（传播）：

- `repo:owner/repo` —— 多仓 hint，clone fallback 也读它
- `spec_home_repo:owner/repo` —— spec home 声明（M17 弱归属）
- `ux:*` / `priority:*` / `team:*` / 任何团队自定义 tag —— sisyphus 不解析，原样传

### Callsite 改动

四处创 / 改 sub-issue 的 stage action 在拼 `tags=` 时合并 `filter_propagatable_intent_tags(body.tags)`：

| 文件 | 改前 | 改后 |
|---|---|---|
| `actions/start_intake.py` | `tags=["sisyphus", "intake", req_id]` | `tags=["sisyphus", "intake", req_id, *forwarded]` |
| `actions/start_analyze.py` | `tags=["analyze", req_id]` | `tags=["analyze", req_id, *forwarded]` |
| `actions/start_analyze_with_finalized_intent.py` | `tags=["analyze", req_id]` | `tags=["analyze", req_id, *forwarded]` |
| `actions/start_challenger.py` | `tags=["challenger", req_id, f"parent-id:{src}", *pr_tags]` | `tags=["challenger", req_id, f"parent-id:{src}", *pr_tags, *forwarded]` |

`forwarded` 取自 webhook body.tags（即触发本次 dispatch 的 issue 当前 tags），过滤后追加。顺序：sisyphus 管的 tag 在前，hint 在后；hint 之间保留出现顺序去重。

### 文档

- `docs/api-tag-management-spec.md` 新加 §10 "Hint tags（用户上下文，sisyphus 转发不解释）"，列举传播规则 + 几条示例。

## 取舍

- **白名单 vs 黑名单**：选黑名单（"sisyphus 管的不传"）。理由：sisyphus 自己管的 tag 集合稳定且文档化在 `api-tag-management-spec.md`；用户 hint tag 是开放集合（团队自定义、未来扩展），列白名单会随时漏。
- **不为 hint tag 引入新 schema 校验**：sisyphus 不解释 `ux:*` / `priority:*` 的语义，纯转发。要解释的 tag（如 `repo:`）有专门 callsite 自己读 —— 但读还是读 `body.tags`，不读 ctx。
- **不写 ctx**：转发是"读 body.tags → 拼新 tag 数组"，无新 ctx 字段。理由：ctx 是 REQ 跨 stage 状态，hint tag 是 issue 维度的传播信号，BKD 自己当持久化层。如果 BKD issue 删了 tag，下游确实丢，但人删 BKD tag 本来就是显式"清空 hint"。
- **前缀 vs 子串**：`parent:` / `parent-id:` / `parent-stage:` 三个独立前缀都标 sisyphus 管，避免 `parent:foo:bar` 类自定义 tag 被误标。所有比较都用 `startswith` —— 简单直观，不引正则。
- **`repo:` 算 sisyphus 管还是 hint？** 算 hint。理由：`repo:` 是用户 / agent 都能挂的"该仓的 hint"，spec §7 写它"可选"。`_clone.py` 当 fallback 第 3 层读它 —— 如果改成 sisyphus-managed 不传，第二层 PATCH 后 `_clone` 读 body.tags 时它就消失了，正好破坏 fallback 设计。保留转发反而让 fallback 跨多 stage 一致工作。
- **`pr:` 算 sisyphus 管**：`pr_links.ensure_pr_links_in_ctx` 在每个 callsite 自己 lazy discover + 自己注入 —— 重复转发是冗余，注入逻辑也已经覆盖 backfill 老 issue 的场景。`pr:` 在白名单里被屏蔽。
- **不动 `bkd_rest.create_issue` 的 `_ensure_sisyphus_tag`**：sisyphus tag 自动注入与 hint 转发是两层正交逻辑。callsite 决定带哪些 hint，create 层决定 sisyphus 标识 —— 不耦合。
- **不做 backfill**：已有 in-flight REQ 不补丁；只对新创建 / 新 PATCH 的 issue 应用。理由：转发只影响 issue 本身的 tag 显示，对状态机推进无影响；老 REQ 跑完即可。

## 兼容性

- `body.tags` 在所有四个 callsite 入参里已有 / 已被传入 ctx 路径上能拿到的等价数据（`tags=` 是 action register 的标准参数）。
- `_clone.py` 既有"读 tags 解 `repo:` slug"逻辑零变化（参数源不变）；本 REQ 只是让"PATCH 之后这些 tag 还在 issue 上"，更稳了不更脆。
- 现有 router.py / state.py 不动 —— router 看 sisyphus 管的 tag 派事件，hint tag 在 router 集合里全 miss，不会误派事件。
- 测试影响：`tests/test_actions_start_analyze.py` 既有断言用 `assert_awaited_once()` 和 cmd 子串匹配，对 tags 数组顺序 / 长度无强约束；新增 `repo:` 用例验证转发即可。
