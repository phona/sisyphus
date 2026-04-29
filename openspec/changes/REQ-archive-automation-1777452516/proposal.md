# 将 archive 改为后台自动任务

## 问题

当前状态机在 pr-ci pass / accept pass 后进入 ARCHIVING state，起一个 done-archive BKD agent 跑 openspec archive + 写报告。这带来了几个问题：

1. **过度工程**：archive 只是机械整理 openspec 产物（`openspec archive REQ --yes` + `git commit`），不需要 AI agent 决策
2. **状态冗余**：ARCHIVING 作为独立 state 增加了状态机复杂度
3. **流程卡住**：archive agent 可能因 runner/PVC 问题失败，导致 REQ 卡在 ARCHIVING
4. **延迟**：等 BKD spawn + agent 执行 + session.completed，增加几分钟到几十分钟的延迟

## 方案

1. **砍 ARCHIVING state 和 Event.ARCHIVE_DONE**
2. **pr-ci pass / accept pass / teardown pass → 直接 DONE**
3. **archive 作为 transition 到 DONE 的副作用异步执行**（fire-and-forget）
4. **失败不阻塞**：archive 失败只 log warning，REQ 状态仍是 DONE
5. **保留 openspec archive 操作**：在 runner pod 里执行 `openspec archive REQ --yes && git add openspec/ && git commit`
6. **不 push main**：archive commit 留在 feat 分支，跟代码一起随 PR 合入

## 影响

- 状态机从 18 states 减到 17 states
- 减少一个 BKD agent stage（done-archive）
- PR merge 后不需要等 archive agent，直接 DONE
