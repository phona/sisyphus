# REQ-pr-ready-for-review-notify-1777344793: PR ready for review 알림 (Phase 1)

## 배경

REQ가 pr_ci를 통과하여 REVIEW_RUNNING 진입 시 PR이 이미 열려있고 mergeable 상태임에도,
사용자는 BKD intent issue가 여전히 `working` 상태로 보여 언제 심사해야 하는지 알 수 없다.
이 "마지막 1km" UX 공백을 BKD intent issue 태그로 최소 가시 신호를 제공하는 방식으로 해소한다.

## 해결 방법 (Phase 1)

REQ가 어떤 stage에서든 REVIEW_RUNNING에 진입할 때:

1. `ctx.pr_urls`(PR이 이미 발견된 경우 채워짐)가 비어있지 않으면
2. BKD intent issue에 `pr-ready` 태그 + `pr:owner/repo#N` 태그(각 PR마다)를 추가
3. statusId는 변경하지 않음 (sisyphus 상태 기계가 자체 관리)
4. BKD PATCH 실패 시 warning 로그만 남기고 상태 전이 차단 안 함

## 구현 위치

- `orchestrator/src/orchestrator/engine.py`: `_tag_intent_pr_ready()` 헬퍼 + `step()`에서 호출
- `orchestrator/src/orchestrator/intent_tags.py`: `pr-ready`를 sisyphus-managed 태그로 등록

## 범위 외

- Telegram/Slack/이메일 알림 (독립 REQ)
- BKD comment 추가 (Phase 2, 선택 사항)
- statusId를 review로 전환 (sisyphus 상태 기계 자체 관리 유지)
