# REQ-default-involved-repos-1777124541 — tasks

## Stage: spec

- [x] author proposal.md（动机、方案、取舍、影响面）
- [x] author specs/helm-default-involved-repos/spec.md（3 条 Requirement，
      ADDED delta，每条带 SHALL/MUST prose + Scenario）

## Stage: implementation

- [x] orchestrator/helm/values.yaml：`env.default_involved_repos: [phona/sisyphus]`
- [x] orchestrator/helm/templates/configmap.yaml：`SISYPHUS_DEFAULT_INVOLVED_REPOS`
      条件块（list 非空时 csv-join 写入；list 空时省略 key 让 Settings 走默认）

## Stage: test

- [x] orchestrator/tests/test_contract_helm_default_involved_repos.py：
  - HDIR-S1 values.yaml `env.default_involved_repos == ["phona/sisyphus"]`
  - HDIR-S2 configmap.yaml 含 `SISYPHUS_DEFAULT_INVOLVED_REPOS` 写入 + 条件块
  - HDIR-S3 Settings(`SISYPHUS_DEFAULT_INVOLVED_REPOS=phona/a,phona/b`) 解出 `["phona/a","phona/b"]`

## Stage: PR

- [x] git push feat/REQ-default-involved-repos-1777124541
- [x] gh pr create
- [x] move issue to review
