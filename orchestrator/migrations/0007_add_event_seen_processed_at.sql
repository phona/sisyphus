-- 0007: event_seen に processed_at カラムを追加し、at-least-once retry を安全にサポート。
-- processed_at IS NULL = 首次处理崩溃，下次 BKD 重发时允许 retry。
-- processed_at IS NOT NULL = 已成功处理，重发直接 skip。

ALTER TABLE event_seen ADD COLUMN processed_at TIMESTAMPTZ;

COMMENT ON COLUMN event_seen.processed_at IS
  'webhook handler 跑完成功时打。NULL 表示首次处理崩溃，下次 BKD 重发时允许 retry';

CREATE INDEX idx_event_seen_processed ON event_seen(processed_at)
  WHERE processed_at IS NULL;
