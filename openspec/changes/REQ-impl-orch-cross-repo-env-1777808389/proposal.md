# Proposal: sisyphus orchestrator — cross-repo env orchestration impl

Closes #326 #333.

## 背景

`feat-cross-repo-env-orchestration` spec（PR #342, merged）形式化了 sisyphus
↔ 业务仓 ↔ lab 的三方契约：每仓在根目录放 `.sisyphus/env.yaml` 声明
emits / needs / inputs / branches，sisyphus 拉起单 runner pod、按拓扑序
sequential 跑 `make accept-env-up` 并把上游 emit 注入下游 input env var。
该 spec 涵盖 R1–R10 共 10 条 requirement + CREO-S1..S39 39 个 scenario。

实现拆 4 个独立 REQ：
1. **本 REQ — sisyphus orchestrator 实现**：R1, R2, R3, R4, R5, R6, R7, R8, R10
2. sisyphus thanatos MCP fallback：R9（独立 REQ，并行进行）
3. ZonEaseTech/ttpos-server-go：加 `.sisyphus/env.yaml`
4. ZonEaseTech/ttpos-flutter：加 manifest + accept-env-up 改造

## 范围

本 REQ 只动 `phona/sisyphus`（orchestrator + migrations + tests）。

新增 `orchestrator/src/orchestrator/cross_repo_env.py` —— 纯函数模块，承担：
- R1 manifest schema validator（`parse_manifest`）
- R2 dependency topology resolver（`resolve_topology`，拓扑排序 + 环检测）
- R3 multi-clone workspace layout（`workspace_dir_map`，short-name + collision OWNER__REPO 兜底）
- R6 cross-repo branch resolver（`resolve_branch`，same-name → class → fail-loud）

改 `orchestrator/src/orchestrator/actions/create_accept.py`：
- 入口先读 source repo manifest；缺则走 R8 backward-compat 单层路径（不动既有行为）
- 有 manifest 且非 emit-only：runner-side 拉所有 needs 仓，按拓扑序循环 `make accept-env-up`，
  从 stdout 末行 JSON 抽 emit fields（R5 passthrough，零 schema 强制），合并成 endpoint bundle
  注入下游 inputs env var；末层完成把 bundle 喂给 accept-agent
- 任一层 fail / 缺 emit field → R10 把 `failed_layer`/`failed_field`/`layers[]` 写入 `stage_runs.context` 后 emit `ACCEPT_ENV_UP_FAIL`

改 `orchestrator/src/orchestrator/actions/teardown_accept_env.py`：
- 多层场景按 R7 反序 best-effort 跑 `make accept-env-down`，单层 fail 不阻塞剩余层

新增 migration `0016_stage_runs_context.sql`：给 `stage_runs` 加 `context JSONB` 列承载 R10 attribution。

## 不做的

- ❌ R9 thanatos `skill_path` fallback（`.sisyphus/scenarios/` → `.thanatos/`）—— 由 thanatos 仓平行 REQ 实现
- ❌ 业务仓自己的 `.sisyphus/env.yaml` —— ttpos-server-go / ttpos-flutter 平行 REQ
- ❌ 跨仓 PR 合并顺序自动化（spec 第 14 决策点明确推出 MVP）
- ❌ 多 layer 并发（spec 决策：sequential 即可）
- ❌ APK build 推到 mobile-env-up 内部（保留 ttpos-ci 异步路径）
- ❌ accept-env-up.fail 重试机制（既有 escalate 走 verifier 主观判，不变）

## 验证

- 所有新增 / 改动模块的 unit test 覆盖 spec.md Readiness Gate 所列 R1/R2/R3/R4/R6/R8/R10 行（R5 / R7 通过 R4/teardown 测试覆盖）
- `make ci-lint` / `make ci-unit-test` 全绿
- 既有 single-layer accept 行为（无 manifest）pytest 一字不动通过 → R8 backward compat
- end-to-end dogfood 在 ttpos 4 个 impl REQ 全合后由 #248 thanatos M3 覆盖（不属本 REQ 范围）
