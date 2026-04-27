# design — REQ-417 thanatos M1: wire accept stage to thanatos MCP

设计权威在仓库根 [`docs/thanatos.md`](../../../docs/thanatos.md) §3 / §4 / §7 / §9b。
本文档记录把那张数据流图落到 prompt + CLI + 文档时的关键决策。

## 决策 1 ─ 加 `python -m thanatos run-*` CLI 而非让 agent 跑 MCP stdio

**Why**：accept-agent 跑在 BKD Coder workspace，要触 vm-node04 上的 K3s 必须经
`mcp__aissh-tao__exec_run`。这个 MCP tool 是 request/response 一锤子买卖
（一次提交完整 stdin、等命令退出、收所有 stdout/stderr），**不能流式驱动**
MCP JSON-RPC 的 `initialize` → `tools/list` → `tools/call` 三段对话。

让 agent 自己拼 JSON-RPC 字符串喂 stdin 也可行（一次 echo 三条 message 给
`kubectl exec -i thanatos -- python -m thanatos.server`），但：

1. MCP server 的初始化握手需要互发 capability response，request/response 的
   aissh exec 拿不到中间的 server response 来构造下一轮请求。
2. JSON-RPC 出错时返回的 ID 对应需要 agent 自己解析，错一个字段全卡住。
3. 多轮调用（30 个 scenario）每次都要重起 server 进程 = 浪费。

CLI 一次 = 一行 shell，stdout 一份 JSON，stderr 是 diagnostics，agent 直接
`jq` 取字段。MCP server 保留：以后跨进程 caller（agent SDK / IDE 集成）走 stdio
会用得上。**两条入口共用 `runner.run_scenario` / `run_all` / `recall`**，逻辑
0 分叉。

**Tradeoff**：CLI 是新表面，违反 M0 的 "MCP-only" 原始设计。可接受 —— `runner` 是
那个真实业务码，CLI 跟 server 都是它的薄 transport adapter，没引入新逻辑。

## 决策 2 ─ `__main__.py` 双模式（0 args = server / N args = cli）

**Why**：M0 chart `deploy/charts/thanatos/templates/deployment.yaml` 写死的是
`command: ["python", "-m", "thanatos.server"]`，**不动 chart**。`python -m thanatos`
（短形式）历史上也是 server。M1 加 CLI 后，需要兼容两种入口：

- chart pod / Dockerfile entrypoint：`python -m thanatos.server`（继续启 server）
- accept-agent: `kubectl exec ... -- python -m thanatos run-scenario --...`（进 CLI）

`__main__.py` 看 `len(sys.argv)`：≤1 = server，>1 = cli。两条路径互不影响。
`thanatos.server.main` 不动；`thanatos.cli.main(argv=None)` 可注入 argv 方便测试。

**Tradeoff**：`python -m thanatos` 现在有两种语义。明确写在 `__main__.py` docstring
里，`thanatos/README.md` 同步更新。

## 决策 3 ─ thanatos pod 名通过 `accept_env.thanatos_pod` 传递，不让 orchestrator 探测

**Why**：[`create_accept.py`](../../../orchestrator/src/orchestrator/actions/create_accept.py)
解析的 endpoint JSON 已经 **全 dict 透传** 给 `accept.md.j2` 模板（`accept_env=accept_env`）。
业务仓 `make accept-env-up` 在 stdout 最后一行的 JSON 里多吐一个 `thanatos_pod`
字段是最薄的注入路径：

```json
{"endpoint":"http://lab....:8080","namespace":"accept-req-417","thanatos_pod":"thanatos-7b9f...-xyz"}
```

orchestrator 0 改动；prompt 直接读 `accept_env.thanatos_pod`。业务仓没启用
thanatos（不吐这字段）→ prompt 走 curl fallback。开关跟着业务仓走，sisyphus 不
强制。

**Tradeoff**：业务仓 Makefile 多 1 行 `kubectl get pod` 的 grep。对开发者负担可接受，
cookbook §6.5 给完整范本。

## 决策 4 ─ kb_updates commit 在 BKD Coder workspace 完成（不进 runner pod）

**Why**：sisyphus 强约定 [架构 §8](../../../docs/architecture.md) —— runner pod 是
**只读 checker**，所有 GH 写操作（`git push` / `gh pr create`）由 BKD Coder
workspace 的 gh auth 完成。thanatos 跑出来的 `kb_updates[]` 是 source repo 的
`.thanatos/anchors.md` / `.thanatos/flows.md` / `.thanatos/pitfalls.md` 增量。
agent 把它们应用到 **自己 cwd 的 source repo 工作目录**（已经在 Coder
workspace clone 过了），`git add .thanatos/ && git commit && git push origin
feat/REQ-417`，不经 runner pod。

