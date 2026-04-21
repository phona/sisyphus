# sisyphus orchestrator

替代当前 n8n workflow + router.js + build-template.py 全套，单 Python 服务。

## 为什么是它

n8n 在 dev 阶段帮忙快速验证设计；72 节点 + 11 层补丁 + 4 个 Gate 后，复杂度
集中在"用 tag 推断 REQ 当前状态再决策"。这正是状态机的活，n8n 表达不好。

把"REQ 状态"显式存（Postgres），事件来了查 transition table，决定 action。
n8n 全砍，BKD webhook 直接打到本服务。

## 设计要点

- **REQ 状态显式持久化**：1 张 Postgres 表 (`req_state`)，CAS 更新
- **Transition table 驱动**：1 张 dict `(state, event) → (next_state, action)`，单测覆盖
- **Event 由 webhook 推断**：tag + event_type → Event 枚举（router.py 复刻原 router.js）
- **Action 处理器一文件一个**：每个 transition 触发的副作用（创 issue / 更新 tag / 发 prompt）独立可测
- **Prompt 用 Jinja2 模板**：`prompts/*.md.j2`，与逻辑解耦
- **Dedup 持久化**：Postgres 表存 event_id 集合（之前在 n8n staticData，现在搬出来）

## 取舍

- 不再可视化（接受）。换来 type hints + 表驱动测试 + 单 binary 部署
- Postgres 必装一份（之前 docs/observability.md 已规划，顺势做）
- 老 REQ 不迁移：dev 阶段，全清重启

## 目录

```
src/orchestrator/
├── main.py            # FastAPI 入口
├── config.py          # 配置（pydantic-settings）
├── webhook.py         # /bkd-events, /bkd-issue-updated
├── state.py           # 状态机 + transition table（核心）
├── router.py          # tag → Event 推断
├── ci_diagnose.py     # 移植自 router/ci-diagnose.js
├── bkd.py             # BKD MCP 客户端
├── observability.py   # structlog
├── store/
│   ├── db.py          # asyncpg pool
│   ├── req_state.py   # state CAS
│   └── dedup.py       # event_id seen
├── actions/           # 12 个 action handler
└── prompts/           # Jinja2 模板
```

## 部署

镜像：
```bash
docker build -t ghcr.io/weifashi/sisyphus-orchestrator:0.1.0 .
docker push ghcr.io/weifashi/sisyphus-orchestrator:0.1.0
```

PG（用户先在 namespace `sisyphus` 装一份 Bitnami postgresql）：
```bash
kubectl create ns sisyphus
helm install sisyphus-postgresql bitnami/postgresql -n sisyphus \
  --set auth.username=sisyphus,auth.database=sisyphus
```

orchestrator：
```bash
cp helm/values.dev.yaml my.yaml
# 编辑 my.yaml 填 secret.bkd_token / secret.webhook_token
helm install orch ./helm -n sisyphus -f my.yaml
```

BKD webhook 配置改为指向（一个 URL 收所有事件类型）：
- `https://sisyphus.coder.tbc.5ok.co/bkd-events`

必须带 header `X-Sisyphus-Token: <你 values 里 webhook_token>`。

> 部署踩过的坑（uv/yoyo/helm/GHCR）见 [docs/deployment-pitfalls.md](docs/deployment-pitfalls.md)。

## 开发

```
cd sisyphus/orchestrator
uv sync                    # 装 deps
uv run uvicorn orchestrator.main:app --reload
uv run pytest              # 单测
```
