# Design: self-dogfood accept stage

## Decision: docker-compose, not helm

Sisyphus 自家 lab 跟 `phona/ttpos-arch-lab`（multi-service Flutter+Go+pg）形态完全不同。
sisyphus 是 **单 Python 进程 + 单 postgres**，用 helm 起整套 K8s namespace + service
就是 over-engineering。

docker-compose 的优势：

- runner pod 已自带 DinD（runner/entrypoint.sh 起 dockerd），不需要额外 K8s 资源
- compose 启停秒级（vs helm install --wait 几分钟）
- 不污染 vm-node04 的 K3s（不挤占别 REQ 的 namespace 资源）
- 端口模型简单：orchestrator container :8000 → host :18000，`curl http://localhost:18000`
  即可（DinD daemon 跟 runner pod 同 network namespace，host port = pod port）

代价：

- 不验 K8s 上的部署模式（helm chart / probes / ingress 等没被 exercise）
- 但这一层有 staging-test + GHA 一起把关，accept stage 主要验"业务行为对不对"，
  K8s 部署形态属另一层关注点

## Decision: source-as-integration fallback

self-host 场景下，sisyphus 自己**既是** source 又是 integration——
没必要再单建一个 `phona/sisyphus-arch-lab` 仓存 compose 文件。
compose 文件直接放 sisyphus 仓 `deploy/accept-compose.yml`，
顶层 Makefile 直接 `ci-accept-env-up` 跑它。

`actions/create_accept.py` 的 `cd /workspace/integration/*` glob 模式是给
"独立 integration 仓"准备的（multi-repo 跨仓项目）。self-host 时 integration dir 空，
glob 失败。**回退**：检查 `/workspace/source/<single>/Makefile` 有没有
`ci-accept-env-up:` target —— 有就用它充 integration dir。

回退条件**严格**：

- `/workspace/integration/` 必须空（或子目录里没 Makefile）
- `/workspace/source/` 下**正好一个** repo 自带 `ci-accept-env-up:` target
- 多于一个候选 → 报错（不强行选）

这样多仓 REQ 的语义不变（必须显式接 integration repo），单仓 self-host 自动 work。

## Decision: smoke scenario 仅 /healthz

第一次落地优先正确性，scenario 只验"orchestrator 起得来 + /healthz 200"。
后续 REQ 再加：webhook 路由 / admin 端点 / state 转移等更深 scenarios（每加一条都要
通过 lab 启动开销验证一次，慢慢加比一次铺开稳）。

## Decision: 不引入 SISYPHUS_INTEGRATION_FROM_SOURCE 配置

考虑过加一个 list 配置（哪些 source basename 自动充 integration），但：

- 配置漂移风险（helm values 跟代码逻辑分两处）
- self-host 场景**就一种**：单仓 + 自带 ci-accept-env-up target — 用代码静态检测
  足够了，不需要外部声明
- 多仓场景必须显式接 integration repo，没必要给"自动选 leader"开后门

代码里直接用 Makefile target 的存在性当 self-host 信号。

## Out of scope

- helm-based integration repo 的引入（需要时再起 REQ）
- multi-repo accept env 的协调（需要时再起 REQ —— 多 source 仓平等地都贡献 service 给 lab）
- accept-agent prompt 的彻底重写（M16 后路径有变，本 REQ 只补 spec.md 读取路径，不动 scenario 协议）

## Risk

- **port 18000 占用**：runner pod 几乎不会在 accept 阶段同时跑别的 host-port-mapped
  服务，但不能 100% 排除。脚本 up 前 `docker compose down` 兜底；冲突在 `docker
  compose up` 阶段会立即 fail，error 可见。
- **build 时间**：每次 accept 都从源码 build orchestrator image（~1-2 min）。
  postgres 用 alpine 镜像缓存。可接受；如果以后嫌慢，再加 `image: sisyphus-orch:accept`
  + GHA 预构建到 GHCR。
- **postgres 数据**：每次 accept 用全新 ephemeral volume（compose `down -v`），
  不残留状态。
