# Tasks for REQ-test-accept-phase-1777267654

## Stage: contract / spec

- [x] author `proposal.md` —— 描述 7 条 accept-phase transition + 不做清单
- [x] author `specs/accept-phase-tests/spec.md` ADDED delta：
      列出 APT-S1..APT-S7 全部 GIVEN/WHEN/THEN

## Stage: implementation

- [x] 新增 `orchestrator/tests/test_engine_accept_phase.py`：
  - 复用 `test_engine.py` 已有的 `FakePool` / `FakeReq` / `_drain_tasks`
    （直接 import 它们的私有定义，避免抄一份让两边漂移；与
    `test_engine_adversarial.py` 同一套接入方式）
  - 7 条 case 一一对应 APT-S1..APT-S7 scenario
  - 所有 case 用 `pytest.mark.asyncio`，不依赖真 DB / BKD / K8s
- [x] APT-S1：(ACCEPT_RUNNING, ACCEPT_PASS) + 链式 emit teardown-done.pass → ARCHIVING
- [x] APT-S2：(ACCEPT_RUNNING, ACCEPT_FAIL) + 链式 emit teardown-done.fail → REVIEW_RUNNING
- [x] APT-S3：(ACCEPT_RUNNING, ACCEPT_ENV_UP_FAIL) → ESCALATED + cleanup_runner(retain_pvc=True)
- [x] APT-S4：(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_PASS) → ARCHIVING（无 cleanup）
- [x] APT-S5：(ACCEPT_TEARING_DOWN, TEARDOWN_DONE_FAIL) → REVIEW_RUNNING（无 cleanup）
- [x] APT-S6：(ACCEPT_RUNNING, SESSION_FAILED) self-loop（无 cleanup）
- [x] APT-S7：(ACCEPT_TEARING_DOWN, SESSION_FAILED) self-loop（无 cleanup）

## Stage: PR

- [x] git push feat/REQ-test-accept-phase-1777267654
- [x] gh pr create --label sisyphus（PR body 写动机 + 测试方案）
