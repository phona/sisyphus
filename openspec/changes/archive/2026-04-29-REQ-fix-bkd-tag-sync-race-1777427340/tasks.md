## Stage: implementation

- [x] `bkd_rest.py` — `merge_tags_and_update` 加乐观锁重试
  - get → merge → update → 验证（result.tags vs expected）
  - 不一致时再次 get 确认
  - 最多重试 3 次，超次 log warning 后返回最后一次结果
- [x] `bkd_mcp.py` — MCP 客户端同步同样逻辑
- [x] `start_analyze.py` — admission 拒绝时 BKD 同步
  - `merge_tags_and_update(add=["reason:rate-limit"])`
  - `follow_up_issue("当前并发 REQ 已满（...），请稍后重试或联系管理员扩容。")`
  - fail-open：BKD 同步失败不阻塞 escalation
- [x] `test_actions_start_analyze.py` — 更新 admission deny 测试
  - 验证 merge_tags_and_update 被调用且含 `reason:rate-limit`
  - 验证 follow_up 消息包含具体拒绝原因
- [x] openspec docs（proposal.md / tasks.md / spec.md / contract.spec.yaml）

## Stage: PR

- [x] git push feat/REQ-fix-bkd-tag-sync-race-1777427340
- [x] gh pr create with sisyphus label
