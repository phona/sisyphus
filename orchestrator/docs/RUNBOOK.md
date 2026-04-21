# Sisyphus 运维手册 (v0.1.0 基线)

面向：日常运行、排查、救场。

---

## 入口

- **Ingress**: `http://sisyphus.43.239.84.24.nip.io`
- **K8s namespace**: `sisyphus` on vm-node04 K3s
- **Auth**: 所有 POST 要 `Authorization: Bearer <webhook_token>`（`test123`，可 kubectl 取）

## BKD webhook 配置

```
URL:  http://sisyphus.43.239.84.24.nip.io/bkd-events
events: [issue.updated, session.completed, session.failed]
secret: <webhook_token>    # BKD 自动包成 Authorization: Bearer <secret>
```

## 快速诊断

```bash
# 整体健康一行
curl -sH 'Authorization: Bearer test123' http://sisyphus.43.239.84.24.nip.io/admin/metrics | jq

# 当前有哪些 REQ 在跑
curl -sH 'Authorization: Bearer test123' http://sisyphus.43.239.84.24.nip.io/admin/metrics \
  | jq '.state_distribution'

# 某个 REQ 详情
curl -sH 'Authorization: Bearer test123' http://sisyphus.43.239.84.24.nip.io/admin/req/REQ-N

# 实时日志
kubectl -n sisyphus logs -f deploy/orch-sisyphus-orchestrator
```

---

## skip 开关（`configmap/orch-sisyphus-orchestrator`）

通过 `SISYPHUS_SKIP_<stage>` env 控制某 stage 是否跳过 BKD agent 直接 emit done/pass。

| env | 生产 | 调试常用 | 作用 |
|---|---|---|---|
| `SISYPHUS_SKIP_ANALYZE` | false | false | |
| `SISYPHUS_SKIP_SPEC` | false | false | |
| `SISYPHUS_SKIP_DEV` | false | false | |
| `SISYPHUS_SKIP_CI_UNIT` | false | false | |
| `SISYPHUS_SKIP_CI_INT` | false | **true**（磁盘紧 / DinD vfs 放大）| 跳 ci-integration |
| `SISYPHUS_SKIP_ACCEPT` | false | **true**（ttpos-arch-lab 没接） | 跳 accept |
| `SISYPHUS_SKIP_REVIEWER` | false | false | |
| `SISYPHUS_SKIP_ARCHIVE` | false | false | |
| `SISYPHUS_TEST_MODE` | false | **true**（验状态机 20s 走完）| 全 skip |

改开关：
```bash
kubectl -n sisyphus patch configmap orch-sisyphus-orchestrator --type=merge \
  -p '{"data":{"SISYPHUS_SKIP_CI_INT":"true"}}'
kubectl -n sisyphus rollout restart deploy/orch-sisyphus-orchestrator
```

**当前 baseline 默认**（v0.1.0）：
- SKIP_CI_INT=true（磁盘紧）
- SKIP_ACCEPT=true（lab 没接）
- 其他全 false

---

## 常见救场

### 某 REQ 卡住不动（idle > 15 min）

```bash
# 看 REQ 细节
curl -sH 'Authorization: Bearer test123' .../admin/req/REQ-N

# 手工推进（已知下一步该发什么事件）
curl -X POST -H 'Authorization: Bearer test123' -H 'Content-Type: application/json' \
  -d '{"event":"ci-int.pass"}' .../admin/req/REQ-N/emit

# 强制止损
curl -X POST -H 'Authorization: Bearer test123' .../admin/req/REQ-N/escalate
```

Event 白名单见 `src/orchestrator/state.py` 的 `Event` 枚举，或看 400 错误返回的 `valid`。

### vm-node04 磁盘爆（K3s disk-pressure 驱逐 pod）

症状：`kubectl get pods` 有 Pending / Evicted。

```bash
# vm-node04 上
docker rm -f $(docker ps -aq --filter name=sisyphus-runner)
docker volume rm $(docker volume ls -q | grep sisyphus) 2>/dev/null
docker system prune -af
sudo systemctl restart k3s   # 刷新 disk-pressure taint
```

### Orchestrator pod CrashLoop

```bash
kubectl -n sisyphus logs deploy/orch-sisyphus-orchestrator --previous
# 通常是 PG 连不上 → 查 sisyphus-postgresql-0 状态
# 或 yoyo 迁移失败 → 看 _yoyo_log 表
```

---

## 观测指标速查

所有指标都是 PG view，直接 `psql -U sisyphus -d sisyphus` 或 `sisyphus_obs`：

```bash
# 进 PG
kubectl -n sisyphus exec -it sisyphus-postgresql-0 -- \
  env PGPASSWORD=$(kubectl -n sisyphus get secret sisyphus-postgresql -o jsonpath='{.data.password}' | base64 -d) \
  psql -U sisyphus -d sisyphus
```

**主库 views**（`sisyphus`）：
- `req_summary` — REQ 总览
- `req_latency` — 每 REQ 每 stage 耗时
- `stage_stats` — per-stage avg/p50/p95
- `failure_mode` — escalated 原因分布

**观测库 views**（`sisyphus_obs`）：
- `agent_quality` — per-agent-role 一把过率、平均耗时、pass/fail 计数
- `bugfix_diagnosis` — bugfix-agent 归因分布（code-bug/test-bug/spec-bug/env-bug）
- `suspicious_sessions` — review 状态但无 result tag 的可疑 session

示例 SQL：
```sql
-- 最慢 5 个 stage（按 p95）
SELECT stage, p95_sec FROM stage_stats ORDER BY p95_sec DESC LIMIT 5;

-- agent 一把过率 ranking
SELECT agent_role, first_pass_pct, total_invocations
FROM agent_quality ORDER BY first_pass_pct ASC;

-- bugfix 归因
SELECT * FROM bugfix_diagnosis;
```

---

## 创 REQ（触发流程）

```bash
# 1. 在 BKD UI 创 issue（或 API）
# 2. 加 intent:analyze tag
# 3. sisyphus 收到 issue.updated webhook → 创 req_state → 启 analyze-agent
```

MCP 方式：
```bash
# 见 docs/deployment-pitfalls.md 的 BKD MCP curl handshake
```

---

## 基线配置清单

`helm/values.yaml` 关键项：
```yaml
env:
  skip_ci_int: true
  skip_accept: true
  # 其余全 false
  test_mode: false
observability:
  enabled: true
  snapshotIntervalSec: 300
```

deploy：
```bash
helm upgrade orch ./helm -n sisyphus -f my-values.yaml
```

---

## 已知限制（v0.1.0）

1. **ci-int 跳过** — vm-node04 49GB 扛不住 vfs DinD，先 skip 免得拖挂
2. **accept 跳过** — ttpos-arch-lab 集成未实现
3. **done_archive PR 创建未系统验证** — agent 自述成功，没手动翻过 PR 内容
4. **spec-agent 可能自欺**（mock 测 mock）—— REQ-969 暴露，prompt 硬约束未加
5. **vm-node04 磁盘 49GB 紧** — 连跑 2-3 个并发 ci-int 就爆

修复优先级见 `docs/STATUS.md` 的"下一步推荐顺序"。

---

## 下一版改进方向

等跑完基线数据再决策：
- 扩盘 or fuse-overlayfs → 放开 ci-int
- 接 ttpos-arch-lab → 放开 accept
- spec prompt 硬约束 + 静态检查 → 防 mock 自欺
- event_log `token_in/out` 埋点 → 成本监控
- Metabase UI → 看板可视化
