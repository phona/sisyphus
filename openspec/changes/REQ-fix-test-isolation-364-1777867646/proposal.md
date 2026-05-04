# Proposal: 修 orch test suite isolation pollution（31 fail in deterministic order）

## Problem

`cd orchestrator && uv run pytest -m "not integration"` 全 suite 跑稳定 31 fail，
但任何子集（单文件 / 几文件）跑都 pass：

```
$ uv run pytest tests/test_runner_gc.py
13 passed in 1.08s

$ uv run pytest -m "not integration"
31 failed, 2234 passed
```

→ 经典 test isolation pollution：某个早跑的 test 改了 module-level 状态，没还原，
后跑的 test 拿到污染状态崩。

### 影响

- CI `lint-test` job 永远红 → PR review 不能信 CI 状态
- 每个 sisyphus self-dogfood PR 都看到这 31 fail，agent / 人都得分辨"我这 PR
  真破了 test 还是只是继承 pre-existing"
- verifier-agent 看 staging-test 红绿做 fix/escalate 决策时被误导
- 实证 5/3：PR #363（fix-362）触发 10+ test fail → 我 + agent 都误判 regression
  → 浪费时间循环讨论。最后跑 main full suite 也 31 fail 才确认 pre-existing

### 根因（bisect 出来）

唯一的 polluter 是 `tests/test_contract_readyz_namespaced_challenger.py` 里的
`_readyz_harness` contextmanager。RZN-S3 路径同时叠了两个 patch 在同一目标：

```python
patches.append(patch.object(k8s_runner, "get_controller", side_effect=...))
patches.append(patch("orchestrator.main.k8s_runner.get_controller", side_effect=...))
```

`orchestrator.main.k8s_runner` 通过 `from . import k8s_runner` 共享同一模块对象，
两个 patch 指向同一 attribute。问题在 stop 顺序：

1. patch1.start() 把 `k8s_runner.get_controller` 从 real_fn 换成 MagicMock₁
2. patch2.start() 时"原值"读到的是 MagicMock₁（patch1 已替换），保存它
3. finally 按 forward 顺序 stop：patch1.stop() → 还原成 real_fn ✓
4. patch2.stop() → 还原成 patch2 保存的"原值" = MagicMock₁ ✗

结果：测试结束后 `k8s_runner.get_controller` 永远停在 MagicMock₁，下游所有
依赖它的 test 都拿到一个不抛 RuntimeError 也不返真 controller 的 mock，
于是 31 个 test 集中崩在 runner_gc / engine cleanup / intent sync 链路。

## Solution

最小改动修 polluter：

1. **删冗余 patch**：`patch.object(k8s_runner, "get_controller", ...)` 已经把目标
   改了；`patch("orchestrator.main.k8s_runner.get_controller", ...)` 是多余的，
   因为两条路径都解到同一个模块对象。
2. **stop 反序**：嵌套 patch 用 LIFO 顺序还原，防御性写法（万一以后又叠多个 patch）。

用 `--randomly-seed=N` 多 seed 跑 suite 还会暴露其他 latent isolation bug
（与 #364 报告无关、深度更深的几处 module-level state leak）。**不在本 REQ
scope 内**：本 REQ 只修 #364 报告的 deterministic-order 31 fail，让 main 的
`make ci-unit-test` 重新可信；剩余 latent bug 单独立 follow-up issue 跟踪。

## Why not 加 random-order 守

#364 的"修法"列里写"CI 跑 `pytest --random-order` 让 isolation bug 早暴露"。
本 REQ **加了 `pytest-randomly` 作为 dev dep**（需要时 `--randomly-seed=N`
本地复现），但**不在 CI 默认开**——多 seed 跑会暴露与本 REQ 无关的额外
isolation bug，强行打开会让 CI 红的更花哨，违反"fix one thing per REQ"。

启用 random-order-default 留给 follow-up REQ，先把那批 latent bug 收掉再开。

## Scope

- `orchestrator/tests/test_contract_readyz_namespaced_challenger.py`
  —— `_readyz_harness` 删冗余 patch + stop 反序
- `orchestrator/pyproject.toml` —— 加 `pytest-randomly>=3.15` 到 dev deps
  （`[project.optional-dependencies].dev` + `[dependency-groups].dev`）

## Out of scope

- random-order 默认开 / CI 改 `make ci-unit-test` 命令
- 修其他 seed 下才出的 isolation bug（test_contract_escalate_pr_merged_override
  / test_contract_gh_incident_per_repo / test_actions_smoke.test_teardown_*
  在 seed=1 下 fail —— 单独 issue）
- conftest.py 加 autouse 'leak guard'（防御性 fixture，未来 REQ 考虑）
- 删 `_readyz_harness` 本身（contract test 形态保留）
