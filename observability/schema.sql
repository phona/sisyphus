-- Sisyphus 可观测性数据库 schema
--
-- 4 张表 + 5 个索引 + 3 个 view。
-- Postgres 15+。年增 <100K 行，单实例舒适区，不需分区。
--
-- 部署：随 Bitnami postgresql chart 的 initdb.scripts 挂载自动执行。
-- 手动执行：psql -h <host> -U sisyphus -d sisyphus_obs -f schema.sql

-- ===================================================================
-- 1. event_log — n8n tap 写入的事件流（append-only）
--
-- 只记 BKD 看不见的事件：Router 决策 / action 执行 / 检测命中 /
-- 人工介入 / session 成本。BKD 自己能答的当前状态走 bkd_snapshot。
-- ===================================================================
CREATE TABLE IF NOT EXISTS event_log (
  id               BIGSERIAL PRIMARY KEY,
  ts               TIMESTAMPTZ NOT NULL DEFAULT now(),
  kind             TEXT NOT NULL,
    -- 已知 kind（不用 enum type，方便加新值）：
    --   webhook.received   BKD webhook 进入 n8n
    --   router.decision    Router 输出的 action
    --   gate.check         SPG gate 判断
    --   action.executed    BKD/GH/Lark HTTP 调用成功
    --   action.failed      同上但非 2xx
    --   dedup.hit          dedup 闸命中跳过
    --   check.violation    检测规则命中
    --   human.approval     审批按钮点击
    --   session.cost       BKD session 结束的 token 数

  -- 业务维度（可空，不同 kind 填不同列）
  req_id           TEXT,
  stage            TEXT,       -- analyze / dev-spec / contract-spec /
                               --   accept-spec / ui-spec / migration-spec /
                               --   dev / ci / bugfix / test-fix /
                               --   reviewer / accept / done-archive
  issue_id         TEXT,
  parent_issue_id  TEXT,
  parent_stage     TEXT,

  -- tag 解析后的结构化列（查得快 + Metabase 好用）
  tags             TEXT[],     -- 原始 tag，array 类型
  layer            TEXT[],     -- 解析自 layer:*
  round            INT,        -- 解析自 round-N
  target           TEXT,       -- ci target: lint / unit / integration

  -- router.decision 专属
  router_action    TEXT,
  router_reason    TEXT,

  -- action.* 专属
  duration_ms      INT,
  status_code      INT,
  error_msg        TEXT,

  -- ci.runner.result 专属（从 ci runner issue description 解析）
  exit_code        INT,
  failed_tests     TEXT[],

  -- session.cost 专属
  token_in         INT,
  token_out        INT,

  -- 追溯 / 版本
  config_version   TEXT,       -- Router/prompt/threshold 的 hash，改动 bump
  n8n_exec_id      TEXT,       -- 点进 n8n UI 看原始 execution

  -- 兜底存原始 payload，debug 用
  extras           JSONB
);

