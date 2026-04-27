# design — REQ-415 (thanatos M1 wire accept stage to MCP)

权威设计 [`docs/thanatos.md`](../../../docs/thanatos.md) §7 已经描述了 accept
stage 调用 thanatos 的数据流。本文档记录把那个数据流落到 sisyphus prompt + action
代码时的关键决策。

## 决策 1 ─ env-up JSON 用 nested `thanatos` block，不平铺顶层 key

**Why**：env-up 输出已经平铺了 `endpoint` / `namespace` 两个 caller 关心的核心
字段；再加 `thanatos_pod` / `thanatos_namespace` / `thanatos_skill_repo` 三个
prefix 同源字段会让顶层 schema 噪。块化（nested）既给将来扩展（`thanatos.service`
/ `thanatos.port` / `thanatos.tls_secret`）留空间，也跟 [`docs/thanatos.md` §7](../../../docs/thanatos.md)
描述的"thanatos 是 acceptance harness 单独一层"匹配。

**Tradeoff**：sisyphus side 解 JSON 多写一行 `accept_env.get("thanatos") or {}`。
代价微不足道。

## 决策 2 ─ `thanatos` block 完全可选，不强制业务仓现在就出

**Why**：M0 落地后没有任何业务仓在 `accept-env-up` 里 `helm install` thanatos
chart（cookbook 还没改，那是 M2 跟 driver 实现一起的活）。如果 M1 把 `thanatos`
block 列必填，sisyphus self-accept（compose 路径）+ 所有现存业务仓全部炸。

**怎么落**：`create_accept.py` 用 `accept_env.get("thanatos") or {}` 容错；模板用
`{% if thanatos_pod %}` 双分支。`thanatos_pod` 为空 → 模板渲染老分支，行为完全
等同 [REQ-self-accept-stage-1777121797 SDA-S9](../REQ-self-accept-stage-1777121797/specs/self-accept-stage/spec.md)。

## 决策 3 ─ accept-agent 通过 `kubectl exec ... -i python -m thanatos.server` 喂 stdio MCP，不上 service

**Why**：跟 [`docs/thanatos.md` §3](../../../docs/thanatos.md) 决定一致 ——
"白嫖 K8s 鉴权 + 不引入新 RBAC 网络面"。MCP stdio 协议天然就是 stdin/stdout
JSON-RPC stream，`kubectl exec -i` 把 agent 的 JSON 请求灌进 pod 的 thanatos
process 即可。helm chart 也明确说 service 只 debug 用。

**Tradeoff**：每次 `tools/call` 都要新起一个 `kubectl exec` 进程（不能复用 mcp
session）。但 accept stage 一次 REQ 就跑一轮 `run_all`，session 反复 spin-up 不是
瓶颈。

## 决策 4 ─ 模板里硬编码 MCP JSON-RPC 调用 shape，不抽函数

**Why**：accept-agent 是 Claude agent，不是 Python 程序 —— 它"看"的是 prompt
描述的 shell 命令。给它写一段直白的 `printf '...JSON-RPC...' | kubectl exec ...`
比抽一层"thanatos client wrapper"更直观，agent 也容易照抄。MCP initialize +
tools/call 的 wire format 本身已经稳定。

**Tradeoff**：MCP client 库有变化时模板要手改。但 sisyphus 主动选了 stdio over
kubectl exec 这条最瘦的路径，client 库本来就不在 sisyphus 控制下。

## 决策 5 ─ kb_updates 由 accept-agent commit 到 feat 分支，不绕回 sisyphus

**Why**：[`docs/thanatos.md` §8](../../../docs/thanatos.md) 钉死了"thanatos 算
delta，agent 顺手 commit；不走 propose/auto-refresh 二分；不走 sisyphus checker
卡未提交"。M1 只是把这条原则落进 prompt —— accept-agent 拿到 `kb_updates: []` 后
对每个 entry `git apply` / `>>` 到对应 path，再 `git add .thanatos/ && git commit
&& git push origin feat/<REQ>`。

sisyphus 这边 `create_accept.py` 不需要知道 kb_updates 存在，env-down 也不读它。
全部是 accept-agent 在 feat 分支上自己处理。

## 决策 6 ─ M0 driver stub 阶段也照样跑 thanatos MCP，不 short-circuit

**Why**：M1 上线后所有接 thanatos 的业务仓都会拿到 `pass=false +
failure_hint="M0: thanatos scaffold only, drivers not implemented"`，看起来"白跑
一轮"。但这正是想要的：

- 让 `result:fail` 上报到 verifier，verifier 看到 `failure_hint` 字符串就知道是
  M0 stub，escalate（既不 fixer-dev 也不 fixer-spec），人接手把 driver 接进来
- 提前发现 wire-up bug（kubectl exec 通不通 / JSON parse 对不对 / kb_updates 应用
  对不对），不要等到 M2 driver 真实现时才debug 编排链

**Tradeoff**：每个 REQ accept 多 1 个 `result:fail` 转 escalate。可接受 —— M2 上线
后就消失。

## 决策 7 ─ accept.md.j2 老分支保留不删

**Why**：sisyphus self-accept（docker compose）永远不会接 thanatos —— 它在
runner pod 的 DinD 里跑，没有 K3s namespace 概念，thanatos pod 没地方 `helm
install`。即使 M2/M3/M-final 全部 ready 了，sisyphus 自家 acceptance 也保留 curl
路径。删老分支等于砍 sisyphus dogfood 自己 验收。

**怎么落**：模板用 `{% if thanatos_pod %}...{% else %}...{% endif %}` 两分支，
不在测试里强制要求只走某一个分支。

## 决策 8 ─ 不引入新 stage / state / event / checker / verifier 模板

**Why**：M1 是粘合层，不是新能力。状态机视角 accept stage 没变（pre-accept
env-up → dispatch accept-agent → wait session.completed → teardown_accept_env），
verifier-agent 视角 accept stage 也没变（看 result:pass/fail tag + follow-up 报告
判 pass/fix/escalate）。

如果 M1 引入新 state（比如 `THANATOS_RUNNING`），engine.py / state.py / 多个
verifier prompt / state-machine.md 全部要跟着改，跟"M1 = 把 prompt 双分支化"的
真实工作量极不匹配。

## 不做（明确砍掉）

- 不动 thanatos source code、driver 实现、scenario parser、skill loader（M0 已
  锁，M2 才动）
- 不动 deploy/charts/thanatos/（M0 已锁）
- 不动 state.py / engine.py / router.py / 任何 checker / 任何 verifier prompt
- 不动 deploy/accept-compose.yml（sisyphus 自家 self-accept 走老路）
- 不动 docs/cookbook/（M2 跟 driver 实现一起改业务仓 accept-env-up 模板）
- 不写新 stage_runs / verifier_decisions schema（沿用现有）
- 不写 thanatos pod 健康检查 fallback（pod 起不来是 helm install 的问题，由业务
  仓 accept-env-up 自己等 ready —— 那是 [`docs/integration-contracts.md` §2.3](../../../docs/integration-contracts.md)
  的"幂等性硬要求"职责）
