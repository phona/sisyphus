# REQ-953 端到端首跑复盘

**日期**：2026-04-21
**REQ**：REQ-953 — `e2e-runner-test: 加个 health 检查端点 /api/healthz`
**最终状态**：卡在 ci-int round-3（envs bug 死循环），未达 done
**目的**：第一次用新 sisyphus-runner 容器模型跑全套研发流，验证 state 机 + 容器 + agent

---

## 摘要

整套基础设施（state 机、容器、observability、ingress、auth、image build/push）**全部按预期工作**。
死锁修复（reviewer.pass → 重跑 ci-int）**首次实战通过**。

但碰到一个被 AI 自己**正确诊断**却**无路可走**的问题：sisyphus runner 镜像装的是 Debian 自带 docker.io（Docker 20.10），缺 `docker compose` v2 plugin。被测项目 Makefile 用 v2 syntax → ci-int 必失败。bugfix-agent 准确识别为环境问题，但 state 机当时只有 code/test/spec 三类诊断，agent 强归为 test-bug → test-fix-agent → reviewer → 重跑同环境 → 死循环到 round-3。

---

## 走通的事（按预期）

### 1. State 机：15 步无死锁
```
init
 → analyzing      (intent.analyze, 4m)
 → specs-running  (analyze.done → fanout_specs, 8m 双 spec 并发)
 → dev-running    (spec.all-passed → create_dev, 5m)
 → ci-unit-running (dev.done → create_ci_runner_unit, 2m)
 → ci-int-running (ci-unit.pass → create_ci_runner_integration, 2m)
 → bugfix-running (ci-int.fail → open_gh_and_bugfix, 2m)
 → test-fix-running (bugfix.done → create_test_fix, 5m)
 → reviewer-running (test-fix.done → create_reviewer, 3m)
 → ci-int-running (reviewer.pass → create_ci_runner_integration) ★
 → ... (round 2/3 重复 bugfix → test-fix → reviewer → ci-int)
```
**★ 关键**：reviewer.pass → 重跑 ci-int 这条 transition 是状态机重写的**核心动机**，
旧 n8n 版本会死锁在这里（Gate "stale" filter 拒绝），新 state 机自动派单零卡顿。

### 2. 容器复用
- container `sisyphus-runner-REQ-953` 同一个，跑 42+ 分钟
- 跨 9 个 stage（analyze/spec×2/dev/ci-unit/ci-int×3/bugfix×2/test-fix×2/reviewer×2）
- Go modules / pub cache / docker images 全留存
- 第二次 ci-int 起步比第一次快（cache 已暖）

### 3. Observability
- `event_log` 28+ webhook.received / 9+ action.executed / 9+ router.decision / 2 dedup.hit
- `bkd_snapshot` 同步 387+ rows，5 min 间隔
- snapshot UPSERT 修过 datetime bug 后稳定

### 4. State 机正确处理 late event
**bugfix issue (round 1) 在 reviewer.pass 之后又发了一次 session.completed**：
- 11:14:26 webhook.received bugfix.done from 7ju6m414
- 当时 state 已 = ci-int-running（reviewer.pass 11:12:33 推进过去）
- engine.illegal_transition log → skip，不破坏状态
- 紧接着 11:14:27 真正的 ci-int.fail 进来正确处理

late / replay 信号被状态机过滤是设计目标，**实战验证通过**。

### 5. AI agent 工作质量
按 session log 抽样：

| agent | 行为 | 评价 |
|---|---|---|
| analyze | 写了 proposal.md / design.md / tasks.md skeleton，3 stage section 齐 | ✓ 按 prompt |
| contract-spec | 写 contract.spec.yaml + tests/contract/ scenarios | ✓ |
| acceptance-spec | 写 FEATURE-A* Given/When/Then | ✓ |
| dev | 实现 health endpoint + unit test | ✓ |
| ci-runner (unit) | pass | ✓ |
| ci-runner (int) | fail，**准确定位 docker version 问题**：`容器内 docker 版本过旧，不支持 docker compose 子命令（缺少 compose plugin）` | ✓ 报告员该有的样子 |
| bugfix-agent (round-1) | 诊断 = "TEST BUG — CI 容器 Docker 20.10.24 缺 docker compose V2 插件" | ✓ 诊断准，分类错（无 env-bug 选项） |
| test-fix-agent | "测试代码本身没问题，3 个 contract test 都正确。**根因：不是 test bug，是容器环境问题** —— 缺 docker compose v2 + Go 版本不匹配。GitHub Actions CI 不受影响" | ✓✓ 诚实承认非测试问题，没瞎改 |
| reviewer | winner=dev / result:pass（因为没真改东西，merge 哪边都一样）| ✓ |

**最积极的发现**：agent 没有为了走通流程而胡乱"修复"。test-fix-agent 主动标注"这不是 test bug"——这种**保留判断**是无人值守系统最重要的品质之一。

---

## 没走通的事（缺陷 + 修法）

### Bug A：sisyphus runner 镜像缺 docker compose v2

**根因**：`runner/go.Dockerfile` 用 `apt install docker.io` → Debian 给的是 Docker 20.10，没有 `docker compose` plugin。

