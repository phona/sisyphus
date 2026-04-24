DROP INDEX IF EXISTS idx_event_seen_processed;
ALTER TABLE event_seen DROP COLUMN IF EXISTS processed_at;
