# Design — analyze→execute rename

## 改动结构

### 1. 状态机权威源（`state.py`）

`ReqState` / `Event` 枚举的成员名 + 字符串值同步换：

```python
class ReqState(StrEnum):
    EXECUTING = "executing"
    EXECUTE_ARTIFACT_CHECKING = "execute-artifact-checking"
    # ... 其它 state 不动

class Event(StrEnum):
    INTENT_EXECUTE = "intent.execute"
    EXECUTE_DONE = "execute.done"
    EXECUTE_ARTIFACT_CHECK_PASS = "execute-artifact-check.pass"
    EXECUTE_ARTIFACT_CHECK_FAIL = "execute-artifact-check.fail"
    # ... 其它 event 不动
```

`TRANSITIONS` 表 + `_ESCALATED_RESUME_EVENT_SOURCES` + `_PENDING_USER_REVIEW_RESUME_EVENT_SOURCES`
里所有 `ReqState.ANALYZING` / `Event.ANALYZE_*` 同步替换。

### 2. 文件重命名（用 `git mv` 保留 history）

```
actions/start_analyze.py                          → actions/start_execute.py
actions/start_analyze_with_finalized_intent.py    → actions/start_execute_with_finalized_intent.py
actions/create_analyze_artifact_check.py          → actions/create_execute_artifact_check.py
checkers/analyze_artifact_check.py                → checkers/execute_artifact_check.py
prompts/analyze.md.j2                             → prompts/execute.md.j2
prompts/verifier/analyze_success.md.j2            → prompts/verifier/execute_success.md.j2
prompts/verifier/analyze_fail.md.j2               → prompts/verifier/execute_fail.md.j2
prompts/verifier/analyze_artifact_check_success.md.j2 → prompts/verifier/execute_artifact_check_success.md.j2
prompts/verifier/analyze_artifact_check_fail.md.j2    → prompts/verifier/execute_artifact_check_fail.md.j2
tests/test_actions_start_analyze.py               → tests/test_actions_start_execute.py
tests/test_actions_start_analyze_supersede.py     → tests/test_actions_start_execute_supersede.py
tests/test_actions_create_analyze_artifact_check.py → tests/test_actions_create_execute_artifact_check.py
tests/test_checkers_analyze_artifact_check.py     → tests/test_checkers_execute_artifact_check.py
tests/test_contract_analyze_artifact_check.py     → tests/test_contract_execute_artifact_check.py
```

不重命名（保留历史 REQ id 字面）：
- `tests/test_contract_analyze_resume_guard_challenger.py`（refs `REQ-fix-analyze-resume-guard-1777431901` 历史 spec）
- `tests/test_contract_clone_fallback_direct_analyze.py`（refs `REQ-clone-fallback-direct-analyze-1777119520`）
- `openspec/changes/archive/*`（不可变审计）
- `openspec/changes/REQ-fix-analyze-resume-guard-1777431901/` 历史 REQ 自身

### 3. router/webhook 读 path 双识别

`router.derive_event`：
- `intent:analyze` 与 `intent:execute` 都映射 `Event.INTENT_EXECUTE`
- `analyze` 与 `execute` 都映射 `Event.EXECUTE_DONE`（仅在 result 同时缺失才走老 fallback；新写的 stage agent issue 应走 `result:pass/fail` 通道）

`webhook.pass_event_for_stage`：
- key `"analyze"` 与 `"execute"` 都映射 `Event.EXECUTE_DONE`
- key `"analyze_artifact_check"` 与 `"execute_artifact_check"` 都映射 `Event.EXECUTE_ARTIFACT_CHECK_PASS`

`intent_tags.SISYPHUS_MANAGED_EXACT`：加 `"execute"`，保留 `"analyze"`（避免老 issue 上的 hint 被错传）

### 4. action handler 名

`actions/__init__.py` 的 `from .` 导入路径换；`@register("...")` decorator 名换；
`state.py` 里 `Transition.action` 字段同步改：

```
"start_analyze"                       → "start_execute"
"start_analyze_with_finalized_intent" → "start_execute_with_finalized_intent"
"create_analyze_artifact_check"       → "create_execute_artifact_check"
"invoke_verifier_for_analyze_artifact_check_fail" → "invoke_verifier_for_execute_artifact_check_fail"
```

