# REQ-415: feat(thanatos): M1 wire accept stage to thanatos MCP

## 问题

[REQ-thanatos-m0-scaffold-v6-1777283112](../REQ-thanatos-m0-scaffold-v6-1777283112/proposal.md)
落了 thanatos 模块骨架（MCP stdio server + scenario parser + skill loader + helm
chart），但 sisyphus 的 accept stage 还没接它 —— `accept.md.j2` 里 accept-agent
仍走"读 spec.md → 直接 curl endpoint"的人工路径，跟 thanatos 没任何粘连。这意味着
M2/M3 真接 driver 实现时无处插入；同时业务仓如果在 `accept-env-up` 里 `helm
install` 了 thanatos pod 也没人调它。

## 方案

只动**编排粘合层**，**不动** thanatos 自身代码、driver、scenario parser。M1 把
accept stage 的 prompt 跟 `create_accept.py` 升级成"知道 thanatos pod 存在、知道
怎么 kubectl exec 它跑 MCP"——但保留"thanatos 不存在 → 老路径"的兜底，让 sisyphus
self-accept（docker compose，无 thanatos pod）继续工作。

### 1. `accept-env-up` JSON 契约扩展（向后兼容）

[`docs/integration-contracts.md` §3](../../../docs/integration-contracts.md) 里
`make accept-env-up` 的 stdout 末行 JSON 加一个**可选** `thanatos` 顶层 block：

```json
{
  "endpoint": "http://lab.accept-req-415.svc.cluster.local:8080",
  "namespace": "accept-req-415",
  "thanatos": {
    "pod": "thanatos-7d8f8d8f8-abcde",
    "namespace": "accept-req-415",
    "skill_repo": "ttpos-flutter"
  }
}
```

- `thanatos.pod`：accept-agent `kubectl exec` 进它跑 `python -m thanatos.server`
- `thanatos.namespace`：默认等于顶层 `namespace`，业务仓自己 `helm install` 到
  其它 namespace 时显式覆盖
- `thanatos.skill_repo`：accept-agent 从 `/workspace/source/<skill_repo>/.thanatos/`
  取 skill.yaml 给 thanatos MCP 调用喂参数

`thanatos` block 缺省 → accept-agent 走老路（直接 curl endpoint 跑 scenarios）。
sisyphus 自家 `deploy/accept-compose.yml` 不变（不起 thanatos pod，走老路）。

### 2. `create_accept.py` 抽取 + 透传

`actions/create_accept.py` 在解析完 env-up JSON 后，把 `thanatos` block 拍平
进 `prompt ctx`：

```python
thanatos_block = accept_env.get("thanatos") or {}
prompt = render(
    "accept.md.j2",
    ...
    thanatos_pod=thanatos_block.get("pod"),
    thanatos_namespace=thanatos_block.get("namespace") or namespace,
    thanatos_skill_repo=thanatos_block.get("skill_repo"),
)
```

`thanatos_pod` 为空 → 模板渲染 fallback 分支（老路径）。

### 3. `accept.md.j2` 双分支

新增一个 thanatos MCP 分支（`{% if thanatos_pod %}`），保留老分支兜底。
thanatos 分支干这些事（all kubectl 调用走 `mcp__aissh-tao__exec_run`）：

1. `kubectl exec` 进 thanatos pod，stdin 喂 MCP `tools/call run_all` 请求
2. 解析返回 `[ScenarioResult]` JSON
3. `kb_updates`（如有）apply 到 `/workspace/source/<skill_repo>/`，git
   add/commit/push 到 `feat/{{ req_id }}` 分支
4. 按全 `pass=true` / 任一 `pass=false` 贴 `result:pass` / `result:fail` tag
5. follow-up 报告 per-scenario evidence

老分支（`{% else %}`）保持当前 ([SDA-S9](../REQ-self-accept-stage-1777121797))
"glob /workspace/source/*/openspec/changes/<REQ>/specs/*/spec.md → 直接 curl 跑
scenario" 行为不变。

### 4. 不动的部分（明确砍掉）

- ❌ thanatos source（M0 driver 还是 `NotImplementedError`，M2 才填真实现 —— M1
  跑过 thanatos MCP 会拿到 `pass=false` + `failure_hint="M0 scaffold only"`，accept-agent
  按 `result:fail` 走，verifier 看 hint escalate 即可）
- ❌ helm chart（`deploy/charts/thanatos/` 已经在 M0 落地）
- ❌ state machine / actions / checkers / verifier prompt（M1 仅碰 accept 一个 prompt + 一个 action）
- ❌ sisyphus 自家 `accept-compose.yml`（self-accept 走老路，无 thanatos）
- ❌ 业务仓 `accept-env-up` Makefile 模板（cookbook 改是 M2/M3 跟 driver 实现一起的活）

## 取舍

- **可选 `thanatos` block 而非必填**：sisyphus self-accept compose、所有还没接
  thanatos 的业务仓都不能因为这次升级炸；fallback 路径必须留。
- **JSON block 而非 flat key**：env-up 输出已经有 `endpoint`/`namespace` 平铺
  字段，再加 `thanatos_pod` / `thanatos_namespace` / `thanatos_skill_repo` 三
  个会污染顶层 namespace。`thanatos` block 一目了然，将来加 `service` / `port`
  也不冲突。
- **不在 sisyphus 自家 compose 加 thanatos**：sisyphus orchestrator 是 HTTP
  service，scenarios 全 `curl` 即可（已经有 SDA-S1..S9 覆盖）；硬接 thanatos
  只为追求形式统一不值。**`.thanatos/skill.yaml` 也不进 sisyphus 仓**——M1 不
  给 sisyphus 自身做验收能力升级。
- **只跑 `run_all` 不跑 `run_scenario`**：accept-agent 不挑 scenario，spec.md
  里所有 `#### Scenario:` 都跑；MCP 接口已经给了 `run_all`，没必要单 scenario
  调用 N 次。

## 影响范围

- `orchestrator/src/orchestrator/prompts/accept.md.j2` —— 改：双分支 + thanatos MCP 调用
- `orchestrator/src/orchestrator/actions/create_accept.py` —— 改：解 thanatos block + 透传 ctx
- `docs/integration-contracts.md` —— 改 §3：env-up JSON 加 thanatos 字段
- `orchestrator/tests/test_create_accept_thanatos.py` —— 新增：thanatos block 抽取 + 透传 + 缺省回退
- `orchestrator/tests/test_prompts_accept_thanatos.py` —— 新增：模板双分支渲染
- `openspec/changes/REQ-415/` —— 本 change

**不**改：thanatos/ / deploy/charts/thanatos/ / state.py / router.py / engine.py /
checkers / verifier prompt / accept-compose.yml / runner Dockerfile / 业务仓 Makefile。
