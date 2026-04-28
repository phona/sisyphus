# REQ-533: feat(orchestrator): 启动时自动 apply observability schema

## Why

`observability/schema.sql` 定义了 sisyphus_obs 数据库的完整 schema（4 张表 + 5 个索引 + 3 个 view），但目前 orchestrator 启动时**不自动执行**该 schema。新环境部署需要手动建表，易遗漏，导致 observability 数据写入失败或缺失。

## What changes

**NEW** `orchestrator/src/orchestrator/obs_schema.py` — 封装 observability schema 自动应用逻辑：

1. `apply_obs_schema()` 在 `main.py` startup 流程中于 `init_obs_pool` 之后调用
2. 自动定位 `observability/schema.sql`（env 覆盖 > cwd 相对 > 包相对 fallback）
3. 幂等：schema.sql 全用 `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` / `CREATE OR REPLACE VIEW`
4. best-effort：obs DSN 未配、schema 文件不存在、或执行失败 —— 均 log warning，不阻断主服务启动
5. 无 schema_version 表：依赖 SQL 本身的 `IF NOT EXISTS` 语义做幂等，保持简单

**MOD** `orchestrator/src/orchestrator/main.py` — startup 流程第 2b 步插入 `await apply_obs_schema()`

**NEW** `orchestrator/tests/test_obs_schema.py` — 7 个单元测试覆盖：
- obs pool 未配时跳过
- schema 文件不存在时跳过
- 空 schema 文件时跳过
- 正常执行 schema SQL
- 执行异常时 swallow 并返回 False
- env 覆盖路径解析
- fallback 路径存在性验证

## Impact

- 无 schema 变更，无 migration 变更。
- 全新部署后 `event_log` 等 observability 表自动存在，无需人工干预。
- 失败时主服务仍可启动，符合 observability "best-effort" 设计原则。
