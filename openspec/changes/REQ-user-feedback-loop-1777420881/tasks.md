## Stage: spec
- [x] 分析现有状态机、webhook、verifier 架构
- [x] 设计 PENDING_USER_PR_REVIEW 状态转换图
- [x] 设计 GitHub webhook endpoint（签名验证、事件解析、REQ 解析）
- [x] 设计 verifier prompt 模板（pr_review_success / pr_review_fail）
- [x] author proposal.md
- [x] author spec.md（openspec delta 格式）

## Stage: implementation
- [x] state.py：新增 PENDING_USER_PR_REVIEW + 3 个 GH PR review Event + transitions
- [x] webhook.py：新增 /github-events endpoint + HMAC 签名验证 + 事件派生
- [x] actions/_verifier.py：新增 pr_review stage + _PASS_ROUTING + 3 个 action handler
- [x] engine.py：STATE_TO_STAGE 加入 pending_user_pr_review
- [x] watchdog.py：_SKIP_STATES + _NO_WATCHDOG_STATES 加入 PENDING_USER_PR_REVIEW
- [x] admin.py：PR_MERGED_VALID_STATES 加入 PENDING_USER_PR_REVIEW
- [x] config.py：新增 github_webhook_secret
- [x] post_acceptance_report.py：更新文案为 GitHub PR review 拍板方式
- [x] prompts/verifier/pr_review_success.md.j2
- [x] prompts/verifier/pr_review_fail.md.j2

## Stage: test
- [x] 更新 test_contract_user_acceptance_gate.py（TEARDOWN_DONE_PASS 新目标 + PENDING_USER_PR_REVIEW transitions）
- [x] 新增 test_contract_pr_review_feedback_loop.py（事件推导、签名验证、REQ 解析、transition、apply_verify_pass 路由）
- [x] 运行 verifier loop / watchdog / engine / PR merged 相关回归测试（全部通过）

## Stage: PR
- [ ] git push feat/REQ-user-feedback-loop-1777420881
- [ ] gh pr create --label sisyphus
