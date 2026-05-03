-- 0016: stage_runs.context — JSONB column for richer stage attribution.
--
-- Driver: feat-cross-repo-env-orchestration spec R10 records per-layer
-- accept-env-up outcomes (failed_layer / failed_field / layers[]) on the
-- stage_runs row representing the accept stage. Existing fail_reason TEXT
-- can't carry structured per-layer timing without bespoke parsing.
--
-- Default NULL preserves zero-impact for stage runs that never write context.
-- New writes use COALESCE($2, context) merge in close_latest_stage_run.

ALTER TABLE stage_runs ADD COLUMN IF NOT EXISTS context JSONB;

COMMENT ON COLUMN stage_runs.context IS
  'Optional structured attribution emitted by the action that closed the stage. '
  'For accept stage this records {failed_layer, failed_field, layers:[{repo,status,duration_ms}]}.';
