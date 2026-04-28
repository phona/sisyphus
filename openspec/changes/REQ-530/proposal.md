# REQ-530: 部署 Metabase + 导入 18 条 SQL 查询 + 建立 Dashboard

## Problem

values/metabase.yaml 存在但 Metabase 从未实际部署运行。18 条 Metabase SQL 查询在 observability/queries/sisyphus/ 中无处可跑，dashboard 设计文档 observability/sisyphus-dashboard.md 只停留在纸面。

## Solution

1. **修正 values/metabase.yaml** —— 修正多个部署障碍：
   - 镜像标签 `v0.50.0` 在 Docker Hub 不存在 → 改为 `v0.50.36`
   - PostgreSQL host/secret 名错误 → 修正为 `sisyphus-postgresql`
   - `existingSecretKey` 不支持 → 改为 `existingSecretPasswordKey`
   - ingress 格式不匹配 pmint/metabase chart → 改为 `className` + `hosts` 字符串数组
   - `env.JAVA_OPTS` 不被 chart 识别 → 改用 `javaOpts`
   - `env.MB_SITE_URL` 不被 chart 识别 → 改用 `siteUrl`
   - 额外 env 改用 `extraEnv` 数组

2. **手动创建 `metabase` 数据库** —— Metabase 应用自身的元数据库，与业务库 `sisyphus` 分离。

3. **首次设置（first-time setup）** —— 通过 `/api/setup` 创建 admin 账号 + 自动添加 `sisyphus` 数据源。

4. **运行 setup_metabase.py** —— 自动导入 18 条 SQL Question（Q1–Q18）和 3 个 Dashboard：
   - M7 — Checker Health（7 cards）
   - M14e — Agent Quality（11 cards）
   - Fixer Audit（3 cards）

5. **修复 setup_metabase.py** —— Metabase v0.50 `PUT /api/dashboard/{id}/cards` 要求每个 card 有唯一 `id`，原脚本 `"id": None` 导致 400。改为用 `enumerate` 生成唯一负数 id（`-1, -2, -3...`）。

6. **修复 Q5 SQL** —— `USING (req_id)` 在多层 JOIN（多个 CTE 都有 `req_id` 列）时导致 `common column name "req_id" appears more than once in left table`。改为 `ON` 语法。

## Scope

- `values/metabase.yaml` —— Helm values 修正
- `observability/setup_metabase.py` —— Dashboard API 兼容性修复
- `observability/queries/sisyphus/05-active-req-overview.sql` —— JOIN 语法修复

无业务代码改动，不涉及 orchestrator 核心逻辑。
