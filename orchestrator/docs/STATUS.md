# Sisyphus 当前状态报告

**截至**：2026-04-21（REQ-953 跑完 + REQ-969/975 端到端验证后）

---

## 一句话

**研发主链（intent → analyze → spec → dev → ci-unit → ci-int）已端到端跑通**，其中**死锁修复路径**首次实战通过。**验收阶段（accept）和 PR 自动归档（done_archive）依赖外部组件未接，临时用 SKIP_ACCEPT 开关绕过**。

---

## 能做到什么程度

### ✅ 已经稳定工作

1. **状态机调度**
   - 14 状态 × 18 事件，CAS 推进，并发安全
   - reviewer.pass → CI_INT 重跑（旧 n8n 死锁路径）实战通过
   - bugfix → test-fix → reviewer 子链工作
   - SESSION_FAILED 任意 running 状态 → ESCALATED 兜底
   - late event / replay 事件优雅 skip 不破坏状态
   - 4 类 bugfix 诊断（code/test/spec/env-bug），后两个走 ESCALATED

2. **运行时隔离 + cache 复用**
   - 每 REQ 一个 docker container（sisyphus-runner-go），跨所有 stage 复用
   - named volume `sisyphus-workspace-$REQ` 持久 git checkout 与构建产物
   - DinD（vfs storage driver，K3s nested 安全）
   - 容器在 done/escalated 时 cleanup（done_archive prompt 末尾 + cron 兜底）

3. **可观测性**
   - `event_log` 全量记录 webhook.received / router.decision / action.executed / dedup.hit
   - `bkd_snapshot` 每 5 min 镜像 BKD project 全量 issue
   - structlog JSON 输出，kubectl logs 可查
   - 多项目天然支持（snapshot 扫 distinct project_id）

4. **入站 / 出站认证**
   - 入站：`Authorization: Bearer <secret>`，BKD `secret` 字段自动转发
   - 出站：BKD MCP 用 `Coder-Session-Token`

5. **部署**
   - Bitnami Postgres 持久 20Gi
   - orchestrator helm release（含 ingress）
   - GHA workflow build push GHCR：orchestrator + runner（go / full 两 flavor）
   - schema migration via yoyo（startup 自动 apply）

### ⚠️ 跑得通但有限制

6. **Agent 实际工作质量**（按 REQ-953 + REQ-969 实测）
   - analyze-agent：写出 proposal/design/tasks 三件套 ✓
   - contract-spec-agent：写 OpenAPI spec + Go 契约测试，自检 lint 通过 ✓
   - acceptance-spec-agent：写 5 个 FEATURE-A 场景（Given/When/Then） ✓
   - dev-agent：实现业务代码 + unit test，commit push ✓
   - ci-runner-agent：跑 make ci-target，准确报告 stderr_tail
   - bugfix-agent：诊断准确（包括环境问题），不胡乱修
   - test-fix-agent：能诚实承认"非测试问题"
   - reviewer-agent：选胜分支 merge

   **总评**：超预期。agent 不为完成流程而硬改东西。

7. **dev 阶段镜像构建链路**（部分通了）
   - dev-agent push 触发 GHA build push image 到 GHCR ✓
   - dev-agent 写 `image-tag:<value>` 回 BKD issue tags ✓
   - **未接**：sisyphus 还没读这个 tag 在 accept stage 用

### ❌ 未实现

8. **accept 阶段（验收）**
   - ttpos-arch-lab K3s helm 部署链路未接
   - sisyphus 没 RBAC 在 K3s 内 helm install
   - 临时方案：`SISYPHUS_SKIP_ACCEPT=true` 直接 emit accept.pass
   - 后续要接：sisyphus orchestrator 装 helm CLI + git clone ttpos-arch-lab + 调 K8s API helm install + 把 svc DNS 写进 accept-agent prompt

9. **done_archive PR 创建**
   - prompt 在 container 里跑 `gh pr create`，理论可以，但还没真验证过
   - 需要 vm-node04 docker container 内 gh CLI 有 GITHUB_TOKEN

