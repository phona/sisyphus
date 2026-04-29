## Stage: implementation

- [x] 砍 ARCHIVING state 和 ARCHIVE_DONE event
- [x] 修改 transitions：PENDING_USER_REVIEW/PR_MERGED → DONE（不是 ARCHIVING）
- [x] engine.py 新增 _auto_archive 后台 fire-and-forget 任务
- [x] done_archive.py 替换为注释说明文件
- [x] 删除 done_archive.md.j2 prompt
- [x] 更新 execute.md.j2 中的 archive 描述
- [x] 更新所有测试（state tests / engine tests / contract tests）
- [x] 更新 docs/state-machine.md
- [x] 编译验证通过

## Stage: PR

- [ ] git push feat/REQ-archive-automation-1777452516
- [ ] gh pr create --label sisyphus
