# REQ-self-accept-stage-1777121797: feat(accept): sisyphus self-dogfood accept stage

## Why

`skip_accept` 在生产 deploy values 里一直是 `true`（`orchestrator/deploy/my-values.yaml`），
原因是 sisyphus 的"集成 repo"（提供 ephemeral lab 的 helm chart / 部署脚本）从来没接好——
现在没有地方让 `make ci-accept-env-up` 跑、也没有 FEATURE-A* acceptance scenarios。
状态机里 ACCEPT_RUNNING / TEARDOWN_RUNNING 两段几乎从未实跑过，所有 REQ 都在 PR_CI_PASS
后直接 `accept.pass` short-circuit。

这是 self-dogfood 的最后一块缺口：

- staging-test checker 已能跑 sisyphus 自家 `ci-unit-test / ci-integration-test`（PR #78）
- dev-cross-check 已能跑 `ci-lint`（PR #78）
- pr-ci-watch 已能轮 sisyphus 自家 GHA
- **accept 还在跳过** —— 改 sisyphus 的 REQ 永远不会被 sisyphus 自家的 acceptance scenarios 验过

要补这一块，需要把"sisyphus 自己当 integration repo"的能力加上：

1. 顶层 Makefile 提供 `ci-accept-env-up` / `ci-accept-env-down` 真实 target
2. 用 docker-compose 起 ephemeral lab（postgres + orchestrator，不依赖 K8s helm）—— 单仓部署的 self-dogfood 场景不需要全套 helm 栈
3. 写第一条真实 FEATURE-A* smoke acceptance scenario（GET /healthz 返 200）
4. orchestrator action 支持 self-host 路径回退：当 `/workspace/integration/` 空 + 单 source repo 自带 `ci-accept-env-up` target → 用 source repo 充当 integration repo
5. 翻 `orchestrator/deploy/my-values.yaml` 的 `skip_accept: false`，让 sisyphus 改 sisyphus 的 REQ 真跑 accept

## What Changes

### Makefile + compose 栈（infra）

- 顶层 `Makefile` 新增 `ci-accept-env-up` / `ci-accept-env-down`（ttpos-ci 契约里
  `accept-up` / `accept-down` 的 ttpos-ci 命名变体，跟 `create_accept.py` 现用的
  target 名对齐）
- `deploy/accept-compose.yml`：postgres + orchestrator 的最小 ephemeral 栈
  - postgres 16-alpine，临时 volume，`sisyphus` user/db
  - orchestrator 从本仓 checkout build（`build: ./orchestrator`），dummy bkd_token /
    webhook_token，K8s 模式 off
  - orchestrator container 8000 → host 18000（端口可经 `SISYPHUS_ACCEPT_PORT` env 调）
- `scripts/sisyphus-accept-up-compose.sh`：build + up + 等 `/healthz` 200，
  stdout 末行 emit `{"endpoint":"http://localhost:<port>","namespace":"<ns>"}`
- `scripts/sisyphus-accept-down-compose.sh`：`docker compose down -v` 幂等清

### orchestrator action self-host 回退

- `actions/create_accept.py`：把 `cd /workspace/integration/* && make ci-accept-env-up`
  换成 helper 函数 `_resolve_integration_dir()`：
  - 优先选 `/workspace/integration/<basename>`（已有外部 integration repo 的多仓/跨仓场景）
  - 回退选 `/workspace/source/<basename>` —— **当且仅当**单 source repo 自带 `ci-accept-env-up:` target
  - 都没 → emit `ACCEPT_ENV_UP_FAIL`，理由 `no integration dir resolvable`
- `actions/teardown_accept_env.py`：同 helper 复用

### accept-agent prompt 修

- `prompts/accept.md.j2`：spec.md 路径从 `/workspace/openspec/changes/<REQ>/...`
  修成 `/workspace/source/*/openspec/changes/<REQ>/specs/*/spec.md`（M16 后真实位置）

### deploy values

- `orchestrator/deploy/my-values.yaml`：`skip_accept: true` → `false`，附注释
  说 sisyphus 自家 ci-accept-env-up 已接（指向 `deploy/accept-compose.yml`）

### 测试

- `orchestrator/tests/test_create_accept_self_host.py`：覆盖
  - integration 优先路径
  - integration 空 + source 单仓有 target → 回退命中
  - integration 空 + source 单仓没 target → emit env-up fail
  - integration 空 + source 多仓 → emit env-up fail（不强行选一个）

## Impact

- Affected specs: 新建 `self-accept-stage` capability（spec home: sisyphus 仓本身）
- Affected code:
  - `Makefile`（顶层）
  - `deploy/accept-compose.yml`（新建）
  - `scripts/sisyphus-accept-up-compose.sh` / `scripts/sisyphus-accept-down-compose.sh`（新建）
  - `orchestrator/src/orchestrator/actions/create_accept.py`
  - `orchestrator/src/orchestrator/actions/teardown_accept_env.py`
  - `orchestrator/src/orchestrator/prompts/accept.md.j2`
  - `orchestrator/deploy/my-values.yaml`
  - `orchestrator/tests/test_create_accept_self_host.py`（新建）
- Breaking changes: 无（fallback 是新增路径，旧的 `/workspace/integration/<basename>` 多仓
  场景行为不变）
- 部署上线后：下一个改 sisyphus 的 REQ 会真走 accept stage —— 第一条 smoke scenario
  失败 = sisyphus 自家 /healthz 挂了（多半就是这个 PR 把代码改坏了），可作为 escape valve
