# REQ-997 全链路实测报告（v0.1.1 验证跑）

**目的**：验证 v0.1.0 → v0.1.1 三项改动后，能不能解开 `SKIP_CI_INT` 跑通到 accept 之前。
**结论**：**全链路跑通，28m 6s**，所有改动生效。

---

## v0.1.0 → v0.1.1 改了什么

| 改动 | commit | 解决什么 |
|---|---|---|
| DinD storage driver: vfs → fuse-overlayfs | `4e01d15` | 嵌套 docker 空间放大 10x → 1.2x，单 REQ 峰值 15GB → 5GB，可开 ci-int |
| openspec npm 包名修正 `@fission-codes/openspec` → `@fission-ai/openspec` | `2f78eae` | 旧 Dockerfile 装的 `openspec@0.0.0` 是占位空包，agent 报 `command not found` |
| webhook 自动推上游 BKD issue 到 done | `0ed0b36` + `1e0d3b0` | 之前 dev/ci-unit/ci-int/done-archive issue 永远卡 `review`，BKD UI 乱、`agent_quality.review_count` 失真 |

---

## REQ-997 时间线

| 时间偏移 | 状态转移 | 事件 | 阶段耗时 |
|---|---|---|---|
| 0s | `init` → `analyzing` | `intent.analyze` | — |
| +343s | `analyzing` → `specs-running` | `analyze.done` | **5m 43s** |
| +301s | spec 1 完成（contract 或 accept） | `spec.done` | 5m 1s |
| +239s | spec 2 完成 | `spec.done` | 4m（并行） |
| +0s | `specs-running` → `dev-running` | `spec.all-passed` | 立即 |
| +300s | `dev-running` → `ci-unit-running` | `dev.done` | **5m** |
| +101s | `ci-unit-running` → `ci-int-running` | `ci-unit.pass` | **1m 41s** |
| +224s | `ci-int-running` → `accept-running` | `ci-int.pass` | **3m 44s** ← fuse-overlayfs 真跑 |
| +0s | `accept-running` → `archiving` | `accept.pass` | skip（即时） |
| +174s | `archiving` → `done` | `archive.done` | **2m 54s** |

**总：1686s = 28m 6s**

---

## 对比 v0.1.0 基线

基线数据来自 `STATUS.md` v0.1.0 的 7 个真跑 REQ：

| stage | v0.1.0 平均 | REQ-997 实测 | 变化 |
|---|---|---|---|
| analyze | ~18 min | 5m 43s | **3x 快** |
| contract-spec | ~17 min | 5m 1s | 3x 快 |
| acceptance-spec | ~11 min | 4m（并行）| 2.5x 快 |
| dev | ~17 min | 5m | 3x 快 |
| ci-unit | ~11 min | 1m 41s | 6x 快 |
| ci-int | (skipped) | **3m 44s 真跑** | 首次成功 |
| done-archive | ~14 min | 2m 54s | 5x 快 |

**为什么这么快？**
- 需求极简（"测试添加功能"，几乎是空 intent）
- agent 短路了大部分实质工作
- runner image cache hit 高（同一 vm-node04 之前跑过类似 REQ）

**不能直接当 v0.1.1 真实基线**。要 3-5 个真实需求跑完取均值才有代表性。

---

## fuse-overlayfs 实测验证

跑 REQ-997 之前先在 vm-node04 spawn 了一个 throw-away 容器验：
```
Server Version: 29.4.1
Storage Driver: fuse-overlayfs
hello-world test → success
```

REQ-997 跑完后磁盘 check：
- 跑前：vm-node04 `/` 21GB used / 49GB
- 跑后：~25GB used（涨 ~4GB），符合 fuse-overlayfs 单 REQ ~5GB 预期
- vfs 路线下同样负载会涨 ~15GB

**P0 改造结论**：换 fuse-overlayfs 是对的，代价 0 行业务改动。

---

## openspec CLI 修复

### 暴露过程
- REQ-997 在 analyze 阶段 agent 报 `openspec: command not found`
- 查发现 Dockerfile 里 `npm install -g @fission-codes/openspec` 仓库不存在 → fallback 到 `npm install -g openspec` → npm 上 `openspec@0.0.0` 是 2019 年占位空包，没 CLI binary
- 真包名是 `@fission-ai/openspec`（OpenSpec by Fission AI，41k★ TypeScript）

### 修复
- Dockerfile 改用真包名，去掉 `||` fallback 让 build fail 暴露问题
- 当前运行的 REQ-997 容器手动 `npm install -g @fission-ai/openspec@latest`，version 1.3.1
- 新 image 已 GHA build 完，下个 REQ 自动用，不用手装

