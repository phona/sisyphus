"""M4 故障分级重试。

`policy.decide(stage, fail_kind, round)` 返回一个 RetryDecision（纯函数，好单测）；
`executor.run(ctx)` 按 decision 调 BKDClient / 持久化 round / 可选 emit 事件。

接入点：M1/M2/M3 等 checker 的 fail 路径在 feature flag 打开时改调 executor，
不再直接 emit FAIL event。老 BKD agent 路径保持不变（retry_enabled=False 时）。
"""
