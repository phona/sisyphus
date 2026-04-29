## Stage: spec
- [x] author proposal.md
- [x] author specs/fixer-prompts/spec.md scenarios

## Stage: implementation
- [x] create `orchestrator/src/orchestrator/prompts/verifier-fix-dev.md.j2`
- [x] create `orchestrator/src/orchestrator/prompts/verifier-fix-spec.md.j2`
- [x] modify `orchestrator/src/orchestrator/actions/_verifier.py` start_fixer routing
- [x] modify `orchestrator/src/orchestrator/webhook.py` target_repo passthrough
- [x] update `docs/prompts.md` index
- [x] add unit tests in `test_verifier.py`

## Stage: PR
- [ ] git push feat/REQ-dedicated-fixer-prompts-1777420810
- [ ] gh pr create --label sisyphus
