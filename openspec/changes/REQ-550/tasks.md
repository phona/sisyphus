# REQ-550 Tasks

## Stage: spec
- [x] author specs/webhook-dedup-soft-skip/spec.md with scenarios DDS-S1..S4

## Stage: implementation
- [x] Remove `mark_processed` from `skip_no_req_tag` early return (session.completed without REQ tag)
- [x] Remove `mark_processed` from `skip_no_req_or_intent_tag` early return (issue.updated without REQ or intent tag)
- [x] Remove `mark_processed` from `no_event_mapping` early return (event is None after derive_event)
- [x] Remove `mark_processed` from `no_req_id` early return (req_id is None after resolve)
- [x] Verify `mark_processed` is still called only after `engine.step` success

## Stage: tests
- [x] Update RNF-S1: assert `mark_processed` NOT called on noise skip
- [x] Update RNF-S5: assert `mark_processed` NOT called on noise skip
- [x] Add DEDUP-S7: early skip + retry path test

## Stage: PR
- [ ] git push feat/REQ-550
- [ ] gh pr create --label sisyphus
