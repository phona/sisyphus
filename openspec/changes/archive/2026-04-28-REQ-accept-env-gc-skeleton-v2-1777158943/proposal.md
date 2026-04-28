# REQ-accept-env-gc-skeleton-v2-1777158943: feat(orchestrator): accept_env_gc.py file skeleton only (opus retry)

## 问题

`teardown_accept_env` 在 accept 阶段以 best-effort 方式跑 `make accept-env-down`，
失败只 WARNING、不阻塞状态机。后果是 K8s cluster 上常年留着 `accept-req-*` 孤儿
namespace（含 helm release / PVC / pods），靠 ops 手清。

后续会引入一个 background GC 任务（类似 `runner_gc.run_loop`）周期扫
`accept-req-*` namespace 并 cascade 删终态 REQ 的那批。前置 REQ
`REQ-accept-env-gc-minimal-1777138424` 试图一把交付（skeleton + 配置 + main.py
wiring + 单测 + 实际逻辑）但因 sonnet 上下文偏长导致 escalate，并未合入 main。
本 REQ（opus retry）刻意把范围切到**最小可独立交付的一刀**：只落 `accept_env_gc.py`
的文件骨架，公开 API 表面 lock 住，不接任何调度、配置、行为。

## 方案

仅新增一个文件 `orchestrator/src/orchestrator/accept_env_gc.py`，模块内只声明
两个 `async` 函数 stub（`gc_once` / `run_loop`），均直接抛
`NotImplementedError("accept_env_gc skeleton only ...")`。模块顶部用 docstring
说明这是占位骨架、未来 REQ 才会接 K8s API + DB + 调度。

附一个最小单测 `orchestrator/tests/test_accept_env_gc_skeleton.py`：

- 断言 `accept_env_gc.gc_once` / `accept_env_gc.run_loop` 是 `coroutine function`（用
  `asyncio.iscoroutinefunction`）—— lock 住 async API 表面
- 断言 `await accept_env_gc.gc_once()` 抛 `NotImplementedError`
- 断言 `await accept_env_gc.run_loop()` 抛 `NotImplementedError`

测试 lock 的是"骨架契约"，不是行为契约 —— 实现 REQ 把 `NotImplementedError`
抽掉时，本测试用例同步删除（一次性占位测试，不进 archive 后的 specs 库）。

### 故意不做

- **不**改 `orchestrator/src/orchestrator/config.py` —— 没有 GC interval / retention
  这种字段，因为根本没运行的代码用得着；要等真正接入逻辑的下一个 REQ 再加
- **不**改 `orchestrator/src/orchestrator/main.py` —— `run_loop` 不进 startup
  `_bg_tasks`，避免 orchestrator 启动时直接抛 `NotImplementedError` 把进程拖崩
- **不**写实际 K8s API 调用、DB 查询、`_NS_RBAC_DISABLED` 这种 process 级 flag
- **不**写跨场景行为单测（done / escalated retention / RBAC 403）—— 那些是
  实现 REQ 的活，骨架 REQ 只测 API 表面
- **不**写 contract test (`tests/integration/`) —— M18 challenger-agent 的活，
  且骨架阶段无可观测行为可锁
- **不**新建 capability 之外的耦合（不 import `db` / `k8s_runner` / `config`），
  保证 import `orchestrator.accept_env_gc` 在测试里零副作用

## 取舍

- **为什么"骨架 only"值得单独占一个 REQ** —— 上一个全量 REQ
  (`REQ-accept-env-gc-minimal-1777138424`) sonnet 跑爆 token / 反复 escalate，
  说明"全量 8 段一把"在 sisyphus 当前 prompt + 上下文窗口下边界外。把这块
  剥成"先落骨架文件、再落 wiring + 配置、再落实际逻辑"三段，每段独立可 review、
  独立 ci-pass，可以验证 sisyphus 自身的"切片粒度"假设
- **为什么 stub 抛 `NotImplementedError` 而不是空 pass** —— 万一未来不小心把
  `run_loop` 接进 startup 里，立刻在 task 里 raise 比悄悄无限循环 sleep
  显眼得多；属于"fail-loud 防呆"
- **为什么测 `iscoroutinefunction` 而不是直接调** —— `async def` 的同步调用返
  coroutine 而不是抛 NotImplementedError；测试得 `await` 才能拿到真异常。
  同时 lock 住 API 必须是 async（实现 REQ 不能改成 sync）
- **为什么不进 capability `accept-env-gc`** —— 本 REQ 不写任何业务行为契约，
  只 lock 一个文件的 import + 两个函数的 async 签名。把这种"占位"
  写进未来归档的 capability 会把 spec 库污染（archive 后留下"the system SHALL
  raise NotImplementedError"），所以 specs 直接放 `accept-env-gc-skeleton`
  capability，**不进** archive：done_archive 阶段拿 `openspec apply`
  归档时人工 / 后续 REQ 会把它从 changes 删掉而不留 specs/

## 影响面

- 新增 `orchestrator/src/orchestrator/accept_env_gc.py`（约 30 行，含 docstring）
- 新增 `orchestrator/tests/test_accept_env_gc_skeleton.py`（约 35 行）
- 新增 `openspec/changes/REQ-accept-env-gc-skeleton-v2-1777158943/`（proposal /
  tasks / specs/accept-env-gc-skeleton/{spec.md, contract.spec.yaml}）

不动 / 不影响：

- `orchestrator/src/orchestrator/config.py` —— 没新增 settings 字段
- `orchestrator/src/orchestrator/main.py` —— 没接进 startup `_bg_tasks`
- `orchestrator/src/orchestrator/state.py` / `actions/` / `checkers/` —— 状态机
  / 推进动作 / checker 完全不知道这个模块存在
- 其他 capability 的 specs —— 本 REQ 是新 capability `accept-env-gc-skeleton`，
  不 MODIFY / REMOVE 任何已有 spec
- 任何运行行为（pod / db / log 流量）—— 0 import side effect、0 startup wiring