### 反思
- `2>/dev/null || ... || echo "..."` 这种"装失败也算成功"的写法掩盖了根本问题，跑了 6 个 REQ 没人发现
- 之前 REQ 能跑完是因为 analyze prompt 不直接调 openspec CLI（只写文件结构），spec / done_archive prompt 有调用，但 agent 可能忽略了 `command not found`

---

## webhook 上游 issue 自动 done

### 现象
REQ-997 跑完后看 BKD UI，发现 dev / ci-unit / ci-int / done-archive 这 4 个 BKD issue 全卡在 `review` 状态。
查 actions 代码：只有 `fanout_specs`（推 analyze done）和 `mark_spec_reviewed_and_check`（推 spec done）会主动更新上游 BKD issue 状态，其他 5 个 create_X action 都不推。

### 影响
- BKD UI 乱：跑一把累 4-5 个 review 残骸，多跑几个 REQ 完全看不清当前状态
- `agent_quality.review_count` view 永远只增不减
- 排查时 BKD UI 看着像「卡住」实际 sisyphus 早走了

### 修复
集中在 webhook 处理：`session.completed` 且 `derive_event` 识别成有效事件 → 推当前 issue 到 `done`。
重推 done 是幂等的，不冲突已有 action 的 done 推送。`session.failed` 不推（保留人工排查）。

10 行代码 + 2 个单测，全套 110 测试通过。`1e0d3b0` rollout 后下个 REQ 起自动生效。

---

## 实测中暴露的其他小问题

1. **specs-running state 的 stage_stats 有 6 个 enter_count**：因为 `mark_spec_reviewed_and_check` 每次 spec.done 都 self-loop 进 specs-running。这导致 `specs-running` 平均耗时被算成单次 spec 而不是整个 specs 阶段。view 设计要么按 from_state 算，要么把 self-loop 排除。**不阻塞，下个 view 升级修**。

2. **`accept-running` count=2 但 avg=0**：因为 SKIP_ACCEPT=true 时 emit `accept.pass` 是即时的（0ms 滞留）。view 数据**正常**，符合预期。

3. **stage_stats 没有 `done`/`escalated` 终态行**：终态没下一次 transition，自然算不出 latency。**符合预期**。

---

## v0.1.1 基线（暂定）

基于 1 把 REQ-997（注意：单样本，不能当真实生产基线）：

| 配置 | 值 |
|---|---|
| `SISYPHUS_SKIP_CI_INT` | **false** ✅（解锁）|
| `SISYPHUS_SKIP_ACCEPT` | true（lab 没接）|
| 其他 SKIP | false |
| runner storage driver | fuse-overlayfs |
| runner image | `ghcr.io/phona/sisyphus-runner-go:main` (sha `339485d5`) |
| openspec CLI | `1.3.1`（烘进 image）|
| vm-node04 磁盘 | 25GB / 49GB used |

### 状态机覆盖率（17 个 transition）

v0.1.0 是 13/18 stable + 2/18 single-sample + 4/18 未触发。
REQ-997 让 `ci-int.pass → accept` 和 `accept.pass → archiving` 从 single-sample 变 2-sample，**真实 ci-int 路径首次跑通**。

剩下未触发的 4 个还得专门构造（admin emit）：
- `accept.fail → bugfix`
- `bugfix.spec-bug → escalated`
- `bugfix.env-bug → escalated`
- `reviewer.fail → escalated`

---

## 下一步推荐

| 优先级 | 项目 | 理由 |
|---|---|---|
| P0 | 跑 3-5 个真实需求拿真基线（v0.1.1 真值）| 单样本不可信 |
| P1 | ttpos-arch-lab accept 集成 | 解锁最后一个 SKIP，真正全链路 |
| P1 | spec-agent 自欺约束（prompt 硬约束 + 静态检查）| REQ-969 暴露未修 |
| P2 | admin emit 补 4 个未触发 transition | 状态机死角 |
| P2 | escalated → Lark/email 通知 | 卡住自动告警 |
| P2 | event_log token_in/out 埋点 | cost 监控 |

---

## 文档文件索引

- 本文件：`docs/runs/REQ-997-postmortem.md`
- 上一版基线：`docs/STATUS.md`（更新到 v0.1.1）
- 运维：`docs/RUNBOOK.md`（更新 SKIP_CI_INT 默认 false）
- 踩坑：`docs/deployment-pitfalls.md`（新增 #11 DinD storage driver）
