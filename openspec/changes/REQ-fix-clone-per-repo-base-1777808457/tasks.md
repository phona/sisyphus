# tasks: REQ-fix-clone-per-repo-base-1777808457

## Stage: spec

- [x] add openspec/changes/REQ-fix-clone-per-repo-base-1777808457/proposal.md
- [x] add ADDED Requirement to specs/server-side-clone-and-no-env-fallback/spec.md
  with 2 scenarios (CBOR-S1 owner/repo key normalized; CBOR-S2 basename
  form still works after fix)

## Stage: implementation

- [x] `scripts/sisyphus-clone-repos.sh` —— `--base-for KEY VAL` 在写入
  `REPO_BASE_MAP` 时归一 KEY 到 basename
- [x] `orchestrator/src/orchestrator/router.py` —— 新增
  `normalize_base_overrides(d) -> dict[str, str]`
- [x] `orchestrator/src/orchestrator/actions/start_analyze.py` —— 合并完
  `settings.default_base_branches` 之后调一次 `normalize_base_overrides`
- [x] `orchestrator/src/orchestrator/actions/start_analyze_with_finalized_intent.py`
  —— 同上

## Stage: tests

- [x] `scripts/test_clone_repos.sh` —— 新增 case：
  `--base-for owner/repo branch` 触发的 validating diagnostic 用归一后
  的 branch；basename 形式不变
- [x] `orchestrator/tests/test_router.py` —— 新增 `test_normalize_base_overrides`
  覆盖 owner/repo / basename / .git / 空 / 同 basename 多 owner 冲突场景
- [x] `make ci-lint` 全绿
- [x] `make ci-unit-test` 全绿（2127 + 44 passed）
- [x] `make ci-integration-test` 全绿（无 PG → exit 5 视 pass）
- [x] `bash scripts/test_clone_repos.sh` 全绿（PASS=7 FAIL=0）

## Stage: PR

- [x] git push origin feat/REQ-fix-clone-per-repo-base-1777808457
- [x] gh pr create --label sisyphus，PR body 末尾贴 sisyphus:cross-link footer
