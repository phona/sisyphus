# Tasks

## Stage: contract / spec
- [x] author `openspec/changes/REQ-feat-precheck-373-1777864856/proposal.md`
- [x] author spec delta `specs/feat-stage-precheck/spec.md` —— ADDED requirements + scenarios PRECHECK-S1..S6

## Stage: implementation
- [x] `orchestrator/src/orchestrator/prompts/_shared/hooks/precheck.md.j2`：新建 hook 模板
- [x] `orchestrator/src/orchestrator/config.py`：加 `stage_precheck_enabled: dict[str, bool]` 字段（pydantic-settings）
- [x] `orchestrator/src/orchestrator/config.py`：默认 `enabled_prompt_hooks = ["mcp_preflight", "precheck", "self_issue_constraint"]`
- [x] `orchestrator/src/orchestrator/prompts/__init__.py`：注入 `stage_precheck_enabled` 为 jinja2 global
- [x] `docs/integration-contracts.md` §2.6：加 `ci-precheck` 业务仓可选契约 target

## Stage: tests
- [x] `orchestrator/tests/test_prompts_precheck.py`：单测覆盖 PRECHECK-S1..S6
- [x] `tests/test_prompts_mcp_preflight.py`：同步 enabled_prompt_hooks 默认值 assertion (含 precheck)

## Stage: PR（推之前必须全绿）
- [x] git push feat/REQ-feat-precheck-373-1777864856
- [x] `make ci-lint` → 全绿
- [x] `make ci-unit-test` → 全绿
- [x] `make ci-integration-test` → 全绿（无 PG 视为 pass）
- [x] gh pr create --label sisyphus + sisyphus cross-link footer
