# REQ: thanatos M1 — 统一 AI 验收收口落地

## 背景

thanatos 是 sisyphus 的**统一 AI 验收收口层**。业务 repo 只负责在 openspec 里写验收用例（`#### Scenario:` block），thanatos 负责解析、执行、报告。不是每个业务 repo 自己搞一套验收引擎。

当前 thanatos 是 M0 scaffold —— 所有 driver 方法抛 `NotImplementedError`，runner 只解析 spec 不执行。create_accept.py 被改成了 v0.3-lite（make target 方案），偏离了 thanatos MCP 的设计方向。

## 目标

让 thanatos 从 scaffold 变成**可执行的统一验收层**，恢复 thanatos MCP 在 sisyphus 流水线中的位置。

## 任务清单

### 任务 1: HTTP driver 实现

文件: `thanatos/src/thanatos/drivers/http.py`

实现 5 方法 async 契约（drivers/base.py 定义）:

- `preflight(endpoint)` — curl -sf `{endpoint}/healthz`，返回 PreflightResult
- `observe()` — 返回前一次 HTTP 响应的 body + headers 作为 SemanticTree(kind="http")
- `act(step)` — 解析 step 文本中的 curl 命令语义，执行 HTTP 请求
  - step 格式: `When POST /api/v1/order with body {"foo":"bar"}`
  - 或: `When GET /api/v1/order/{id}`
  - 返回 ActResult(ok=True/False)
- `assert_(step)` — 解析 Then 断言
  - step 格式: `Then response code is 200`
  - 或: `Then response.body.order_id > 0`
  - 用 JSONPath 或简单点号路径解析
  - 返回 AssertResult(ok=True/False)
- `capture_evidence()` — 返回最后一次 HTTP 的 request/response 作为 Evidence.network

依赖: httpx (async http client)。thanatos/pyproject.toml 已存在，检查并加依赖。

### 任务 2: Playwright driver 实现

文件: `thanatos/src/thanatos/drivers/playwright.py`

实现 5 方法 async 契约:

- `preflight(endpoint)` — 启动 browser，navigate 到 endpoint，等 page load
- `observe()` — 调用 page.accessibility.snapshot() 返回 SemanticTree(kind="a11y")
- `act(step)` — 解析 step 文本中的 UI 操作
  - 格式: `When click "Submit button"` / `When type "email@example.com" into "Email field"`
  - 用 a11y snapshot 中的 name 属性定位元素
  - 返回 ActResult
- `assert_(step)` — 解析 Then 断言
  - 格式: `Then page title contains "Dashboard"` / `Then element "Success message" is visible`
  - 返回 AssertResult
- `capture_evidence()` — page.screenshot() 返回 base64 PNG 作为 Evidence.screenshot，page.content() 作为 Evidence.dom

依赖: playwright-python。加到 thanatos/pyproject.toml。

### 任务 3: runner.py 补全 execute flow

文件: `thanatos/src/thanatos/runner.py`

当前 M0: 解析 spec 后只 `_pick_driver` 就返回 `passed=False` stub。

M1 需要真正执行:
```
load_skill → parse_spec → _pick_driver → driver.preflight(endpoint)
for each scenario:
  for given_step in scenario.given: driver.act(given_step)
  for when_step in scenario.when: driver.act(when_step)
  for then_step in scenario.then:
    result = driver.assert_(then_step)
    if not result.ok: capture_evidence() → 记录失败 → break
  if all then passed: ScenarioResult(passed=True)
  else: ScenarioResult(passed=False, failure_hint=..., steps=[...])
```

注意: runner 的 `run_scenario` 和 `run_all` 当前是 sync 函数，但 driver 方法是 async 的。需要改成 async。

### 任务 4: create_accept.py 恢复 thanatos MCP

文件: `orchestrator/src/orchestrator/actions/create_accept.py`

当前是 v0.3-lite（make accept-env-up → sleep → make accept-smoke → make accept-env-down）。

改回 thanatos MCP 方案（参考 git commit c26c670）:
1. env-up 逻辑不变: 跑 `make accept-env-up` 拿 endpoint JSON
2. 解析 `thanatos` block（pod/namespace/skill_repo）
3. 派 accept-agent BKD issue，注入 thanatos 参数
4. **不**再跑 make accept-smoke

如果 `thanatos` block 在 env-up JSON 中不存在 → fallback 到当前行为（但这种情况应该逐渐消灭）。

### 任务 5: accept.md.j2 恢复 thanatos MCP 调用

文件: `orchestrator/src/orchestrator/prompts/accept.md.j2`

当前 prompt 让 agent 手动 kubectl exec 跑 curl。

改回 thanatos MCP 调用:
```
## 验收 (ACCEPT / AI-QA) — thanatos M1
...
### Step 3: 通过 thanatos MCP 跑 scenario

thanatos MCP server 在 runner pod 中以 stdio 方式运行:
```bash
kubectl -n sisyphus-runners exec runner-{{ req_id | lower }} -- \
  python -m thanatos.server
```

调用 `run_all` tool:
- skill_path: /workspace/source/<repo>/.thanatos/skill.yaml
- spec_path: /workspace/source/<repo>/openspec/changes/{{ req_id }}/specs/*/spec.md
- endpoint: {{ endpoint }}

thanatos 会返回每个 scenario 的 pass/fail + evidence。你只需:
1. 确认 thanatos server 能启动
2. 调用 run_all 或逐个调用 run_scenario
3. 收集结果，写报告
```

### 任务 6: 恢复 thanatos CI

- 恢复 `.github/workflows/thanatos-ci.yml`（被 cleanup 删了，需要重建）
- Makefile 中 thanatos 的 ci-lint/ci-unit-test 段（被 cleanup 改了，需要恢复）
- thanatos 自己的 unit test: 为 HTTP driver 写测试（mock httpx）

## 验收标准

1. `cd thanatos && uv run pytest` — 所有 thanatos 测试通过
2. `cd thanatos && python -m thanatos.server` — MCP server 能启动，run_scenario 对 HTTP spec 返回真正的 pass/fail
3. `make ci-lint` / `make ci-unit-test` — sisyphus 顶层全部通过
4. create_accept.py 能正确派 accept-agent，agent prompt 中包含 thanatos MCP 调用指引

## 约束

- **不删 thanatos 模块** — 与 REQ-thanatos-cleanup-v2 方向相反，这个 REQ 是做加法不是减法
- **driver 实现优先 HTTP**，Playwright 次之，ADB 最后
- **保持 M0 的接口契约** — drivers/base.py 的 Protocol 不变，runner.py 的输入输出不变
- **技能文件格式不变** — .thanatos/skill.yaml 格式已在 skill.py 中定义
- **scenario 解析器不变** — scenario.py 已经能正确解析，不需要改
