# sisyphus orchestrator

Python 服务，sisyphus 编排核心。承担状态机 / 路由 / action 调度 / 机械 checker / verifier 框架 / watchdog / GC / 指标采集。

> 上层架构、哲学、stage 流见 [../docs/architecture.md](../docs/architecture.md)。
> 状态机权威见 [../docs/state-machine.md](../docs/state-machine.md)。

## 设计要点

- **REQ 状态显式持久化**：1 张 Postgres 表 (`req_state`)，行级 CAS 保并发
- **Transition table 驱动**：1 张 dict `(state, event) → (next_state, action)`，单测覆盖（[state.py](src/orchestrator/state.py)）
- **Event 由 webhook 推断**：tag + event_type → Event 枚举（[router.py](src/orchestrator/router.py)）
- **Action 处理器一文件一个**：每个 transition 触发的副作用（创 issue / 跑 checker / 更新 manifest）独立可测（[actions/](src/orchestrator/actions/)）
- **机械 checker 独立**：staging-test / pr-ci-watch / manifest_validate 都是 sisyphus 直接跑，不绕 BKD agent（[checkers/](src/orchestrator/checkers/)）
- **verifier-agent 框架（M14b/c）**：所有 stage success/fail 走 verifier 主观决策（[actions/_verifier.py](src/orchestrator/actions/_verifier.py)）
- **Prompt 用 Jinja2 模板**：[prompts/](src/orchestrator/prompts/)，与逻辑解耦
- **Dedup / 状态 / artifact / stage_runs / verifier_decisions 都持久化**：[store/](src/orchestrator/store/)
- **K8s runner**：每 REQ 一个 Pod + PVC，privileged + DinD，由 [k8s_runner.py](src/orchestrator/k8s_runner.py) 管生命周期
- **BKD 走 REST 不走 MCP**（PR #1）：[bkd.py](src/orchestrator/bkd.py) factory 默认 REST transport

## 取舍

- 不再可视化（接受）。换来 type hints + 表驱动测试 + 单 binary 部署
- Postgres 必装一份
- 老 REQ 不迁移：清重启

## 目录

```
src/orchestrator/
├── main.py            # FastAPI 入口
├── config.py          # 配置（pydantic-settings）
├── webhook.py         # /bkd-events 入口
├── engine.py          # event 分发 + action 调度
├── state.py           # 状态机 + transition table（核心）
├── router.py          # tag → Event 推断 + verifier decision 校验
├── bkd.py             # BKD 客户端 factory（REST 默认）
├── bkd_rest.py / bkd_mcp.py  # 两种 transport 实现
├── k8s_runner.py      # per-REQ Pod + PVC controller
├── watchdog.py        # M8 卡死兜底
├── runner_gc.py       # M10 即时 cleanup + retention GC
├── snapshot.py        # BKD list-issues → bkd_snapshot 同步 cron
├── observability.py   # structlog 配置 + 写表 helpers
├── store/             # asyncpg pool + req_state CAS + dedup + 各表写入
├── actions/           # 15 个 action handler（含 _verifier / _skip）
├── checkers/          # 6 个机械 checker（manifest_io / staging_test / pr_ci_watch / ...）
├── prompts/           # Jinja2 模板（含 verifier/{stage}_{trigger}.md.j2 共 12 个）
├── schemas/           # manifest.json (draft-07) + others
└── retry/             # M9 重试策略
```

## 部署

镜像：

```bash
docker build -t ghcr.io/phona/sisyphus-orchestrator:<tag> .
docker push ghcr.io/phona/sisyphus-orchestrator:<tag>
```

Postgres：

```bash
kubectl create ns sisyphus
helm install sisyphus-postgresql bitnami/postgresql -n sisyphus \
  --set auth.username=sisyphus,auth.database=sisyphus
```

orchestrator：

```bash
cp helm/values.dev.yaml my.yaml
# 编辑 my.yaml 填 secret.bkd_token / secret.webhook_token / secret.gh_token
helm install orch ./helm -n sisyphus -f my.yaml
```

BKD webhook 配置改为指向（一个 URL 收所有事件类型）：
- `https://sisyphus.<your-domain>/bkd-events`

必须带 header `Authorization: Bearer <你 values 里 webhook_token>`。

> 部署踩过的坑（uv / yoyo / helm / GHCR）见 [docs/deployment-pitfalls.md](docs/deployment-pitfalls.md)。

## 开发

```
cd sisyphus/orchestrator
uv sync                    # 装 deps
uv run uvicorn orchestrator.main:app --reload
uv run pytest              # 单测
```

加 / 改流水线 stage 的步骤见 [../docs/state-machine.md §8](../docs/state-machine.md#8-怎么在状态机加新-stage--event)。
