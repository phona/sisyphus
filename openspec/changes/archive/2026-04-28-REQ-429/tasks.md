# REQ-429 Tasks

## Stage: spec
- [x] author specs/admin-escalate-kind/spec.md with scenarios EKS-S1..S6

## Stage: implementation
- [x] Add `EscalateBody` model with `kind: str = "admin"` to `admin.py`
- [x] Update `force_escalate` to accept optional `EscalateBody` body
- [x] Use `params.kind` in SQL UPDATE context JSON (replaces hard-coded `"admin"`)
- [x] Add BKD sync after runner cleanup task (merge_tags_and_update + status_id=review)
- [x] Add imports for `BKDClient` and `settings` in `admin.py`
- [x] Add `kind` to response dict

## Stage: tests
- [x] Fix existing `test_force_escalate_marks_escalated_and_triggers_cleanup` (response now includes `kind`)
- [x] Add `EscalateBody` mock to existing happy-path test
- [x] EKS-S1: EscalateBody default kind="admin"
- [x] EKS-S2: EscalateBody custom kind
- [x] EKS-S3: custom kind written to SQL ctx + response
- [x] EKS-S4: BKD sync called with correct args (tags + status_id=review)
- [x] EKS-S5: fallback to req_id when no intent_issue_id in ctx
- [x] EKS-S6: BKD failure does not block SQL UPDATE + cleanup task

## Stage: PR
- [x] git push feat/REQ-429
- [x] gh pr create --label sisyphus
