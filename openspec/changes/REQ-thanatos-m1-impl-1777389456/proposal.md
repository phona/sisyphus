# Proposal: thanatos M1 — 统一 AI 验收收口落地

## 背景

thanatos 是 sisyphus 的统一 AI 验收收口层。M0 阶段仅冻结了 driver Protocol、scenario parser 和 MCP server 的接口形状，所有 driver 方法抛 `NotImplementedError`。create_accept.py 在 REQ-accept-m1-lite 中被改成了 v0.3-lite（纯 make target 方案），偏离了 thanatos MCP 的设计方向。

## 目标

让 thanatos 从 scaffold 变成可执行的统一验收层，恢复 thanatos MCP 在 sisyphus 流水线中的位置。

## 范围

- HTTP driver 实现（优先）
- Playwright driver 实现（次之）
- runner execute flow 补全
- create_accept.py 恢复 thanatos MCP 路径
- accept.md.j2 恢复 thanatos MCP 调用指引
- CI 和测试恢复

## 不做的

- ADB driver 保持 M0 stub（M2/M3 范围）
- thanatos helm chart 升级（PVC 挂载等 M2 范围）
