# tasks: REQ-orch-rate-limit-1777202974

## Stage: contract / spec

- [x] author `specs/orch-rate-limit/spec.md` with delta `## ADDED Requirements`
- [x] write 6 scenarios `ORCH-RATE-S{1..6}` covering cap-disabled,
      under-cap admit, at-cap reject, disk under threshold admit, disk
      over threshold reject, controller-missing dev fallback

## Stage: implementation

- [x] `orchestrator/src/orchestrator/admission.py`: new module with
      `AdmissionDecision` dataclass and `check_admission(pool, *, req_id)`
- [x] `orchestrator/src/orchestrator/config.py`: add
      `inflight_req_cap` (default 10) and
      `admission_disk_pressure_threshold` (default 0.75)
- [x] `orchestrator/src/orchestrator/actions/start_intake.py`: call
      `check_admission` first; on reject patch `ctx.escalated_reason` +
      emit `VERIFY_ESCALATE`
- [x] `orchestrator/src/orchestrator/actions/start_analyze.py`: same
- [x] `orchestrator/tests/test_admission.py`: unit tests for
      `check_admission` covering all 6 scenarios
- [x] `orchestrator/tests/test_intake.py`: integration test that
      `start_intake` escalates when admission denies
- [x] `orchestrator/tests/test_actions_start_analyze.py`: same for
      `start_analyze`

## Stage: PR

- [x] git push `feat/REQ-orch-rate-limit-1777202974`
- [x] gh pr create
