# Tasks: REQ-silence-runner-gc-rbac-1777123726

## Stage: contract / spec

- [x] author `specs/orch-noise-cleanup/contract.spec.yaml` (capability delta —
      log_level for `runner_gc.disk_check_rbac_denied`)
- [x] author `specs/orch-noise-cleanup/spec.md` MODIFIED Requirements / Scenarios
      replacing ORCHN-S4 wording (warning → info)

## Stage: implementation

- [x] `orchestrator/src/orchestrator/runner_gc.py:gc_once`: change
      `log.warning("runner_gc.disk_check_rbac_denied", ...)` to
      `log.info("runner_gc.disk_check_rbac_denied", ...)`; keep the
      `_DISK_CHECK_DISABLED = True` short-circuit unchanged

## Stage: tests

- [x] `orchestrator/tests/test_runner_gc.py::test_disk_check_403_disables_after_first_warn`
      — rename to `..._first_log_disables`, assertion still works (event name
      in stdout)
- [x] `orchestrator/tests/test_contract_orch_noise_cleanup.py::test_orchn_s4_first_403_warns_and_disables`
      — rename to `test_orchn_s4_first_403_logs_info_and_disables`; switch
      `log_level == "warning"` assertion to `log_level == "info"`; update the
      docstring to match the new behavior

## Stage: PR

- [x] `git push origin feat/REQ-silence-runner-gc-rbac-1777123726`
- [x] `gh pr create` with proposal summary + verification plan

owner: analyze-agent
