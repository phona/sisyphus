[SISYPHUS ORCHESTRATED REQ — DO NOT ACT]

本 issue 由 sisyphus orchestrator 全权负责。**不要做任何动作**：

❌ 禁止：
- grep / find / cat 任何源码（包括 sisyphus / 业务仓 / arch-lab）
- 写 spec.md / proposal.md / design.md / test 文件
- git commit / push / branch
- gh pr create / gh pr comment / gh issue update
- helm / kubectl / docker / mcp tool 调用
- thinking 长链；不需要"推理任务"

✅ 唯一动作：**什么都不做，立刻 exit**。

⚠️ 不要 PATCH issue 任何字段（tags / statusId / 其他）。
PATCH tags 会**覆盖**当前 tag 列表（不是 merge），把 sisyphus 派单时打的
`REQ-xxx` / `intent:accept` / `lab-profile:*` / `pr:*` 等 tag 全擦掉，
后续 BKD webhook 给 orch 时这条 REQ 就变孤儿，orch 状态机推不动。

BKD 自己会在 agent session 结束时记录 session.completed，sisyphus orch
另有 child accept-agent 路径继续推进，不需要你 signal 任何东西。

---

为什么：

本 issue 的 intent:accept tag 触发 sisyphus orchestrator 接管：

1. orch 起 runner pod、git clone、helm install ephemeral env
2. orch 拿到 endpoint 后另开一个 `[ACCEPT]` 子 issue
3. 那个子 issue 的 agent（跟你不是同一个）跑 spec.md scenario 真打 endpoint
4. 子 issue 完成后回写 result tag，sisyphus 推进状态机

你的本 issue 只是"触发点 + 进度看板"。任何动手都会跟 sisyphus 流水线打架。

不需要确认、不需要提问、不需要解释。看到本 prompt 就 PATCH tag + exit。
