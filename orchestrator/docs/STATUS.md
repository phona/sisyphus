# Sisyphus 当前状态（v0.1.1）

**截至**：2026-04-22

---

## 一句话

研发主链（intent → analyze → spec → dev → ci-unit → ci-int → accept → archive → done）**状态机全部实现** + **运行时基础设施稳定**。v0.1.1 解锁 **ci-int 真跑**（DinD vfs → fuse-overlayfs，单 REQ 磁盘 15GB → 5GB），accept 仍 skip（ttpos-arch-lab 未接）。REQ-997 验证 28m6s 全链路跑通，详见 [REQ-997-postmortem.md](runs/REQ-997-postmortem.md)。

---

## 能做到什么程度

### ✅ 已稳定工作

1. **状态机**：14 状态 × 18 事件，CAS，死锁路径（reviewer.pass → 重跑 ci-int）实战通过；late-event / session.failed / CB_THRESHOLD 熔断全部验过；env-bug / spec-bug 分支加入

2. **运行时**：per-REQ 一个 docker container（sisyphus-runner-go），named volume，cross-stage cache 复用，done_archive prompt 末尾清理

3. **镜像**：GHCR 双 flavor
   - `ghcr.io/phona/sisyphus-runner-go:main` — Go 1.23 + docker-ce + compose-plugin v2 + DinD（**fuse-overlayfs**） + openspec 1.3.1 + sisyphus scripts（~1.5GB）
   - `ghcr.io/phona/sisyphus-runner:main` — +Flutter SDK（Flutter 项目备用）

4. **部署**：Bitnami PG 20Gi persistent + orchestrator helm + ingress + GHA 自动 build push + yoyo 迁移（startup apply）

5. **auth**：`Authorization: Bearer <secret>`（BKD `secret` 字段自动转发到 `Authorization`）；webhook 端 + admin 端一致

6. **观测数据采集**：
   - `event_log`（sisyphus_obs）webhook.received / router.decision / action.executed/failed / dedup.hit 全量
   - `bkd_snapshot`（sisyphus_obs）5 min 镜像 BKD 全 project issue
   - `structlog` JSON → kubectl logs / Loki

7. **观测指标 view**：
   - 主库：`req_summary / req_latency / stage_stats / failure_mode`
   - obs 库：`agent_quality / bugfix_diagnosis / suspicious_sessions`

8. **Admin / 调试能力**：
   - `GET /admin/metrics` — 6 段 JSON 系统健康
   - `GET /admin/req/{id}` — 单 REQ 详情（state / history / ctx）
   - `POST /admin/req/{id}/emit` — 手工注入事件
   - `POST /admin/req/{id}/escalate` — 强制止损
   - per-stage SKIP flags + TEST_MODE 全跳 20s 验状态机

### ⚠️ v0.1.1 仍 skip 的一段

- **accept 跳过**：ttpos-arch-lab helm 集成未接（lab chart + RBAC + 数据快照 PVC 生命周期都没写）

### ✅ v0.1.1 解锁的能力

- **ci-int 真跑**：DinD storage driver 从 vfs 换 fuse-overlayfs（用户态 overlay，3-5x 省空间，单 REQ 峰值 15GB → 5GB）；vm-node04 49GB 能撑 3-4 并发
- **openspec CLI 修包名**：`@fission-codes/openspec`（不存在）→ `@fission-ai/openspec` 1.3.1（真包），spec / done_archive 阶段不再 `command not found`
- **BKD 上游 issue 自动 done**：webhook 收到 `session.completed` 且识别成有效事件就推 done，BKD UI 不再卡 review 残骸，`agent_quality.review_count` 数据准确

### ❌ 已知缺陷（下一版改进）

