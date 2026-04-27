# REQ-417: thanatos M1 — wire accept stage to thanatos MCP

## 问题

[REQ-thanatos-m0-scaffold-v6-1777283112](../REQ-thanatos-m0-scaffold-v6-1777283112/)
落地了 thanatos 模块 + helm chart，**但 sisyphus accept stage 仍走纯 curl 路径**：
[`accept.md.j2`](../../../orchestrator/src/orchestrator/prompts/accept.md.j2) 让
accept-agent 直接 `kubectl exec runner -- curl $ENDPOINT`，跟 [docs/thanatos.md
§7](../../../docs/thanatos.md) 描述的 "accept-agent 调 thanatos MCP → 拿
results + kb_updates → 写回 source repo feat 分支" 数据流完全脱节。

后果：

- M0 已经有 `python -m thanatos.server` 跑得起来，但**没人调用它**。
- 业务仓 `accept-env-up` 还没有 "顺便 `helm install thanatos`" 的样板，cookbook
  ([`docs/cookbook/ttpos-arch-lab-accept-env.md`](../../../docs/cookbook/ttpos-arch-lab-accept-env.md))
  零 thanatos 提及。
- 业务仓 `.thanatos/` 目录约定（[docs/thanatos.md §2 / §5](../../../docs/thanatos.md)）
  没有进 [docs/integration-contracts.md](../../../docs/integration-contracts.md)，
  business-repo 接入方不知道要不要建 `.thanatos/skill.yaml`。

## 方案

**M1 = 全责把"accept-agent 调 thanatos"这条调用链落到 prompt + 文档 + shell-friendly
入口三层**。**不**写 driver 真实运行时（playwright / adb / http 五方法仍 `raise
NotImplementedError("M0: scaffold only")`），那是 M2 的活。

### 1. 新增 `thanatos.cli` —— shell 一次 run-scenario

`thanatos/src/thanatos/cli.py` 暴露三个 subcommand 镜像 MCP server 的三个 tool：

```bash
python -m thanatos run-scenario --skill <path> --spec <path> --scenario-id S1 --endpoint $E
python -m thanatos run-all       --skill <path> --spec <path> --endpoint $E
python -m thanatos recall        --skill <path> --intent "..."
```

stdout 一行 JSON（与 MCP `call_tool` 返回的 ScenarioResult / list 同 schema），exit
`0` 派发成功，`2` argparse / 校验错，`3` runner 抛异常。`__main__.py` 0 参数
保留 M0 的 server 行为（chart deployment.yaml + Dockerfile entrypoint 不动）。

### 2. 改写 `accept.md.j2` —— thanatos 优先 / 无 skill 退 curl

新流程（条件分支）：

1. agent 在 BKD Coder workspace 跑 `mcp__aissh-tao__exec_run` → `kubectl exec
   runner-$REQ -- ls /workspace/source/*/.thanatos/skill.yaml` 检测每个 source
   repo 是否启用 thanatos。
2. **任一仓启用 thanatos** → 走新路径：
   - 用 `accept_env.thanatos_pod` / `accept_env.namespace`（`make accept-env-up`
     吐的 JSON 增字段，cookbook 教如何吐）找到 thanatos pod。
   - 对该仓 spec.md 的每个 `#### Scenario:` 跑：
     `kubectl -n <ns> exec <thanatos_pod> -- python -m thanatos run-scenario
     --skill /<repo>/.thanatos/skill.yaml --spec /<repo>/openspec/.../spec.md
     --scenario-id <id> --endpoint <endpoint>`
   - 解析 JSON 收集 pass/fail + `kb_updates[]`。
   - 把 `kb_updates` 应用到 agent 自己 cwd 的 source repo working tree（`patch` /
     `append` 两种 action）→ `git add .thanatos/ && git commit -m
     'kb: thanatos updates from REQ-417 accept' && git push origin
     feat/REQ-417`。
3. **所有仓都没 `.thanatos/skill.yaml`** → 退回 M0 既有 curl path（保 sisyphus
   self-dogfood：本仓 accept 既无 UI 也没 thanatos skill，纯 HTTP curl 即可）。

vacuous-pass 防御：thanatos branch 跑出空 scenario list（spec.md 缺 `#### Scenario:`）→ 强制 `result:fail`。

