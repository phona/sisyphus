# Proposal: stage-agent step-0 unified precheck phase

Closes #373.

## 背景

`联调加速 #4` (#373) 复盘了 5/4 dogfood 现场：v3/v4/v5 REQ 派出去后，runner pod 一路跑到
`dev_cross_check` 才发现 `fvm/flutter_sdk` symlink 缺，watchdog 7 min kill，整 stage 重派 —
ANALYZE + CHALLENGER 几千 token 全白烧。同 pattern 之前出过：MCP 缺 (#270)、
runner GH_TOKEN 缺 (#365)、KUBECONFIG 错 (#292)。

每次都是「跑到深处才 fail」的等待浪费。MCP 缺已经被 #270 的 `mcp_preflight` hook 封死
(`enabled_prompt_hooks` pluggable hook 框架)，但其它 env / 工具 / 业务仓自检还没统一入口。

## 范围

只动 `phona/sisyphus`：

1. **新增 hook `_shared/hooks/precheck.md.j2`** —— stage agent 第 0 步统一 precheck phase。
   按 #270 同款 pluggable hook 模式（`enabled_prompt_hooks` filename + config-list，
   见 memory `feedback_prompt_pluggable_via_filename_convention`）。
   当 `stage_precheck_enabled[stage]` 为 True 时渲染下列 fail-fast 段落：
   - **Pod env 必填**：`SISYPHUS_REQ_ID` / `GH_TOKEN` / `KUBECONFIG`
   - **工具必装**：`gh auth status` / `kubectl version --client` / `make --version`
   - **业务仓自报**：`/workspace/source/<repo>/` 跑 `make ci-precheck`（业务仓**可选**契约
     target，echo OK 即可；缺 target = soft pass，不阻塞）
   任一硬性 check fail → update-issue tags 加 `result:fail` + `fail-reason:precheck:<item>`，
   立即结束 session（verifier 直接 escalate，**不重试**——precheck 失败基本是
   ops/部署 bug，重跑无意义）。

2. **config 加 `stage_precheck_enabled: dict[str, bool]`**（pydantic-settings）：
   默认 `{intake: False, analyze: True, challenger: True, accept: True, staging_test: True,
   pr_ci_watch: True, done_archive: False, bugfix: True}`。
   与 `stage_mcp_requirements` 平行，operator 可 helm values 覆盖。

3. **enabled_prompt_hooks 默认值改为 `["mcp_preflight", "precheck", "self_issue_constraint"]`**：
   precheck 排在 mcp_preflight 之后（precheck 需要 MCP exec_run 可用），
   排在 self_issue_constraint 之前（fail-fast 段必须先于业务约束段）。

4. **docs/integration-contracts.md 加 §2.6 ci-precheck 契约**：
   ttpos-ci 标准外加一条**可选** target `make ci-precheck`：
   - 业务仓可选实现，缺 target = `precheck` hook 跳过该仓
   - 实现示例：`echo OK` / `flutter doctor --machine` / `which fvm` 等
   - 退码 0 = pass，非 0 = fail（被 hook 转成 `fail-reason:precheck:ci-precheck:<repo>`）

5. **测试**：`tests/test_prompts_precheck.py` 覆盖：
   - hook 启用时 stage prompt 渲出 precheck 段（含三类 check）
   - hook 关闭时段落消失（pluggable invariant）
   - intake 默认 False → 渲不出（避开 chat brainstorm 误打）
   - 默认 hook 顺序 mcp_preflight → precheck → self_issue_constraint
   - precheck 段引用 `mcp__{{ provider }}__exec_run`，不硬编 `aissh-tao` 字面量

## 不在范围内

- 业务仓侧 `ci-precheck` 实现（fixture repo / ttpos-flutter / ttpos-server-go） —— 各仓
  独立 PR 跟进，本 REQ 只立契约 + sisyphus 侧执行框架
- runner pod entrypoint 内置 precheck —— 走 prompt hook 而非 entrypoint，能 helm 覆盖
  + 跟既有 mcp_preflight 同模式，单一来源，避免 entrypoint / prompt 双 fail-fast 路径
- pre-stage independent precheck stage —— 沿用 hook 模式（agent 第 0 步），不引入
  状态机新 state；新 stage 状态机 coverage / verifier 路径成本远高于一段 prompt

## Roll-out

1. 合本 PR
2. 默认 hook 列表升级，所有现存 REQ 下个 stage dispatch 自动带新 precheck 段
3. 各业务仓 (#373 关联 follow-up) 按 docs §2.6 加 `ci-precheck` target，echo OK 起步
4. 收集 30 天 `result:fail` + `fail-reason:precheck:*` 命中数据，验证 7 min watchdog 等待
   是否被 precheck 截断（Metabase Q-precheck-fail-rate 后续 REQ）

## 影响

| 依赖 | 验证方式 |
|---|---|
| 既有 mcp_preflight hook | 顺序锁在第一位，precheck 在第二位（fail-fast 链） |
| `stage_mcp_requirements` | 不动，precheck 用独立 `stage_precheck_enabled`（关注点分离） |
| 现有业务仓无 `ci-precheck` target | hook 内 `make -n ci-precheck` 探测，缺则 soft pass |
| operator helm 覆盖 | `SISYPHUS_ENABLED_PROMPT_HOOKS` / `SISYPHUS_STAGE_PRECHECK_ENABLED` 走原通道 |
