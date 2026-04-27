# REQ-thanatos-m0-scaffold-v6-1777283112: feat(thanatos): M0 module scaffold per docs/thanatos.md

## 问题

sisyphus 的 accept stage 当前没有验收能力层 —— accept-agent 只能跑 prompt，
没有结构化 "读 spec → 操作真实环境 → 判 pass/fail → 沉淀产品知识" 的工具链。
[docs/thanatos.md](../../../docs/thanatos.md) 已定义这一层（thanatos：MCP stdio
server + scenario parser + driver Protocol + helm chart），需要先把骨架进仓让
后续 milestones（M1 接 accept stage 调用链、M2 driver 真实实现、…）有承载点。

v1..v5 起伏：
- **v1** (`REQ-thanatos-m0-scaffold-1777219498`)：plan 锁了 25 文件，但 analyze 漏推
  feat 分支 + `docs/thanatos.md` 没入仓，spec_lint vacuous-pass 然后 escalate。
- **v2-v5**：各种 dispatch / state-machine 角度的重试，没有产出。

v6 = 直接吃 v3 intake 已 finalize 的 design（24 新增文件 + 顶层 Makefile 改动），
**全责交付** —— spec + code + PR 一站到位，feat 分支真 push、PR 真开、
`docs/thanatos.md` 真入仓。

## 方案

### M0 范围（24 新增文件 + 1 既有 Makefile 改动）

```
sisyphus/
├── docs/thanatos.md                                # 设计权威同步入仓
├── thanatos/                                       # 新模块
│   ├── README.md / pyproject.toml / Dockerfile
│   ├── src/thanatos/
│   │   ├── __init__.py / __main__.py
│   │   ├── server.py          MCP stdio server，注册 3 tool（全 stub）
│   │   ├── runner.py          run_scenario / run_all 调度
│   │   ├── scenario.py        ⭐ 真实 parser（gherkin + bullet 两种格式）
│   │   ├── skill.py           ⭐ 真实 yaml loader（pydantic 校验）
│   │   ├── result.py          dataclasses
│   │   └── drivers/
│   │       ├── base.py        Driver Protocol（5 方法 async）
│   │       ├── playwright.py  全 NotImplementedError("M0: scaffold only")
│   │       ├── adb.py         同上
│   │       └── http.py        同上
│   ├── docs/semantic-contracts/  README + web/android/flutter 三份契约
│   └── tests/
│       ├── test_scenario_parser.py   ≥10 case
│       └── test_skill_loader.py      合法 + 缺 driver / 未知 driver / 缺 entry
├── deploy/charts/thanatos/                          # helm chart
│   ├── Chart.yaml / values.yaml / README.md
│   └── templates/_helpers.tpl / deployment.yaml / service.yaml / NOTES.txt
└── openspec/changes/REQ-thanatos-m0-scaffold-v6-1777283112/   # 本 change
```

顶层 `Makefile` 既有的 `ci-lint` / `ci-unit-test` / `ci-integration-test` 三条
target 各加一行 `cd thanatos && uv run ...`，把 thanatos 的 lint / 单测 / 集成测
跟 orchestrator 一起串进 sisyphus 自己的 `staging_test` / `dev_cross_check` 检查。

### MCP 接口契约（`server.py` + `specs/thanatos/contract.spec.yaml`）

```python
run_scenario(skill_path, spec_path, scenario_id, endpoint) -> ScenarioResult
run_all(skill_path, spec_path, endpoint)                   -> list[ScenarioResult]
recall(skill_path, intent)                                 -> list[dict]
```

M0 三个 tool **全部 stub**：`run_scenario` / `run_all` 解析参数 → load skill →
parse spec → 选 driver class，但**不**调任何 driver 方法，直接返回
`pass=False, failure_hint="M0: thanatos scaffold only, drivers not implemented"`。
`recall` 永远返回 `[]`。

### scenario parser（`scenario.py`）= M0 唯一真业务码

支持两种 `#### Scenario:` 块：
- gherkin code block：` ```gherkin Given/When/Then ``` `
- markdown bullet：`- **GIVEN** ...`

输出统一 `ParsedScenario(scenario_id, description, given, when, then, source_format)`。
错误情况：mixed 格式 / 重复 id / 空块都 raise（`ScenarioFormatError` /
`EmptyScenarioError`）。`#### Scenario:` 出现在 fenced code block 内会被忽略（不
当真 scenario 识别）。

### Driver Protocol（`drivers/base.py`）

```python
class Driver(Protocol):
    name: str
    async def preflight(self, endpoint: str) -> PreflightResult
    async def observe(self) -> SemanticTree
    async def act(self, step: str) -> ActResult
    async def assert_(self, step: str) -> AssertResult
    async def capture_evidence(self) -> Evidence
```

M0 三个 driver class（`PlaywrightDriver` / `AdbDriver` / `HttpDriver`）五方法
**全部** `raise NotImplementedError("M0: scaffold only")`。Protocol 形状是 M0 冻
结的契约，方法体在 M1 才填。

### helm chart（`deploy/charts/thanatos/`）

`.Values.driver` toggle 三种拓扑：
- `playwright` / `http` → 单容器 Pod（仅 thanatos）
- `adb` → 双容器 Pod（redroid sidecar privileged + thanatos 连 localhost:5555）
- 其他值 → `helm template` 直接 `fail "thanatos.driver must be ..."`

不发 OCI、不动 `runner/Dockerfile`、不在业务仓 `accept-env-up` 调 helm install
（M1+）。

## 取舍

- **`involved_repos: []`**：v3 intake 拍的 —— 跟 helm `default_involved_repos:
  [phona/sisyphus]` 走 L4 兜底，单仓 dogfood 不重复声明。
- **顶层 Makefile 串 thanatos 而非自带 ci-* target**：`ci-*` 是
  [docs/integration-contracts.md](../../../docs/integration-contracts.md) 给独立
  source repo 的契约，thanatos 是 sisyphus 同仓子模块。让顶层 Makefile 多调一行
  `cd thanatos && uv run ...` 比新建 `thanatos/Makefile` 让 staging_test 看不见
  正确得多。
- **driver 全 NotImplementedError 而非空函数体**：明示"M0 scaffold only"，M1 跑
  到这里如果忘补会立刻 fail，不会 silent-pass。
- **scenario parser 写真而非 stub**：openspec spec.md 进 spec_lint
  ([scripts/check-scenario-refs.sh](../../../scripts/check-scenario-refs.sh)) 已
  经验证 scenario id 引用一致性；parser 真要在 M1+ 给 driver 喂 GIVEN/WHEN/THEN
  数据，提前测够省 M1 debug 时间。

## 影响范围

- `docs/thanatos.md` —— 新增（设计权威）
- `thanatos/` —— 新增模块（16 src + 4 docs + 2 tests + Dockerfile + pyproject + README）
- `deploy/charts/thanatos/` —— 新增 helm chart（4 templates + values + Chart + README）
- `openspec/changes/REQ-thanatos-m0-scaffold-v6-1777283112/` —— 本 change
- `Makefile`（顶层）—— `ci-lint` / `ci-unit-test` / `ci-integration-test` 各扩一行

**不**改：accept.md.j2 / state machine / actions / checkers / runner Dockerfile /
业务仓 accept-env-up Makefile / `.github/workflows/`。这些都是 M1+ 范围。