`actions/_verifier.py` 里 verifier prompt 路由（`stage` → `prompts/verifier/<stage>_*.md.j2`
路径生成）的字符串 mapping 同步换。

### 5. observability / engine.STATE_TO_STAGE / AGENT_STAGES

```
ReqState.EXECUTING                  → "execute"
ReqState.EXECUTE_ARTIFACT_CHECKING  → "execute_artifact_check"
AGENT_STAGES                        : {"execute", "verifier", "fixer", "accept"}  # was "analyze"
```

`observability.py` 的 stage 列表（含 valid_stages）同步换。

### 6. snapshot.py orphan 恢复

`_trigger_orphan_intent_analyze` → `_trigger_orphan_intent_execute`；触发条件
`"intent:analyze" not in tags` → 同时识别 `intent:analyze` / `intent:execute`
（read-compat），但仅打 `execute` rebrand tag。

### 7. DB migration `0017_rename_analyze_to_execute.sql`

```sql
-- req_state
UPDATE req_state SET cur_state='executing'                  WHERE cur_state='analyzing';
UPDATE req_state SET cur_state='execute-artifact-checking'  WHERE cur_state='analyze-artifact-checking';

-- event_log（如有 event_name 列存历史值）
UPDATE event_log SET event_name='intent.execute'            WHERE event_name='intent.analyze';
UPDATE event_log SET event_name='execute.done'              WHERE event_name='analyze.done';
UPDATE event_log SET event_name='execute-artifact-check.pass' WHERE event_name='analyze-artifact-check.pass';
UPDATE event_log SET event_name='execute-artifact-check.fail' WHERE event_name='analyze-artifact-check.fail';

-- stage_runs
UPDATE stage_runs SET stage='execute'                       WHERE stage='analyze';
UPDATE stage_runs SET stage='execute_artifact_check'        WHERE stage='analyze_artifact_check';

-- verifier_decisions
UPDATE verifier_decisions SET stage='execute'               WHERE stage='analyze';
UPDATE verifier_decisions SET stage='execute_artifact_check' WHERE stage='analyze_artifact_check';

-- artifact_checks (kind 列)
UPDATE artifact_checks SET kind='execute_artifact_check'    WHERE kind='analyze_artifact_check';
```

回滚镜像 (`*.rollback.sql`) 反向 UPDATE。

### 8. docs / Metabase SQL

机械替换 `analyze*`（state/event/stage 字面）。保留**历史 REQ id**（`REQ-*-analyze-*-NNN`）
跟 archive markdown。

`observability/queries/sisyphus/05-active-req-overview.sql` 注释里的 `analyzing` 状态枚举
样例 → `executing`。

## 不做的事

- 不改 `analyze-agent` 之外的 agent role 命名
- 不动 `intake` / `accept` / `verifier` / `fixer` / `challenger` 等命名
- 不删历史 archive REQ markdown
- 不改 `REQ-*-analyze-*-NNN` 等历史 REQ slug

## 测试策略

- `tests/test_state.py` — enum 成员存在 + transition 表完整性
- `tests/test_router.py` — `intent:execute`/`intent:analyze` 双读 + `execute`/`analyze` 双读
- `tests/test_actions_start_execute.py`（重命名后）— 主入口路径
- `tests/test_engine_main_chain.py` / `test_engine_escalated_resume.py` — full chain 都用新命名
- 新增 `tests/test_router_analyze_compat.py`（小）覆盖兼容读旧 tag

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 漏改某文件 → import error / runtime KeyError | grep `analyze\|ANALYZ` 残余清单在 PR body；test_state + test_engine_main_chain 跑全链 |
| migration 锁 req_state / event_log → 短暂阻塞 webhook | sisyphus 单实例 / dev 流量 / 表 < 几千行；UPDATE 微秒级 |
| 1-2 周内人工新打 `intent:analyze` tag → router 接住 | read-compat 覆盖 `intent:analyze` 老路径，自动转 INTENT_EXECUTE |
| 1-2 周后忘了清理 read-compat | 在 README / CHANGELOG 标 follow-up REQ；router 内 TODO 注释指明 deadline |
