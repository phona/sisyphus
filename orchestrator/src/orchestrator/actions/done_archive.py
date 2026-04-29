"""done_archive 已改为 engine._auto_archive 后台 fire-and-forget 任务。

REQ-archive-automation-1777452516：archive 不再起 BKD agent，transition 到 DONE
时由 engine 异步触发 runner pod 内 `openspec archive REQ --yes && git commit`。
失败只 log warning，不阻塞状态机。
"""
