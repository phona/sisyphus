# Tasks: REQ-pr-ready-for-review-notify-1777344793

## Stage: spec
- [x] author specs/pr-ready-notify/spec.md scenarios
- [x] author specs/pr-ready-notify/contract.spec.yaml

## Stage: implementation
- [x] engine.py: `_tag_intent_pr_ready()` 헬퍼 추가
- [x] engine.py: `step()`에서 REVIEW_RUNNING 진입 시 fire-and-forget 호출
- [x] intent_tags.py: `pr-ready`를 SISYPHUS_MANAGED_EXACT에 추가

## Stage: unit tests
- [x] test_contract_pr_ready_notify.py: PRN-S1 ~ PRN-S5 (5개 테스트)
  - [x] PRN-S1: REVIEW_RUNNING + pr_urls 비어있지 않음 → pr-ready + pr:repo#N 태그 추가
  - [x] PRN-S2: pr_urls = {} (빈 dict) → BKD PATCH 없음
  - [x] PRN-S3: pr_urls = None → BKD PATCH 없음
  - [x] PRN-S4: BKD 5xx → warning 로그 + 상태 전이 불차단
  - [x] PRN-S5: intent_issue_id 없음 → no-op

## Stage: PR
- [x] git push feat/REQ-pr-ready-for-review-notify-1777344793
- [x] gh pr create --label sisyphus
