## Stage: spec / design
- [x] 定义 hotfix 精简流水线状态图（跳过 intake/spec-lint/challenger/accept）
- [x] 设计 hotfix 入口（intent:hotfix tag）
- [x] 设计 hotfix 审计标记（ctx.hotfix + archive tag）
- [x] 设计 verifier 框架 hotfix 路径兼容方案

## Stage: implementation
- [x] state.py: 新增 HOTFIX_* 状态 + INTENT_HOTFIX 事件 + 26 条 transition
- [x] router.py: derive_event 识别 intent:hotfix
- [x] webhook.py: hotfix 入口过滤 + ctx.hotfix 初始化
- [x] engine.py: STATE_TO_STAGE / AGENT_STAGES 增加 hotfix 映射
- [x] _verifier.py: _HOTFIX_PASS_ROUTING / _HOTFIX_RETRY_ROUTING + apply_verify_pass / apply_verify_infra_retry 支持 hotfix
- [x] done_archive.py: hotfix tag + [HOTFIX DONE] 标题
- [x] docs/state-machine.md: 更新计数和表格

## Stage: test
- [x] test_state.py: hotfix transition 参数化测试 + 状态存在性 + SESSION_FAILED + 无孤儿 action
- [x] test_router.py: intent:hotfix / 已接管返回 None
- [x] test_state_transitions_gap.py: 更新计数（79）、新增 hotfix 非法事件负向断言
- [x] test_engine_escalated_resume.py: 全量 sweep 覆盖 79 条 transition

## Stage: PR
- [x] git push feat/REQ-hotfix-mode-1777420843
- [ ] gh pr create --label sisyphus
