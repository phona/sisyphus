# Proposal: thanatos MCP `.sisyphus/scenarios/` fallback (R9)

## 背景

`feat-cross-repo-env-orchestration` 顶层 spec（PR #342）已 settle 第 7 决策：
`.thanatos/` 是 sisyphus 内部组件名，不应在业务仓暴露。业务仓的 thanatos
scenario 软迁移到 `.sisyphus/scenarios/`，thanatos MCP skill loader 加 fallback
读取，**不破坏现有 `.thanatos/` 仓**。

本 REQ 实现该 spec 的 **R9** 单条要求 —— skill loader 路径 fallback。

## 目标

让 thanatos skill loader 解析 `skill.yaml` 路径时按 R9 两步顺序：

1. `<repo_root>/.sisyphus/scenarios/`（存在且非空）→ 用之
2. 否则 fallback 到 `<repo_root>/.thanatos/`（保留现有行为）
3. 都无 → fail-loud

对 accept-agent / thanatos runner 透明（caller 传 `repo_root` 调
`resolve_skill_path()` 拿到 `skill.yaml` 的实际路径）。

## 范围

- `thanatos/src/thanatos/skill.py` 新增 `resolve_skill_path(repo_root)` 公共函数
- `thanatos/tests/test_skill_loader.py` 增加 4 条 CREO-S32..S35 单测 + 1 条
  custom filename + 1 条 end-to-end pipeline

## 不做的

- 不改 MCP server `run_scenario` / `run_all` / `recall` 工具的 inputSchema
  （`skill_path` 仍是绝对文件路径 — 由 caller 计算后传入）
- 不动现有 `.thanatos/` 仓的任何文件
- 不改 `accept.md.j2` prompt 中的 `SKILL_PATH` 计算（属相邻关切，留待后续 REQ
  在 `R9` 上线后再切；当前 fallback 是新功能 + backward-compat，不破坏现状）
- 不实现 R1–R8 / R10（属 spec 其余要求，归别的 impl REQ — 见 PR #342 计划）

## 不变量

- `.thanatos/skill.yaml` 单仓行为完全不变（CREO-S33 即此场景）
- `load_skill(file_path)` API 签名 / 错误语义完全不变