**影响**：被测项目 Makefile 用 `docker compose -f tests/contract/docker-compose.yml up` → command not found → ci-int 必挂。

**修法**：改走 Docker 官方仓库装 `docker-ce + docker-ce-cli + containerd.io + docker-compose-plugin + docker-buildx-plugin`，跟生产 CI runner 对齐。
（commit: 本次 PR 的 runner Dockerfile 改造）

### Bug B：state 机缺 env-bug 逃生口

**根因**：bugfix-agent 的 diagnosis 类别只有 `code-bug / test-bug / spec-bug`，环境问题没归处。
agent 被迫归到 test-bug → test-fix-agent → reviewer.pass → 重跑同环境 → 永远循环到 CB_THRESHOLD=3 才熔断。

**影响**：环境问题 8-10 分钟才被熔断到 ESCALATED，浪费 3 轮 round（每轮 ~5 min agent 时间）。

**修法**：
- `Event.BUGFIX_ENV_BUG = "bugfix.env-bug"`
- `(BUGFIX_RUNNING, BUGFIX_ENV_BUG) → ESCALATED, action=escalate`
- router 识别 `diagnosis:env-bug` tag
- bugfix.md.j2 prompt 加 ENV BUG 类别说明
（commit: 本次 PR 的 state 机改造）

### Bug C：late event 默认行为是 skip，不归档

`engine.illegal_transition` 当前只 debug log + return skip。late event 出现说明：
- agent session 重启（BKD 自身行为）
- webhook 重投递（已经有 dedup 但偶尔漏）
- 流程时序错乱

**当前状态**：不影响业务，但失去了分析机会。

**未来改进**：把 illegal_transition 也写一行 `event_log.kind = 'illegal_transition'`，便于事后统计。
（暂不修，先观察发生频率）

---

## 数据维度

### 时间分布（前 9 步 happy path）
```
intent → analyzing       4m20s
analyzing → specs-running  4m34s    (analyze 写 4 文件)
specs-running × 2 → dev-running  7m33s  (双 spec 并发完成)
dev-running → ci-unit    4m56s   (dev 写 health endpoint)
ci-unit → ci-int        2m24s   (单元测试 pass 快)
ci-int → bugfix          2m      (ci-int fail，sisyphus 立即派 bugfix)
bugfix → test-fix        2m24s
test-fix → reviewer      5m35s
reviewer → ci-int        3m18s
```
**单次完整链路（不含重试）≈ 36 分钟**。Reviewer pass 重跑 ci-int 在 cache 暖的容器里只用 2 min。

### 资源
- vm-node04 docker container 1 个，~1.5GB image + ~500MB workspace volume
- orchestrator pod 内存 ~80MB / CPU 几乎 0（事件驱动）
- postgresql ~150MB
- 整 REQ 全程 sisyphus 侧资源消耗可忽略不计

### Issue 计数（16 个）
| stage | 数量 |
|---|---|
| analyze / contract-spec / acceptance-spec | 3（前置） |
| dev / ci-unit / ci-int | 3 |
| bugfix / test-fix / reviewer | 各 2 |
| github-incident | 2（每轮 ci-int.fail 一个） |
| ci-int 重跑 | 2（reviewer.pass 后） |

---

## 其他观察

1. **dev-agent 没把 commit push 触发 CI**：观察来看，dev/ci 阶段使用 `make ci-unit-test` 在容器内跑，没去 push GitHub PR。这跟我们设计的"转测前提 PR，CI 流水打镜像"不符。意味着当前流没真正走"build image push GHCR"那段。后续要补。
2. **GHA 流水线打镜像 + image-tag tag** 的回写机制还没实战。dev 阶段 push commit 触发 GHA build，agent 要写 `image-tag:REQ-N-sha-X` 到 dev issue tags —— 这条还没接上。等 lab 验收时一起补。
3. **runner 镜像 cache hit** 让 round-2 ci-int 只 2 分钟，证明容器 per-REQ 模式收益明显。
4. **observability "escalated" issue 噪音** （`emtdci7r` n8n 时代残留）已被 webhook 早期 noise filter 静默 skip，符合预期。

---

## 待办

- [x] 修 runner/Dockerfile + go.Dockerfile，装 docker-compose-plugin
- [x] state 机加 env-bug 分支
- [x] bugfix.md.j2 prompt 加 env-bug 类别说明
- [ ] 验证 fix：新 image build 完后再触发一个测试 REQ，确认 ci-int 能 pass
- [ ] 接 dev-agent push PR + GHA build image + 写 image-tag tag 链路
- [ ] 接 ttpos-arch-lab 验收链路（accept-running stage）
- [ ] 把 illegal_transition 也写入 event_log（统计 late event 频率）
- [ ] 看是否要给 escalated REQ 自动开 GitHub issue（目前只在 incident 时开）

---

## 结论

**state 机 + 容器 + observability 三层基础设施可信**。
**AI agent 工作质量好于预期**——不胡乱修，老实承认能力边界。
**剩余的事是把缺的工程位补全（runner 镜像、image-tag 链路、accept lab）**，不需要再动核心架构。
