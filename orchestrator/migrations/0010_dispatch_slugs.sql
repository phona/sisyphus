-- REQ-427: dispatch_slugs — slug-based idempotency for BKD issue creation
--
-- Before each bkd.create_issue() call, an action handler checks this table.
-- If the slug already maps to an issue_id, the existing id is reused and no
-- POST is issued to BKD.  This prevents duplicate issue creation on webhook
-- retry (dedup status = "retry") when the process crashes between create_issue
-- and mark_processed.
--
-- Slug schemes:
--   invoke_verifier : verifier|{req_id}|{stage}|{trigger}|r{fixer_round}
--   start_fixer     : fixer|{req_id}|{fixer}|r{next_round}
--   other handlers  : {action}|{req_id}|{executionId or ""}

CREATE TABLE IF NOT EXISTS dispatch_slugs (
    slug        TEXT PRIMARY KEY,
    issue_id    TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
