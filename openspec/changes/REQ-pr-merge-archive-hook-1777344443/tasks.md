# Tasks: REQ-pr-merge-archive-hook-1777344443

## Stage: spec
- [x] author specs/pr-merged-archive-hook/spec.md with scenarios PMH-S1 through PMH-S10
- [x] author specs/pr-merged-archive-hook/contract.spec.yaml

## Stage: implementation
- [x] Add `Event.PR_MERGED = "pr.merged"` to `orchestrator/src/orchestrator/state.py`
- [x] Add transitions: PENDING_USER_REVIEW/REVIEW_RUNNING/PR_CI_RUNNING + PR_MERGED → ARCHIVING (done_archive)
- [x] Add `PrMergedBody` model and `pr_merged` endpoint to `orchestrator/src/orchestrator/admin.py`
- [x] Add GHA workflow `.github/workflows/sisyphus-pr-merged-hook.yml`
- [x] Write unit tests `orchestrator/tests/test_admin_pr_merged.py` (PMH-S1 to PMH-S10)

## Stage: PR
- [x] git push feat/REQ-pr-merge-archive-hook-1777344443
- [x] gh pr create --label sisyphus