**Tradeoff**：agent prompt 多一段 "解析 kb_updates → patch/append 文件 → git commit
push" 的步骤。**相对** runner pod 也开 GH 写权限来说，这个 prompt 复杂度可接受。

## 决策 5 ─ 任一仓启用 thanatos 即整 REQ 走 thanatos branch

**Why**：alternative 是 "每仓独立判断"——某仓有 skill.yaml 走 thanatos，
某仓没有走 curl，混合执行。代码量翻倍，prompt 复杂度爆炸，且 verifier 看到
"一半 thanatos 一半 curl 结果" 难判全局通过/失败。

M1 简化：**短路逻辑**——`for repo in /workspace/source/*; do test -f
$repo/.thanatos/skill.yaml; done`，一个 hit 就整个 REQ thanatos branch；全 miss
才 curl fallback。多仓且只一仓启用的少数 case 中，curl 走的那仓 scenarios
没人跑——靠 verifier 主观判 "scenario 数量 vs spec 中实际 #### Scenario: 数量
不一致" → escalate。这是边角问题，M1 不优化。

**Tradeoff**：多仓部分启用 thanatos 时丢数据。低概率 case，等 M2/M3 真有人撞到再
补 per-repo 路由。

## 决策 6 ─ 保 curl fallback path 不删

**Why**：sisyphus self-dogfood（本仓改本仓）`accept-env-up` 跑的是
[`deploy/accept-compose.yml`](../../../deploy/accept-compose.yml) docker compose
单 python + 单 postgres，纯 HTTP API，**没 UI 也没 `.thanatos/skill.yaml`**。
强制走 thanatos = M1 一上线 sisyphus 自己 accept stage 全红（M0 driver 全 stub）。

curl fallback 让 sisyphus 继续 self-dogfood，业务仓按需 opt-in，**渐进迁移**。

**Tradeoff**：维护两条 prompt 路径直到所有业务仓都迁完。预计 M3 后才能删 curl 路径，
[`accept.md.j2`](../../../orchestrator/src/orchestrator/prompts/accept.md.j2)
长度涨 ~80 行。可接受。

## 决策 7 ─ 用 `accept-<req-id>` namespace（已是当前 create_accept 行为）

**Why**：当前 [`create_accept.py:42`](../../../orchestrator/src/orchestrator/actions/create_accept.py)
已经把 namespace 算成 `f"accept-{req_id.lower()}"`，并通过 `SISYPHUS_NAMESPACE`
env 注入业务仓 Makefile。docs/thanatos.md §3 写的 "namespace: req-<REQ_ID>"
是不准确的旧文，**实际值是 `accept-req-<n>`**。

`thanatos.yaml` deployment + redroid sidecar 由业务仓 `accept-env-up` `helm install
-n $SISYPHUS_NAMESPACE` 起，跟 lab pod 共 namespace，sisyphus 不需要新建 namespace
逻辑。

**Tradeoff**：M1 顺手把 docs/thanatos.md §3 的 namespace 写法对齐到 `accept-<req>`
（一行字）。

## 不做（明确砍掉，留给 M2+）

- driver 真实运行时（playwright / adb / http 五方法填实现）—— M2
- preflight 节点数 / a11y tree 真实校验 —— M2
- screenshot 兜底 —— M2
- recall 真实 索引 —— M3+
- failure_class 自动分类（产品 bug / spec 错 / env 起不来 / flaky）—— 永不做（[docs/thanatos.md §1](../../../docs/thanatos.md) 明确，由 verifier 主观判）
- 业务仓 GHA "thanatos lint" 强制全量 a11y instrumentation —— 永不做（违反 JIT 原则）
- 暴露 observe / act / assert 细粒度原语 MCP tool —— v1 后讨论（M0 contract.spec.yaml 已锁 scenario 粒度）
- thanatos chart 升 OCI registry —— M2 视业务仓接入需要再决定
- `kubectl cp` 把 source repo 文件灌进 thanatos pod —— **M1 punt**：当前 M0 driver 全 NotImplementedError 不读文件，wire 到位即可；M2 实现 driver 时再决定 mount / cp 哪个干净
