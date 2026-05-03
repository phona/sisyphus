# Tasks — REQ-fix-verifier-decision-tag-1777812498

## Stage: contract / spec
- [x] author specs/verifier-decision-tag-fallback/spec.md (3 scenarios: VDTF-S1..S3)
- [x] write proposal.md / tasks.md / design.md

## Stage: implementation
- [x] update `orchestrator/src/orchestrator/prompts/verifier/_decision.md.j2` to mandate emitting `decision:<action>[-<fixer>]` tag (with curl example)
- [x] extend `orchestrator/src/orchestrator/verifier_parser.py` with `_extract_from_plain_decision_tag` recognizer; integrate as 3rd extraction source in `extract_decision_robust`
- [x] unit tests in `orchestrator/tests/test_verifier_parser.py` (or equivalent) covering VDTF-S1..S3

## Stage: PR (gates before push)
- [x] `make ci-lint` green
- [x] `make ci-unit-test` green
- [x] `make ci-integration-test` green (or skipped if no PG)
- [x] git push feat/REQ-fix-verifier-decision-tag-1777812498
- [x] gh pr create --label sisyphus
