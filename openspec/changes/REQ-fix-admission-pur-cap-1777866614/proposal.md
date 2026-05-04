# REQ-fix-admission-pur-cap-1777866614: fix(admission): inflight cap 排除 pending-user-review

## 问题

`admission.py:42` `_INFLIGHT_EXCLUDE_STATES`：

```python
_INFLIGHT_EXCLUDE_STATES = ("init", "done", "escalated", "gh-incident-open")
```

排除了 `escalated`（注释说"消耗 0 runner pod"），但**没排除
`pending-user-review`** —— PUR 跟 escalated 一样**也不消耗 runner pod**：进 PUR
之前 verifier escalate 路径已经把 runner Pod 拆掉了，REQ 只是在等用户在 BKD
issue 里 follow-up 续推。

## 实证（5/4 凌晨，issue #384）

orch state distribution：8 PUR + 2 challenger-running = 10/10，
新 REQ 被 admission 全部 reject `inflight-cap-exceeded:10/10`。

8 个 PUR 都是 4/29-5/3 的老 REQ，没人理就堆着。verifier escalate→人 resume
路径 by design 把 REQ 推 PUR 等再次确认，但用户没 UX 提醒，每次 resume 都加一个
PUR，永久积累堵 cap。

## 方案

### 把 `pending-user-review` 加进 `_INFLIGHT_EXCLUDE_STATES`

`orchestrator/src/orchestrator/admission.py`:

```python
_INFLIGHT_EXCLUDE_STATES = (
    "init", "done", "escalated", "gh-incident-open", "pending-user-review",
)
```

加一段注释解释 PUR 跟 escalated 同档（runner pod 已拆，等人续推），并指 issue
#384 作为 incident reference。

### 不动 PUR 的 UX 提醒（拆单独 issue）

issue #384 还提到 watchdog 周期 emit "REQ X 已 PUR Y 小时" 的 UX 改进。本 REQ
只做 admission cap fix，UX 改进单立项，不在本 PR 范围。

### 同步更新 spec 和测试

- `openspec/specs/orch-rate-limit` 把 requirement 里的 exclude state 列表加上
  `pending-user-review`，并新增 scenario `ORCH-RATE-S7` 显式覆盖"8 PUR + 2
  running 时新 REQ 仍能通过"。
- `orchestrator/tests/test_admission.py`：
  - 在已有 `test_inflight_count_under_cap_admits` 的 SQL state-list 断言里
    追加 `assert "pending-user-review" in state_list`。
  - 新增 `test_inflight_excludes_pending_user_review`（ORCH-RATE-S7）：
    `_FakePool(count=2)` 模拟 SQL 已排除 8 PUR 后只看到 2 个真 inflight，
    断言 admit=True。

## 影响

- runtime：admission cap 算法只多排除一个状态，cap=0 短路逻辑不变；其他 stage
  的状态机 transition 完全不受影响。
- 操作：堵 cap 的 PUR 老 REQ 立刻不再卡新 REQ；老 PUR 本身仍照常等用户续推。
- 观测：`admission.cap_rejected` warning 频率应明显下降。

## 验证

- `pytest orchestrator/tests/test_admission.py` 全过（含新 ORCH-RATE-S7）。
- `openspec validate openspec/changes/REQ-fix-admission-pur-cap-1777866614` 通过。
- `check-scenario-refs.sh` 通过（ORCH-RATE-S7 引用与定义匹配）。
