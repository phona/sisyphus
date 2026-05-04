-- Rollback REQ-refactor-analyze-execute-392 (closes #392)
-- Reverse mapping for migration 0017_rename_analyze_to_execute.sql.

UPDATE req_state
   SET cur_state = 'analyzing'
 WHERE cur_state = 'executing';

UPDATE req_state
   SET cur_state = 'analyze-artifact-checking'
 WHERE cur_state = 'execute-artifact-checking';

UPDATE event_log
   SET event_name = 'intent.analyze'
 WHERE event_name = 'intent.execute';

UPDATE event_log
   SET event_name = 'analyze.done'
 WHERE event_name = 'execute.done';

UPDATE event_log
   SET event_name = 'analyze-artifact-check.pass'
 WHERE event_name = 'execute-artifact-check.pass';

UPDATE event_log
   SET event_name = 'analyze-artifact-check.fail'
 WHERE event_name = 'execute-artifact-check.fail';

UPDATE stage_runs
   SET stage = 'analyze'
 WHERE stage = 'execute';

UPDATE stage_runs
   SET stage = 'analyze_artifact_check'
 WHERE stage = 'execute_artifact_check';

UPDATE verifier_decisions
   SET stage = 'analyze'
 WHERE stage = 'execute';

UPDATE verifier_decisions
   SET stage = 'analyze_artifact_check'
 WHERE stage = 'execute_artifact_check';

UPDATE artifact_checks
   SET stage = 'analyze-artifact-check'
 WHERE stage = 'execute-artifact-check';
