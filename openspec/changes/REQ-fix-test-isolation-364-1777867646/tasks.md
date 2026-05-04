# Tasks — REQ-fix-test-isolation-364-1777867646

## Stage: spec
- [x] author proposal.md（problem / root cause / scope）
- [x] author specs/test-isolation-readyz-harness/spec.md（ADDED Requirement +
      scenario for "harness MUST not leak get_controller mock"）

## Stage: implementation
- [x] orchestrator/tests/test_contract_readyz_namespaced_challenger.py
      `_readyz_harness`：删 `patch("orchestrator.main.k8s_runner.get_controller", ...)`
      冗余项，保留 `patch.object(k8s_runner, "get_controller", ...)` 单 patch
- [x] orchestrator/tests/test_contract_readyz_namespaced_challenger.py
      `_readyz_harness`：finally 块 stop 顺序改 LIFO（`reversed(patches)`）
- [x] orchestrator/pyproject.toml：加 `pytest-randomly>=3.15`
      到 `[project.optional-dependencies].dev` + `[dependency-groups].dev`

## Stage: verification
- [x] `uv run pytest -m "not integration" -p no:randomly` 全 suite 通过
      （deterministic order，2265 passed / 1 skipped / 16 deselected）
- [x] RZN-S1/S2/S3 三条 challenger contract 仍通过（黑盒断言不动）
- [x] `--randomly-seed=N` 复现能力：dev 本地按需开 random-order 找 latent bug

## Stage: PR（推之前必须全绿）
- [x] git push feat/REQ-fix-test-isolation-364-1777867646
- [ ] `make ci-lint` → 全绿（仅 lint 改动文件）
- [ ] `make ci-unit-test` → 全绿（deterministic order）
- [ ] `make ci-integration-test` → pass / 0-collect-skip
- [ ] gh pr create + sisyphus label + cross-link footer

## Out of scope（follow-up issue tracking）
- [ ] 修 seed=1 下 fail 的 12 个 test：
  - test_contract_escalate_pr_merged_override.py（4）
  - test_contract_escalate_pr_merged_override_challenger.py（4）
  - test_contract_gh_incident_per_repo.py（3）
  - test_actions_smoke.py::test_teardown_skipped_when_accept_skipped（1）
- [ ] CI 默认开 `pytest-randomly`（放在所有 latent leak 修完之后）
- [ ] tests/conftest.py 加 autouse 'leak guard'（断言 module-level 状态在
      test 后还是初始值），降低未来 isolation bug 复现成本
