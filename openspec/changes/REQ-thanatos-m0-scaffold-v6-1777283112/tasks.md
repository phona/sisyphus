# tasks — REQ-thanatos-m0-scaffold-v6-1777283112

## Stage: contract / spec
- [x] 在仓库根写 `docs/thanatos.md`（设计权威，v3 intake 锁定的全文）
- [x] author `specs/thanatos/contract.spec.yaml`（MCP 接口 schema：run_scenario / run_all / recall）
- [x] author `specs/thanatos/spec.md`（4 Requirement + 7 Scenario [THAN-S1..THAN-S7]，delta 格式）
- [x] proposal.md（动机 + 方案 + 取舍 + 影响范围）
- [x] design.md（关键决策记录 + tradeoff）

## Stage: implementation — thanatos 模块
- [x] `thanatos/pyproject.toml`（python>=3.12; mcp>=1.2 + pydantic + pyyaml；dev: pytest + ruff + mypy）
- [x] `thanatos/Dockerfile`（python:3.12-slim + COPY src + entrypoint `python -m thanatos.server`）
- [x] `thanatos/README.md`（M0 scope + build/test 命令）
- [x] `thanatos/src/thanatos/__init__.py` / `__main__.py`（`python -m thanatos` alias）
- [x] `thanatos/src/thanatos/result.py`（StepResult / ScenarioResult / Evidence / KbUpdate dataclasses + to_dict）
- [x] `thanatos/src/thanatos/scenario.py`（**真实 parser**：gherkin code-block + markdown bullet 双格式 + ScenarioFormatError / EmptyScenarioError）
- [x] `thanatos/src/thanatos/skill.py`（**真实 yaml loader**：pydantic Skill model，driver Literal["playwright","adb","http"]，缺 driver / 未知 driver / 缺 entry / 错 yaml 都 raise SkillLoadError）
- [x] `thanatos/src/thanatos/runner.py`（run_scenario / run_all / recall 调度；M0 stub 返回 pass=False + failure_hint）
- [x] `thanatos/src/thanatos/server.py`（MCP stdio server；list_tools 暴露 3 个 tool；call_tool 路由到 runner）
- [x] `thanatos/src/thanatos/drivers/__init__.py`（导出 Driver Protocol + 3 个 driver class）
- [x] `thanatos/src/thanatos/drivers/base.py`（Driver Protocol：preflight / observe / act / assert_ / capture_evidence；PreflightResult / SemanticTree / ActResult / AssertResult / Evidence dataclasses）
- [x] `thanatos/src/thanatos/drivers/playwright.py`（PlaywrightDriver；五方法全 raise NotImplementedError("M0: scaffold only")）
- [x] `thanatos/src/thanatos/drivers/adb.py`（同上 AdbDriver）
- [x] `thanatos/src/thanatos/drivers/http.py`（同上 HttpDriver）
- [x] `thanatos/docs/semantic-contracts/README.md`（总览 + 设计原则）
- [x] `thanatos/docs/semantic-contracts/web.md`（playwright / a11y baseline）
- [x] `thanatos/docs/semantic-contracts/android.md`（adb android native baseline）
- [x] `thanatos/docs/semantic-contracts/flutter.md`（adb flutter baseline）

## Stage: implementation — helm chart
- [x] `deploy/charts/thanatos/Chart.yaml`（apiVersion v2, version 0.0.1, appVersion dev）
- [x] `deploy/charts/thanatos/values.yaml`（driver default playwright / image / redroid 默认社区版）
- [x] `deploy/charts/thanatos/README.md`（driver 选择 + values 字段 + 三种 helm template 检查命令）
- [x] `deploy/charts/thanatos/templates/_helpers.tpl`（labels + assertDriver 守卫）
- [x] `deploy/charts/thanatos/templates/deployment.yaml`（driver-conditional：adb 双容器，其他单容器）
- [x] `deploy/charts/thanatos/templates/service.yaml`（debug ClusterIP）
- [x] `deploy/charts/thanatos/templates/NOTES.txt`（kubectl exec 指令提示）

## Stage: implementation — Makefile
- [x] 顶层 `Makefile` `ci-lint` 加一行 `cd thanatos && uv run ruff check src/ tests/`
- [x] 顶层 `Makefile` `ci-unit-test` 加一行 `cd thanatos && uv run pytest -m "not integration"`
- [x] 顶层 `Makefile` `ci-integration-test` 加一行 `cd thanatos && uv run pytest -m integration`（exit 5 视为 pass）

## Stage: tests
- [x] `thanatos/tests/test_scenario_parser.py`（≥10 case：gherkin / bullet / 多 G-W-T / 大小写 / 链 And-But / mixed reject / 空块 reject / 重复 id reject / fenced code block 不识别 / unicode）
- [x] `thanatos/tests/test_skill_loader.py`（合法 yaml + 缺 driver + 未知 driver + 缺 entry 各 1 case）

## Stage: PR
- [x] `git push origin feat/REQ-thanatos-m0-scaffold-v6-1777283112`
- [x] `gh pr create --label sisyphus`（含 `<!-- sisyphus:cross-link -->` footer）
- [x] BKD intent issue PATCH tags + statusId=review