1. **spec-agent 自欺**：contract-spec-agent 能在 tests/contract/*.go 里塞 mock handler 自测（REQ-969 实证），prompt 硬约束未加
2. **done_archive PR** 未系统验证（agent 自述 PR #16 创建，没人手工翻内容）
3. **dev → GHA build image → image-tag 链路** 部分通（dev-agent 会写 tag，但 image 内容没验）
4. **无成本监控**：`event_log.token_in/out` schema 有但没人写
5. **无告警**：escalated / failure 发生不自动通知
6. **vm-node04 磁盘 49GB** 偏紧（fuse-overlayfs 后并发 3-4 OK，再多还是会挤）
7. **specs-running stage_stats self-loop 失真**：每次 `spec.done` 都 self-loop 进 specs-running，`avg_sec` 算成单次 spec 而非整 specs 阶段（view 设计要么按 from_state 算，要么排除 self-loop）

---

## 当前基线数据（截至 v0.1.0）

基于真实跑过的 REQ-945/953/969/975/983/990/991（部分实测 + 部分 test_mode）：

| agent | 调用次数 | 平均耗时 | 一把过率 | 备注 |
|---|---|---|---|---|
| analyze | 15 | ~18 min | 100% | 最慢稳定 stage |
| contract-spec | 5 | ~17 min | 100% | |
| acceptance-spec | 4 | ~11 min | 100% | |
| dev | 7 | ~17 min | 100% | |
| ci (unit/int) | 20 | ~11 min | 100% | 含 fail，first_pass 逻辑简单 |
| bugfix | 4 | ~13 min | 33% | 低一把过率：正常（bugfix 就是要反复）|
| test-fix | 6 | ~32 min | 25% | **最慢最差**，要盯 |
| reviewer | 7 | ~13 min | 0% | 定义上就不会 first-pass |
| accept | 4 | ~16 min | 100% | REQ-945 真跑，其他 skip |
| done-archive | 12 | ~14 min | 100% | |

bugfix 归因分布：test-bug 2, code-bug 1, no-diagnosis 1（env-bug 用例还没实际触发）

---

## 部署信息

- **K3s namespace**: `sisyphus` on vm-node04
- **Ingress**: `sisyphus.43.239.84.24.nip.io`（nip.io 公网可达，BKD 能回调）
- **Webhook 端点**: `POST /bkd-events`（一个端点收所有事件）
- **Admin 端点**: `/admin/metrics`, `/admin/req/{id}/*`
- **BKD webhook 已注册**：id `01KPQFZVYWMSY7AK733BM5XNH2`（secret=Bearer，events=issue.updated+session.completed+session.failed）

---

## 关键 commit / milestone

| commit | 说明 |
|---|---|
| `d5e791b` | 初版 orchestrator 骨架（state + router + actions + prompts + bkd）|
| `b8a47f6` / `bf5e8c2` / `da02198` | yoyo 迁移 + schema fix 系列 |
| `ecc4b6f` | asyncpg placeholder 错位 fix |
| `a73b6e5` | Bearer auth |
| `61b43d3` | prompts 包进 docker container |
| `df144d4` | docker-compose-plugin + env-bug 分支 |
| `6894847` | DinD vfs storage driver |
| `57aed25` | Go 1.23 bump |
| `5fa662f` | per-stage skip flags + admin endpoints |
| `58c51ae` | engine recursion depth 12 |
| `fe64513` | SQL views（req_latency / stage_stats / failure_mode 等）+ /admin/metrics |
| `04c94bb` | agent_quality / bugfix_diagnosis / suspicious_sessions views |
| `d044537` | v0.1.0 baseline 文档定稿 |
| `4e01d15` | DinD storage vfs → fuse-overlayfs（解锁 ci-int）|
| `2f78eae` | openspec npm 包名 fix（@fission-codes → @fission-ai）|
| `0ed0b36` / `1e0d3b0` | webhook 自动推上游 BKD issue done |

---

## 下一步推荐顺序

1. **[P0]** 跑 3-5 个真实需求拿 v0.1.1 真基线（REQ-997 单样本不可信）
2. **[P0]** spec prompt 硬约束（禁 tests/ 内含 handler 实现；RED 测试自检）— 治 spec-agent 自欺
3. **[P1]** ttpos-arch-lab accept 链路（helm + RBAC + chart bootstrap + 数据快照 PVC 生命周期）— 放开 accept
4. **[P1]** escalated / failure → Lark / email webhook 通知
5. **[P2]** admin emit 补 4 个未触发 transition（accept.fail / spec-bug / env-bug / reviewer.fail）
6. **[P2]** event_log 埋 token_in/out（cost 监控）
7. **[P2]** Metabase UI（看板 / 报表）
8. **[P2]** done_archive PR 系统验证（真打开 PR 看代码 + 镜像跑起来）
9. **[P2]** specs-running stage_stats self-loop 修正（按 from_state 算或排除 self-loop）

---

## 文档 / 文件索引

| 文件 | 用途 |
|---|---|
| `docs/RUNBOOK.md` | 日常运维 / 诊断 / 救场 |
| `docs/STATUS.md` | 本文件（当前状态快照）|
| `docs/deployment-pitfalls.md` | 踩过的 10+ 个坑 |
| `docs/runs/REQ-953-postmortem.md` | v0.1.0 首次全链路复盘 |
| `docs/runs/REQ-997-postmortem.md` | v0.1.1 ci-int 真跑首次验证 |
| `observability/README.md` | 观测栈设计（Metabase 计划等）|
| `observability/agent_quality.sql` | agent_quality views SQL |
| `helm/` | Helm chart |
| `runner/` | Runner 镜像 Dockerfile |
