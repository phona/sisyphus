-- Sisyphus orchestrator schema. Two tables：req_state（状态）+ event_seen（dedup）。
-- 复用 docs/observability.md 规划的 sisyphus Postgres 实例。

CREATE TABLE IF NOT EXISTS req_state (
    req_id          TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    state           TEXT NOT NULL,
    -- 历史 transition 记录（JSONB array of {ts, from_state, to_state, event, action}）
    history         JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- 业务上下文（issue_id 引用、最近一次 ci issue id、bugfix round 计数 …）
    context         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS req_state_state_idx       ON req_state(state);
CREATE INDEX IF NOT EXISTS req_state_updated_at_idx  ON req_state(updated_at DESC);

CREATE TABLE IF NOT EXISTS event_seen (
    event_id        TEXT PRIMARY KEY,
    seen_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS event_seen_at_idx ON event_seen(seen_at DESC);

-- GC 老 dedup 记录（>7d）的 helper（手动 cron 调）
-- DELETE FROM event_seen WHERE seen_at < now() - interval '7 days';

-- !rollback
DROP TABLE IF EXISTS event_seen;
DROP TABLE IF EXISTS req_state;
