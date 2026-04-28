# Proposal: fix(escalate): null escalated_reason audit + 默认 reason 兜底

## 问题

Metabase 仪表盘发现 4 条 `state='escalated'` 行的 `context->>'escalated_reason' IS NULL`，
导致看板 Q 系列 SQL 分组黑洞。

## 根因分析

两类代码缺陷：

1. **caller 不写 reason**：多个 action 在 emit 进 ESCALATED 路径的 event 前没有调
   `req_state.update_context` 设置 `escalated_reason`，依赖 `escalate.py` 的 ctx
   读取——但 escalate 读的是 CAS 前已存在的 ctx，caller 不写就是 null。

   受影响路径：
   - `start_analyze.py`：clone 失败 → `VERIFY_ESCALATE`
   - `start_analyze_with_finalized_intent.py`：finalized intent 缺失 → `VERIFY_ESCALATE`；
     clone 失败 → `VERIFY_ESCALATE`
   - `create_pr_ci_watch.py`：`ValueError` config error → `PR_CI_TIMEOUT`；
     `exit_code=124` → `PR_CI_TIMEOUT`（直通 ESCALATED，不过 verifier）
   - `create_accept.py`：5 个 `ACCEPT_ENV_UP_FAIL` 路径（no integration dir /
     exec crash / non-zero exit / bad JSON / missing endpoint）

2. **escalate.py 写太晚**：`escalated_reason` 在 GH incident + BKD tag 写完后才落
   `ctx_patch`，如果这段代码中途抛异常，状态行已 CAS 到 ESCALATED 但 reason 仍为 null。

## 修复策略（双保险）

1. 各 action 在 emit 前调 `req_state.update_context` 写明 reason
2. `escalate.py` 在 GH incident 代码之前提前落 `escalated_reason`；同时加 `if not final_reason` 兜底为 `"unknown"`

## 影响范围

- `orchestrator/src/orchestrator/actions/escalate.py`
- `orchestrator/src/orchestrator/actions/start_analyze.py`
- `orchestrator/src/orchestrator/actions/start_analyze_with_finalized_intent.py`
- `orchestrator/src/orchestrator/actions/create_pr_ci_watch.py`
- `orchestrator/src/orchestrator/actions/create_accept.py`
- `orchestrator/tests/test_contract_escalate_reason.py`（新增 8 条回归测试）
