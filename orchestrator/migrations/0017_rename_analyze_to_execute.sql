-- REQ-refactor-analyze-execute-392 (closes #392)
-- Stage rename: analyze → execute (analyze 暗示只看不动手，实际是全责交付 spec + code + PR)
--
-- Migrate stored string values for in-flight + historical rows so Metabase /
-- observability queries see the new naming and ReqState(...) / Event(...)
-- StrEnum readback works on resumed REQ. Columns affected:
--   req_state.cur_state            (ReqState value)
--   event_log.event_name           (Event value)
--   stage_runs.stage               (engine.STATE_TO_STAGE value)
--   verifier_decisions.stage       (verifier-agent stage label)
--   artifact_checks.stage          (kind label written by checker)
--
-- All UPDATEs are idempotent (no-op if already migrated). Single-instance
-- sisyphus + dev traffic = no worry about lock contention.

UPDATE req_state
   SET cur_state = 'executing'
 WHERE cur_state = 'analyzing';

UPDATE req_state
   SET cur_state = 'execute-artifact-checking'
 WHERE cur_state = 'analyze-artifact-checking';

UPDATE event_log
   SET event_name = 'intent.execute'
 WHERE event_name = 'intent.analyze';

UPDATE event_log
   SET event_name = 'execute.done'
 WHERE event_name = 'analyze.done';

UPDATE event_log
   SET event_name = 'execute-artifact-check.pass'
 WHERE event_name = 'analyze-artifact-check.pass';

UPDATE event_log
   SET event_name = 'execute-artifact-check.fail'
 WHERE event_name = 'analyze-artifact-check.fail';

UPDATE stage_runs
   SET stage = 'execute'
 WHERE stage = 'analyze';

UPDATE stage_runs
   SET stage = 'execute_artifact_check'
 WHERE stage = 'analyze_artifact_check';

UPDATE verifier_decisions
   SET stage = 'execute'
 WHERE stage = 'analyze';

UPDATE verifier_decisions
   SET stage = 'execute_artifact_check'
 WHERE stage = 'analyze_artifact_check';

UPDATE artifact_checks
   SET stage = 'execute-artifact-check'
 WHERE stage = 'analyze-artifact-check';
