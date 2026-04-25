# REQ-default-agent-model-sonnet-1777131381 — tasks

## Stage: spec

- [x] author proposal.md（动机、方案、取舍、影响面）
- [x] author specs/agent-model-default/spec.md（ADDED delta，DAMS-S1/S2 scenarios）
- [x] author specs/agent-model-default/contract.spec.yaml

## Stage: implementation

- [x] orchestrator/src/orchestrator/config.py：`agent_model` default `None` → `"claude-sonnet-4-6"`，更新注释

## Stage: test

- [x] orchestrator/tests/test_contract_default_agent_model_sonnet.py：
  - DAMS-S1 无 env override 时 settings.agent_model == "claude-sonnet-4-6"
  - DAMS-S2 SISYPHUS_AGENT_MODEL env 可覆盖

## Stage: PR

- [x] git push feat/REQ-default-agent-model-sonnet-1777131381
- [x] gh pr create
- [x] move issue to review