CREATE INDEX IF NOT EXISTS idx_event_req_ts     ON event_log (req_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_issue_ts   ON event_log (issue_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_kind_ts    ON event_log (kind, ts DESC);
CREATE INDEX IF NOT EXISTS idx_event_stage_ts   ON event_log (stage, ts DESC)
  WHERE stage IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_event_config_ver ON event_log (config_version)
  WHERE config_version IS NOT NULL;
-- tags 数组查询：GIN 索引支持 @> 操作符
CREATE INDEX IF NOT EXISTS idx_event_tags_gin   ON event_log USING GIN (tags);


-- ===================================================================
-- 2. bkd_snapshot — BKD issue 当前状态（覆盖式，n8n cron 刷）
--
-- BKD 是主账本，这张表只是它的本地镜像，方便 Metabase 聚合查询。
-- 由 n8n 定时 workflow 每 5 分钟 list-issues + upsert。
-- ===================================================================
CREATE TABLE IF NOT EXISTS bkd_snapshot (
  issue_id         TEXT PRIMARY KEY,
  req_id           TEXT,
  stage            TEXT,
  status           TEXT,       -- todo / working / review / done / cancelled
  title            TEXT,
  tags             TEXT[],
  layer            TEXT[],
  round            INT,
  target           TEXT,
  parent_issue_id  TEXT,
  parent_stage     TEXT,

  -- BKD 自己的时间戳
  created_at       TIMESTAMPTZ,
  bkd_updated_at   TIMESTAMPTZ,

  -- 本地 sync 时间
  synced_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_snap_req_stage  ON bkd_snapshot (req_id, stage);
CREATE INDEX IF NOT EXISTS idx_snap_status     ON bkd_snapshot (status)
  WHERE status IN ('working','review');
CREATE INDEX IF NOT EXISTS idx_snap_updated    ON bkd_snapshot (bkd_updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_snap_tags_gin   ON bkd_snapshot USING GIN (tags);


-- ===================================================================
-- 3. config_version — 追溯 Router / prompt / 阈值的变更
--
-- 每次改 Router 规则 / agent prompt / 熔断阈值，插一行。
-- event_log.config_version 引用此表的 version_hash，Metabase 可按
-- 版本拆列做 A/B 对比。
-- ===================================================================
CREATE TABLE IF NOT EXISTS config_version (
  version_hash     TEXT PRIMARY KEY,  -- git short SHA 或自定义 hash
  kind             TEXT NOT NULL,     -- router / prompt / threshold
  target           TEXT,              -- contract-test-agent / CB_THRESHOLD / ...
  applied_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  retired_at       TIMESTAMPTZ,       -- NULL 表示当前在用
  git_commit       TEXT,              -- 完整 SHA
  diff_summary     TEXT,              -- 1 行描述本次改了啥
  author           TEXT
);

CREATE INDEX IF NOT EXISTS idx_cfg_active ON config_version (kind, target)
  WHERE retired_at IS NULL;


-- ===================================================================
-- 4. improvement_log — 假设 → 验证循环（人工维护为主）
--
-- 把"改了什么 / 为什么改 / 验证结果"落成可查的记录，支撑可持续改进。
-- Metabase 里按 metric_name 分组看历次 verdict，就是改进成绩单。
-- ===================================================================
CREATE TABLE IF NOT EXISTS improvement_log (
  id               BIGSERIAL PRIMARY KEY,
  opened_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at        TIMESTAMPTZ,

  hypothesis       TEXT NOT NULL,      -- "改 contract-test prompt 加 oneOf 指令，
                                       --  预期一把过率从 40% 升到 65%"
  config_version   TEXT REFERENCES config_version(version_hash),

  metric_name      TEXT NOT NULL,      -- contract_test_first_pass_rate
  metric_sql       TEXT NOT NULL,      -- 计算该 metric 的 SQL（存着防忘）

  baseline_value   NUMERIC,
  target_value     NUMERIC,
  observed_value   NUMERIC,
  verdict          TEXT,               -- success / no_effect /
                                       --   regression / inconclusive

  notes            TEXT
);

CREATE INDEX IF NOT EXISTS idx_imp_verdict ON improvement_log (verdict, closed_at DESC);


-- ===================================================================
-- Views — Metabase 看板和 Alert 主力查询
-- ===================================================================

-- REQ 全链路时间轴
CREATE OR REPLACE VIEW req_timeline AS
SELECT
  s.req_id,
  s.issue_id,
  s.stage,
  s.status,
  s.round,
  s.created_at,
  s.bkd_updated_at AS last_update,
  EXTRACT(EPOCH FROM (s.bkd_updated_at - s.created_at))::INT AS duration_sec,
  s.parent_issue_id,
  s.parent_stage
FROM bkd_snapshot s
WHERE s.req_id IS NOT NULL;

-- agent 质量
CREATE OR REPLACE VIEW agent_quality AS
SELECT
  stage AS agent_role,
  COUNT(*) FILTER (WHERE status='done') AS completed,
  COUNT(*) FILTER (WHERE round > 0) AS needed_retry,
  ROUND(100.0 * COUNT(*) FILTER (WHERE round=0 AND status='done')
        / NULLIF(COUNT(*), 0), 1) AS first_pass_rate_pct
FROM bkd_snapshot
WHERE stage IS NOT NULL
GROUP BY stage;

-- REQ 成本
CREATE OR REPLACE VIEW req_cost AS
SELECT
  e.req_id,
  SUM(e.token_in) AS total_token_in,
  SUM(e.token_out) AS total_token_out,
  COUNT(DISTINCT e.issue_id) AS issue_count,
  MIN(e.ts) AS started_at,
  MAX(e.ts) AS last_event_at
FROM event_log e
WHERE e.req_id IS NOT NULL
GROUP BY e.req_id;

-- 最近 7 天检测规则命中
CREATE OR REPLACE VIEW recent_violations AS
SELECT
  e.ts,
  e.req_id,
  e.extras->>'rule' AS rule,
  e.extras->>'reason' AS reason,
  e.extras->>'suggested_action' AS suggested_action,
  s.stage AS current_stage,
  s.status AS current_status
FROM event_log e
LEFT JOIN bkd_snapshot s USING (req_id)
WHERE e.kind = 'check.violation'
  AND e.ts > now() - interval '7 days'
ORDER BY e.ts DESC;
