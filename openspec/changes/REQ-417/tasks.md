# tasks — REQ-417 thanatos M1: wire accept stage to thanatos MCP

## Stage: contract / spec
- [x] author `specs/thanatos-accept-wire/contract.spec.yaml`（CLI subcommand schema + accept_env JSON ext + .thanatos/ opt-in shape）
- [x] author `specs/thanatos-accept-wire/spec.md`（4 Requirement + 6 Scenario [TM1-S1..TM1-S6]，delta 格式）
- [x] proposal.md
- [x] design.md

## Stage: implementation — thanatos CLI
- [x] `thanatos/src/thanatos/cli.py` —— argparse-based dispatcher：run-scenario / run-all / recall 三个 subcommand，stdout JSON / stderr diagnostics / exit 0|2|3
- [x] `thanatos/src/thanatos/__main__.py` —— 0 args 走 server（M0 兼容），N args forward 到 cli.main

## Stage: implementation — accept prompt 改写
- [x] `orchestrator/src/orchestrator/prompts/accept.md.j2`：
  - [x] Step 2.5 新增：检测 `/workspace/source/*/.thanatos/skill.yaml` + `accept_env.thanatos_pod` 决定走 thanatos 还是 curl
  - [x] Step 3a 新增 thanatos branch：`kubectl exec <thanatos_pod> -- python -m thanatos run-scenario ...` per scenario，收集 `kb_updates`
  - [x] Step 3b 新增 kb_updates apply：在 BKD Coder workspace cwd 的 source repo working tree 应用 patch/append 后 commit + push 到 feat/REQ-417
  - [x] Step 3 既有 curl branch 改成 fallback（保留为"没启用 thanatos 时"的路径）
  - [x] vacuous-pass 防御：thanatos branch 0 scenario / 0 kb_updates 都强制 fail

## Stage: implementation — docs
- [x] `docs/integration-contracts.md` 新增 §10 "Thanatos opt-in (.thanatos/ directory contract)"，描述：skill.yaml schema + 启用方式 + endpoint JSON `thanatos_pod` 字段 + curl fallback 兜底
- [x] `docs/cookbook/ttpos-arch-lab-accept-env.md` 新增 §6.5 "helm install thanatos in accept-env-up"，提供完整 Makefile 片段：`helm upgrade --install thanatos $(SISYPHUS_THANATOS_CHART)` + `kubectl get pod -l app.kubernetes.io/name=thanatos` 取 pod name 注入 endpoint JSON
- [x] `docs/thanatos.md` §3 namespace 写法对齐到 `accept-<req-id>`（小修一行）

## Stage: tests
- [x] `thanatos/tests/test_cli.py`：
  - [x] argparse 拒绝缺 required arg（exit 2）
  - [x] run-scenario stdout 一行 JSON + 含 `pass`/`scenario_id`/`failure_hint` 字段
  - [x] run-all stdout JSON array
  - [x] recall stdout JSON `[]`
  - [x] runner 抛异常时 exit 3 + stderr 含异常类型
- [x] `orchestrator/tests/test_prompts_accept_thanatos.py`：
  - [x] render `accept.md.j2`（含 / 不含 `accept_env.thanatos_pod`）→ 渲染结果含或不含 thanatos branch 关键串
  - [x] kb_updates commit 段含 `git push origin feat/`
  - [x] curl fallback 段在所有渲染下都存在（保 backward compat）

## Stage: PR
- [x] `git push origin feat/REQ-417`
- [x] `gh pr create --label sisyphus`（含 `<!-- sisyphus:cross-link -->` footer）
- [x] BKD intent issue PATCH tags 保留 `analyze` + `REQ-417`，statusId=review
