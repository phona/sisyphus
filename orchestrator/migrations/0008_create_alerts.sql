-- 0008: alerts 表，持久化 warn / critical 告警，对接 Telegram 推送。
CREATE TABLE IF NOT EXISTS alerts (
    id               BIGSERIAL PRIMARY KEY,
    severity         TEXT NOT NULL CHECK (severity IN ('info', 'warn', 'critical')),
    req_id           TEXT,
    stage            TEXT,
    reason           TEXT NOT NULL,
    hint             TEXT,
    suggested_action TEXT,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    acknowledged_at  TIMESTAMPTZ,
    sent_to_tg       BOOLEAN DEFAULT FALSE
);

CREATE INDEX idx_alerts_unack ON alerts(created_at DESC) WHERE acknowledged_at IS NULL;
