# tasks — REQ-415 (thanatos M1 wire accept stage to MCP)

## Stage: contract / spec
- [x] author `specs/thanatos-mcp-wire/contract.spec.yaml`（env-up JSON 加 `thanatos` block 的 schema）
- [x] author `specs/thanatos-mcp-wire/spec.md`（3 Requirement + 6 Scenario [TMW-S1..TMW-S6]，delta 格式）
- [x] proposal.md（动机 + 方案 + 取舍 + 影响范围）
- [x] design.md（关键决策记录 + tradeoff）

## Stage: implementation — orchestrator
- [x] `orchestrator/src/orchestrator/actions/create_accept.py` —— `accept_env.get("thanatos") or {}` 抽取，三字段平铺进 `prompt ctx`（`thanatos_pod` / `thanatos_namespace` / `thanatos_skill_repo`）
- [x] `orchestrator/src/orchestrator/prompts/accept.md.j2` —— `{% if thanatos_pod %}` 分支跑 MCP run_all + 应用 kb_updates；`{% else %}` 分支保留老 curl 行为
- [x] `docs/integration-contracts.md` §3 —— accept-env-up JSON 字段表加 `thanatos` 块 + sample 示例

## Stage: tests
- [x] `orchestrator/tests/test_create_accept_thanatos.py`
  - [x] env-up JSON 含 `thanatos` block → 三字段透传进 prompt（thanatos_pod 非空）
  - [x] env-up JSON 缺 `thanatos` block → thanatos_pod 为 None / 空字符串（fallback 路径）
  - [x] env-up JSON `thanatos.namespace` 缺省 → 默认顶层 namespace
- [x] `orchestrator/tests/test_prompts_accept_thanatos.py`
  - [x] `thanatos_pod` 设值时模板含 `python -m thanatos.server` + `tools/call` 关键 token
  - [x] `thanatos_pod` 为 None 时模板回到老分支（含 `/workspace/source/*/openspec/changes/.../spec.md` glob）
  - [x] 两个分支都不能掉 `result:pass` / `result:fail` tag 行为
  - [x] 空字符串 thanatos_pod 也走 fallback（template `{% if %}` truthiness）

## Stage: PR
- [ ] `git push origin feat/REQ-415`
- [ ] `gh pr create --label sisyphus`（含 `<!-- sisyphus:cross-link -->` footer）
- [ ] BKD intent issue PATCH tags + statusId=review