### 3. `docs/integration-contracts.md` 加 §10 thanatos opt-in

新章节描述：

- 业务仓**可选**建 `.thanatos/skill.yaml`（schema 见 thanatos M0 contract.spec.yaml）
- 启用后 accept stage 走 thanatos run-scenario，未启用退 curl
- `.thanatos/{anchors,flows,pitfalls}.md` 由 thanatos 写、agent commit 到 feat 分支
- 业务仓 `make accept-env-up` 必须把 thanatos 一起拉起 + 在 endpoint JSON 多吐
  `thanatos_pod` / `thanatos_namespace`（不吐则等同未启用，agent 自动退 curl）

### 4. `docs/cookbook/ttpos-arch-lab-accept-env.md` 加 §6.5 helm install thanatos

新增样板片段：

```makefile
accept-env-up:
    helm upgrade --install lab charts/accept-lab -n $(NS) --create-namespace --wait
    helm upgrade --install thanatos $(SISYPHUS_THANATOS_CHART) -n $(NS) \
        --set driver=playwright --wait
    @kubectl -n $(NS) wait --for=condition=ready pod -l app.kubernetes.io/name=thanatos --timeout=2m
    @thanatos_pod=$$(kubectl -n $(NS) get pod -l app.kubernetes.io/name=thanatos \
                      -o jsonpath='{.items[0].metadata.name}'); \
     printf '{"endpoint":"...", "namespace":"%s", "thanatos_pod":"%s"}\n' \
        "$(NS)" "$$thanatos_pod"
```

`SISYPHUS_THANATOS_CHART` 默认指 sisyphus 仓内 `deploy/charts/thanatos/`（runner pod 内 `/workspace/source/sisyphus/deploy/charts/thanatos`），业务仓可 override 自己拉的 chart 副本。

## 取舍

- **加 CLI 而不是让 agent 直接说 MCP stdio**：`mcp__aissh-tao__exec_run` 是
  request/response 包，不能流式驱动 MCP JSON-RPC handshake。让 agent 拼一串
  `initialize` + `tools/call` JSON 喂 stdin 可行但脆。CLI 一行 = 一次调用，
  shell + jq 处理结果天然顺手。MCP server 仍保留给未来跨进程 caller 用，**两条
  入口共用 `runner.run_scenario`**，没分叉。
- **driver 仍全 NotImplementedError**：M1 严格只做 wiring。真实 driver
  实现是 M2 的范围（playwright spawn chromium / adb shell / http client +
  preflight + a11y tree + screenshot 兜底）。
- **保留 curl fallback**：sisyphus self-dogfood REQ 没 UI 没 `.thanatos/skill.yaml`，
  强制走 thanatos 必失败（driver 全 stub）。fallback 让 self-dogfood 继续工作。
- **`thanatos_pod` 不让 sisyphus 自己探测**：[`create_accept.py`](../../../orchestrator/src/orchestrator/actions/create_accept.py)
  已经透传 `accept_env` 全 dict 给 prompt，业务仓 Makefile 在 endpoint JSON 里
  多吐一个字段是最薄的注入路径。让 orchestrator 主动 `kubectl get pod`
  在每个 REQ 都加一次往返，不值。

## 影响范围

- `thanatos/src/thanatos/cli.py` —— 新增（CLI dispatcher）
- `thanatos/src/thanatos/__main__.py` —— 改：0 参数走 server，N 参数走 cli
- `thanatos/tests/test_cli.py` —— 新增（4+ smoke case）
- `orchestrator/src/orchestrator/prompts/accept.md.j2` —— 改写 Step 3（thanatos branch + curl fallback）
- `orchestrator/tests/test_prompts_accept_thanatos.py` —— 新增（render 出来的 prompt 形状校验）
- `docs/integration-contracts.md` —— 新增 §10 thanatos opt-in
- `docs/cookbook/ttpos-arch-lab-accept-env.md` —— 新增 §6.5 helm install thanatos
- `openspec/changes/REQ-417/` —— 本 change

**不**改：thanatos `runner.py` / `server.py` / `drivers/*` / state machine /
`create_accept.py` / `teardown_accept_env.py` / runner Dockerfile / helm chart
（M0 chart 不动，业务仓 cookbook 用 `helm install` 调即可）。
