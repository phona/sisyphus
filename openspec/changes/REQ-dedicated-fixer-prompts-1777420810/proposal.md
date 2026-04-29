# REQ-dedicated-fixer-prompts-1777420810: 专用 fixer prompts 替代过渡 bugfix.md.j2

## 问题

当前 fixer-agent 复用 `bugfix.md.j2` 这一个通用 prompt 过渡，用于两种完全不同的修复场景：

1. **dev fixer**：改业务代码（修复代码逻辑 bug）
2. **spec fixer**：改 spec（修复验收用例、设计文档）

一个通用 prompt 同时覆盖这两种场景，导致：
- 修复建议经常跑偏（dev fixer 改到 spec 文件，spec fixer 想改代码）
- verifier 的 decision JSON 里 `fixer=dev` / `fixer=spec` 的约束在 prompt 里没有真正体现
- 修复质量在 2 轮后显著下降（IMPACT-REPORT 已观察到）

## 根因分析

1. **prompt 职责不聚焦**：`bugfix.md.j2` 的 "诊断 BUG 类型（CODE / SPEC / ENV）" 让 fixer 自己判断该改什么，而不是由 verifier 的 decision 决定
2. **无文件类型锁定**：通用 prompt 没有明确禁止 dev fixer 碰 spec 文件，也没有禁止 spec fixer 碰业务代码
3. **缺少 target_repo 透传**：verifier decision 里的 `target_repo` 字段没有传到 fixer prompt，多仓 REQ 时 fixer 容易盲修

## 方案

### 1. 新建 dev fixer 专用 prompt

文件：`orchestrator/src/orchestrator/prompts/verifier-fix-dev.md.j2`

- **LOCKED：只改业务代码**，不改 spec / openspec / test / Makefile / CI 配置
- **Scope 限定**：verifier decision 里 `target_repo` 指定了哪仓就改哪仓
- 保留 env-bug 短路检测（kubectl exec 401 / git clone 401 / 工具链缺 / 磁盘满 OOM）
- 保留 `make ci-lint` 预 push 自检（Makefile ci 契约）
- 保留 audit 警告（改动会被 verifier diff-audit）

### 2. 新建 spec fixer 专用 prompt

文件：`orchestrator/src/orchestrator/prompts/verifier-fix-spec.md.j2`

- **LOCKED：只改 spec 文件**（openspec/changes/REQ-*/ 下的 spec.yaml / contract.spec.yaml / spec.md / design.md / tasks.md）
- **LOCKED：不改业务代码 / 不改测试**
- 保留 env-bug 短路检测（openspec 工具链缺等）
- push 前跑 `openspec validate` + `check-scenario-refs.sh` 自检
- 禁止 spec-drift（不要为让测试通过而扭曲 spec）

### 3. 更新 start_fixer 路由

修改 `orchestrator/src/orchestrator/actions/_verifier.py`：

- `fixer=dev` → `verifier-fix-dev.md.j2`
- `fixer=spec` → `verifier-fix-spec.md.j2`
- 无 `fixer` 字段 → fallback 到旧 `bugfix.md.j2`（兼容）

同时把 `target_repo` 从 verifier decision 透传给 prompt 模板。

### 4. webhook 透传 target_repo

修改 `orchestrator/src/orchestrator/webhook.py`：

解析 verifier decision JSON 时，把 `target_repo` 字段写入 ctx（`verifier_target_repo`），供 start_fixer 读取。

## 取舍

- **为什么不直接删 bugfix.md.j2** —— 留作 fallback，等两个专用 prompt 稳定后再删（backward compat）
- **为什么两个 prompt 都保留 runner_container / tools_whitelist / self_issue_constraint** —— 这些共享 partial 是 sisyphus 的通用基础设施约束，所有 agent prompt 都需要
- **为什么 spec fixer 也要 env-bug 检测** —— 虽然 spec 修复依赖的工具链不同（openspec vs go），但 runner pod 环境抖动的检测逻辑同样需要
- **为什么 target_repo 从 webhook 透传而不是让 fixer 自己推断** —— verifier 已经在决策时做了多仓判断，让 fixer 再推断一次是重复劳动且容易错

## 影响面

- 新增 2 个 prompt 模板文件
- 改 `orchestrator/src/orchestrator/actions/_verifier.py`：start_fixer 模板路由 + target_repo 透传
- 改 `orchestrator/src/orchestrator/webhook.py`：verifier decision 解析加 target_repo
- 改 `docs/prompts.md`：索引更新
- 新增 unit test：`test_verifier.py` 验证模板路由和 target_repo 透传
- 不动 state.py / router.py / migrations / BKD 集成层 / 机械 checker
