"""一次性运维 CLI 集合（不在 sisyphus 主进程的热路径上）。

每个模块独立可跑：`python -m orchestrator.maintenance.<name>`，依赖最小，
不拉 settings / db pool / engine。失败 / 不再用了直接删，没人会引。
"""
