# REQ-530 Tasks

## Stage: deploy
- [x] 检查数据库状态：确认 `sisyphus` 主库和 `sisyphus_obs` 库存在
- [x] 创建 `metabase` 数据库（Metabase 应用元数据库）
- [x] 修正 values/metabase.yaml：镜像标签、host、secret、ingress 格式、javaOpts、siteUrl、extraEnv
- [x] Helm install Metabase（pmint/metabase chart）
- [x] 等待 Pod ready（首次启动需初始化数据库）

## Stage: setup
- [x] 首次设置：通过 `/api/setup` 创建 admin + 添加 `sisyphus` 数据源
- [x] 运行 setup_metabase.py 导入 18 条 SQL Question（Q1–Q18）
- [x] 修复 setup_metabase.py：dashboard card `id` 用唯一负数（Metabase v0.50 API 兼容性）
- [x] 运行 setup_metabase.py 创建 3 个 Dashboard（M7 / M14e / Fixer Audit）

## Stage: fix
- [x] 修复 Q5 SQL（05-active-req-overview.sql）：`USING(req_id)` → `ON` 语法，避免多层 JOIN 列名冲突
- [x] 验证 Q5 可正常出数（返回 2 行活跃 REQ）
- [x] 验证 Q1/Q3/Q6/Q8/Q12/Q16/Q18 均可正常执行

## Stage: PR
- [x] 写 openspec/changes/REQ-530/proposal.md + tasks.md
- [x] git push feat/REQ-530
- [x] gh pr create --label sisyphus
