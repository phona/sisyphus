# Tasks: REQ-clone-and-pr-ci-fallback-1777115925

## Stage: spec

- [x] `openspec/changes/REQ-clone-and-pr-ci-fallback-1777115925/proposal.md`
- [x] `openspec/changes/REQ-clone-and-pr-ci-fallback-1777115925/tasks.md`
- [x] `openspec/changes/REQ-clone-and-pr-ci-fallback-1777115925/specs/server-side-clone-and-no-env-fallback/spec.md`
- [x] `openspec/changes/REQ-clone-and-pr-ci-fallback-1777115925/specs/server-side-clone-and-no-env-fallback/contract.spec.yaml`

## Stage: implementation

- [x] `orchestrator/src/orchestrator/actions/_clone.py` (new)：
  共用 helper `clone_involved_repos_into_runner(req_id, ctx)`：
  解析 ctx 拿 involved_repos，调 `k8s_runner.exec_in_runner` 跑
  `/opt/sisyphus/scripts/sisyphus-clone-repos.sh`；返回 `(cloned_repos, ok)`
  以便 caller 决定 emit。

- [x] `orchestrator/src/orchestrator/actions/start_analyze.py`：
  在 `ensure_runner` 之后、`bkd.follow_up_issue` 之前，调用
  `clone_involved_repos_into_runner`；clone 失败 → emit `VERIFY_ESCALATE`；
  没 involved_repos → 跳过 clone 但继续 dispatch（直接路径兼容）。

- [x] `orchestrator/src/orchestrator/actions/start_analyze_with_finalized_intent.py`：
  同样接入 server-side clone（这条路径必然有 `intake_finalized_intent`，
  clone 失败 → `VERIFY_ESCALATE`）。

- [x] `orchestrator/src/orchestrator/checkers/pr_ci_watch.py::watch_pr_ci`：
  删 `SISYPHUS_BUSINESS_REPO` env fallback（line 86）；空 repos 直接
  `raise ValueError("no repos provided ...")`。更新 module docstring 第 4
  行 + watch_pr_ci docstring 第 84 行去掉 env 提及。

- [x] `orchestrator/src/orchestrator/actions/create_pr_ci_watch.py`：
  更新 module docstring（第 8-12 行）：repo 来源序列只剩两档
  （runner 文件系统 discovery > intake `involved_repos`），删第 3 档
  env fallback 描述。

- [x] `orchestrator/src/orchestrator/prompts/analyze.md.j2`：
  Part A.3 (clone) 从"agent 必须跑"改成"sisyphus 已替你预 clone；
  `/workspace/source` 是空再自己跑 helper"。提示 agent 优先把
  involved_repos 在 intake 阶段 finalize 到 ctx。

## Stage: tests

- [x] `orchestrator/tests/test_checkers_pr_ci_watch.py`：
  - `patch_pr_lookup` 不再 setenv，签名改回返回 repo 名给调用方做参数
  - 现有 9 个依赖 env 的 case 改用 `repos=["phona/ubox-crosser"]` 参数
  - 新增 `test_watch_pr_ci_raises_value_error_even_if_env_set_when_repos_none`
    验证 env 设了 + repos=None 也直接 ValueError（确保 env fallback 真的删了）

- [x] `orchestrator/tests/test_actions_start_analyze.py` (new)：
  - `test_start_analyze_server_side_clones_when_involved_repos_present`：
    ctx 含 `involved_repos` → exec_in_runner 跑 sisyphus-clone-repos.sh
    + bkd.follow_up_issue 收到 prompt
  - `test_start_analyze_skips_clone_when_no_involved_repos`：
    ctx 空 → 不调 exec_in_runner（直接路径兼容）
  - `test_start_analyze_clone_failure_emits_verify_escalate`：
    exec_in_runner 退码非 0 → return 含 `emit: VERIFY_ESCALATE`
  - `test_start_analyze_with_finalized_intent_clone_uses_involved_repos`：
    ctx.intake_finalized_intent.involved_repos 三仓 → helper 收到三仓参数

- [x] `orchestrator/tests/test_contract_clone_and_pr_ci_fallback.py` (new)：
  契约层兜底：grep 源码确认 `SISYPHUS_BUSINESS_REPO` 在 checker /
  config / actions 里被删干净（防回归）。

## Stage: PR

- [x] git push feat/REQ-clone-and-pr-ci-fallback-1777115925
- [x] gh pr create
