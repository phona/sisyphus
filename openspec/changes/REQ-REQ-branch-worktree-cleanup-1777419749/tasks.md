## Stage: spec
- [x] author proposal.md
- [x] author specs/branch-worktree-cleanup/spec.md scenarios

## Stage: implementation
- [x] modify `.github/workflows/sisyphus-pr-merged-hook.yml` to delete merged branch
- [x] modify `orchestrator/src/orchestrator/engine.py` `_cleanup_runner_on_terminal` to clean bkd/* worktrees + branches
- [x] add unit tests for git cleanup in `test_engine.py`

## Stage: PR
- [ ] git push feat/REQ-REQ-branch-worktree-cleanup-1777419749
- [ ] gh pr create --label sisyphus
