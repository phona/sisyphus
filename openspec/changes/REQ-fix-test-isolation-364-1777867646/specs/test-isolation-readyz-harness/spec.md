# test-isolation-readyz-harness

## ADDED Requirements

### Requirement: _readyz_harness SHALL restore get_controller cleanly so it does not leak a mock to subsequent tests

`tests/test_contract_readyz_namespaced_challenger.py` 里的 `_readyz_harness`
contextmanager 在 `raise_runtime_on_get_controller=True` 路径下 SHALL 只对
`orchestrator.k8s_runner.get_controller` 这一个 attribute 应用一个 patch；
当 contextmanager 退出时 `orchestrator.k8s_runner.get_controller` MUST 恢复
为原始未被 patch 的实现，使后续 test 调用 `k8s_runner.set_controller(fake)`
+ `k8s_runner.get_controller()` 时 MUST 拿到 fake controller 本身、而不是
被 leak 的 MagicMock。

实现 MUST NOT 在同一个 attribute 上叠多个 patch（例如同时 patch
`k8s_runner.get_controller` 和 `orchestrator.main.k8s_runner.get_controller`），
因为这两条路径解到同一模块对象，叠 patch 后 stop 顺序错时会把后启动 patch 的
"原值"还原成前一 patch 替换后的 mock，造成 mock 永久泄漏到下游全部 test。
若 harness 出于防御性必须叠多个 patch，stop MUST 按反向顺序（LIFO）执行，
保证最先启动的 patch 最后还原。

#### Scenario: TIRH-S1 deterministic-order full suite passes after harness runs

- **GIVEN** `orchestrator/tests/test_contract_readyz_namespaced_challenger.py::test_RZN_S3_controller_not_initialized_skipped_returns_200` 被运行
- **AND** 紧随其后的同一 pytest session 跑 `tests/test_runner_gc.py::test_active_includes_inflight`
- **WHEN** test_active_includes_inflight 的 `mock_controller` fixture 调
  `k8s_runner.set_controller(fake)`，再 `await runner_gc.gc_once()`
- **THEN** `runner_gc.gc_once` MUST 解析到 fake controller（而非 leak 的 MagicMock）
- **AND** `fake.gc_orphan_pods.await_args` MUST NOT be None
- **AND** test MUST pass，断言 `pod_keep == {"REQ-1","REQ-2","REQ-3"}` 通过

#### Scenario: TIRH-S2 RZN-S3 contract assertion still passes

- **GIVEN** `_readyz_harness(controller=None, raise_runtime_on_get_controller=True)` 被使用
- **WHEN** harness body 内调 `_client().get("/readyz")`
- **THEN** `k8s_runner.get_controller()` MUST 在 harness body 内抛
  `RuntimeError("not init")`
- **AND** `/readyz` 响应 MUST 是 HTTP 200，body `{"status": "ok"}`
- **AND** RZN-S3 黑盒断言（status code 200 + body shape + `failed` 不含 "k8s"）
  MUST 通过

#### Scenario: TIRH-S3 harness exit restores original get_controller

- **GIVEN** 进入 `_readyz_harness` 之前 `orchestrator.k8s_runner.get_controller`
  是 sisyphus 源码定义的真实 function（非 mock）
- **WHEN** `_readyz_harness(controller=None, raise_runtime_on_get_controller=True)`
  context 退出（无论 body 是否抛异常）
- **THEN** `orchestrator.k8s_runner.get_controller` MUST 重新等于原始真实 function
- **AND** 调用 `k8s_runner.get_controller()` 在 `_controller is None` 时
  MUST 抛 `RuntimeError`（来自源码原版 `get_controller`），错误消息以
  `"RunnerController 未初始化"` 开头，**不是** `"not init"`（leak mock 的特征字符串）