10. **escalated 后续处理**
    - 当前只在 intent issue 加 tag `escalated reason:<x>`，没自动通知
    - 没自动开 GH incident issue（除非 ci-int.fail / accept.fail 触发 open_gh_and_bugfix）

### 🐛 已发现待修

11. **vm-node04 磁盘紧** — 49GB 共享，runner image 1.5GB + workspace volume + DinD image cache，跑几个并发 REQ 就紧张
12. **dev-agent 的 image-tag 命名约定** dev prompt 和 GHA build tag 命名要保证 sisyphus accept 能反查（已修双 REQ- 前缀，但格式还需对齐 `<service>:REQ-N-sha-X`）
13. **runner image 没 push 到 cache 池** — 每次 REQ 第一个 stage 都 docker pull，慢

---

## REQ 实测时间分布

| 阶段 | REQ-953 | REQ-969 | REQ-975 (in flight) |
|---|---|---|---|
| analyze | 4m20s | 5m23s | 4m47s |
| spec ×2 | 7m33s | 8m21s | 在跑 |
| dev | 4m56s | 10m31s | TBD |
| ci-unit | 2m24s | 2m26s | TBD |
| ci-int | 2m+fail | 5m+session-fail | TBD |
| **happy-path 总计** | ~30 min | ~35 min | TBD |
| bugfix loop（每轮）| ~12 min | N/A | N/A |

---

## 关键修过的 bug

| 日期 | 现象 | 根因 | 修复 commit |
|---|---|---|---|
| 04-21 | wheel install 找不到 migrations | parents[2] 在 site-packages 走偏 | b8a47f6 |
| 04-21 | yoyo 不认 postgresql+psycopg2 | 自创 scheme | bf5e8c2 |
| 04-21 | yoyo 把 -- !rollback 当 forward 跑 DROP | yoyo SQL 不支持内联 rollback | da02198 |
| 04-21 | cas_transition placeholder 计数错 | SQL 4 个占位符传 5 args | ecc4b6f |
| 04-21 | snapshot ISO datetime parse fail | asyncpg 不接 string | 8f80ee7 |
| 04-21 | ci-int parent:unknown | _infer_parent_stage 缺 ci 类 | a8e9f94 |
| 04-21 | webhook GET 405、无 auth POST 422 而非 401 | FastAPI body 校验跑在 auth 前 + 缺 GET | 9c75694 |
| 04-21 | runner 镜像缺 docker compose v2 | apt docker.io 是 20.10 | df144d4 |
| 04-21 | bugfix 无 env-bug 类别 死循环 | state 机缺 env-bug | df144d4 |
| 04-21 | DinD overlay2 在 K3s nested 失败 | 嵌套 overlayfs 内核冲突 | 6894847 |
| 04-21 | dev-agent image-tag 双 REQ- 前缀 | dev.md.j2 typo | 6894847 |

---

## 下一步推荐顺序

1. **接 ttpos-arch-lab accept 链路** —— 是当前最大缺口，工程量约 1-2 天：
   - sisyphus orchestrator 镜像装 helm CLI + git
   - 加 K8s ServiceAccount RBAC（namespace create/delete + helm install rights）
   - 启动时 git clone ttpos-arch-lab 进 /charts/
   - create_accept 改成调 helm install + 等 ready + 开 BKD accept issue 给 svc DNS
   - done_archive 末尾 helm uninstall lab namespace
2. **完善 image-tag 链路** —— dev-agent push 后 GHA 真 build/push 验证 + sisyphus accept 读 tag → helm `--set image.tag=...`
3. **vm-node04 磁盘扩展或挂独立卷** —— 持续跑会爆
4. **写 dev prompt 验证 PR 真创了** —— 当前 dev-agent 是否真触发 GHA 没系统验证

---

## 临时开关

```yaml
# helm values
env:
  skip_accept: true   # 跳过 accept，直接走 done_archive
```
ttpos-arch-lab 接好后改 false。
