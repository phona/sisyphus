# Tasks — REQ-fix-verifier-schema-395-1777869659

## Stage: contract / spec
- [x] author specs/verifier-decision-retry-cap/spec.md (3 scenarios: VDRC-S1..S3)
- [x] write proposal.md / tasks.md

## Stage: implementation
- [x] update `orchestrator/src/orchestrator/webhook.py`: retry cap 2 → 3, expand `_VERIFIER_RETRY_PROMPT` with mandate phrases (last-assistant-message + decision tag + 4 valid actions)
- [x] add `scripts/lint-verifier-prompts.py` static checker (stdlib python3, exit non-zero on violation)
- [x] wire lint into `.github/workflows/orchestrator-ci.yml` (new step in lint-test job, after ruff)
- [x] unit tests in `orchestrator/tests/test_webhook_verifier_retry.py` covering retry_count=2 (still retries) and retry_count=3 (escalate); update existing test names/comments to reflect new cap
- [x] standalone test `orchestrator/tests/test_lint_verifier_prompts.py` for the new lint script

## Stage: PR (gates before push)
- [x] `make ci-lint` green
- [x] `make ci-unit-test` green
- [x] `make ci-integration-test` green (or skipped if no PG)
- [x] `python3 scripts/lint-verifier-prompts.py` green (self-test)
- [x] git push feat/REQ-fix-verifier-schema-395-1777869659
- [x] gh pr create --label sisyphus
