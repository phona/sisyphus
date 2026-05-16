# Golden CoW orch 集成

> 业务侧契约 + chart 实现：见 [ttpos-arch-lab/docs/golden-cow-acceptance-env.md](https://github.com/ZonEaseTech/ttpos-arch-lab/blob/main/docs/golden-cow-acceptance-env.md)
> 设计哲学：IoC / ambient context — 业务 chart 不感知 baseline / Longhorn / 跨 ns，sisyphus orch 在 accept ns 注 k8s 原语"伪装"拓扑。

## 1. 责任分层

```
arch-lab repo                   ← chart owner，提供 lab-ephemeral.yaml profile
sisyphus orch                   ← ★ 本文档：注入 ambient context
ttpos accept-env.sh             ← 5 行透传 env, --values lab-ephemeral.yaml
```

## 2. orch 实现

`orchestrator/src/orchestrator/golden_cow.py`，新模块（Phase A，~280 行）。

`setup_ephemeral_ns(req_ns, spec)` 一次性做 4 件事：

| 步骤 | 干啥 | 防什么坑 |
|---|---|---|
| 1. cross-ns import VolumeSnapshot | 取 `golden-volumes/golden-*` 最新 VS 的 `snapshotHandle`，cluster-scope 重建 VSC（`DeletionPolicy: Retain`）+ ns-scope VS（固定本地名让 chart 引用） | **必须 Retain**：之前 e2e PoC 用 `Delete` 导致 ns 删时 cascade 把 source longhorn snapshot `markRemoved=true`，所有后续 PVC create 失败 |
| 2. ambient Service + EndpointSlice | 跟 baseline 中间件同名的 Service（无 selector）+ EndpointSlice 指 baseline 真 ClusterIP | 业务 chart 用默认短名连，DNS 优先本 ns 解析拿 ClusterIP（是 IP 不是 FQDN，绕 rocketmq-client-go v2.1.3 不接 FQDN 限制） |
| 3. 复制 secret | 把 baseline `*-erpnext-auth` / `ghcr-pull` 复制到 req ns | chart 重新生成的 password 跟 golden snapshot 里 `mysql.user` 不匹配 |
| 4. 拼 helm `--set` 列表 | 从复制进来的 secret 读 key，base64 decode，输出 `key=value` 字符串 | chart `mariadb.rootPassword` 默认 `testroot` 跟 golden 不一致；必须 override |

返回 list[str] = `["erpnext.mariadb.rootPassword=<baseline_pwd>", ...]`。

## 3. 跟 ttpos accept-env.sh 的契约

orch 把第 4 步返回的 list 通过 env `SISYPHUS_HELM_EXTRA_SETS`（newline-separated）
传给 runner pod。`make accept-env-up` 入口的 `accept-env.sh cmd_up_ephemeral`：

```bash
EXTRA=""
IFS=$'\n'
for kv in ${SISYPHUS_HELM_EXTRA_SETS:-}; do
  [ -n "$kv" ] && EXTRA="$EXTRA --set $kv"
done

helm upgrade --install ttpos-arch-lab "$LAB_DIR/charts/edge-lab" \
  --values "$LAB_DIR/values/lab-ephemeral.yaml" \
  $EXTRA \
  --timeout 8m
```

5 行。ttpos 不知道 baseline / longhorn / snapshot 这些事。

## 4. 配置

```yaml
# /etc/sisyphus/golden-cow.yaml (路径见 SISYPHUS_GOLDEN_COW_SPEC_PATH)
enabled: true
snapshots: [...]
ambient_services: [...]
copy_secrets: [...]
helm_extra_sets_from_secret: [...]
```

完整示例见 [orchestrator/config/golden-cow.example.yaml](../orchestrator/config/golden-cow.example.yaml)。

**文件不存在或 `enabled: false`** → orch 跳过 setup，旧 accept 流程不受影响（向后兼容）。

## 5. 集成点

`actions/create_accept.py` 在 runner exec `make accept-env-up` 之前调一次：

```python
from .. import golden_cow

# 在 runner exec 之前
spec = golden_cow.load_spec()
if spec.enabled:
    extra_sets = await golden_cow.setup_ephemeral_ns(namespace_for_pr, spec)
    env["SISYPHUS_HELM_EXTRA_SETS"] = "\n".join(extra_sets)
```

## 6. GC

`golden_cow.gc_orphan_vsc()` 扫所有 cluster-scope VSC，找 `volumeSnapshotRef.namespace`
已经不存在的 → 删。集成进 `accept_env_gc` 每 15min tick。

## 7. Phase 路线

| Phase | 范围 |
|---|---|
| **A**（已写） | `golden_cow.py` 模块 + `setup_ephemeral_ns` + `gc_orphan_vsc` + spec yaml + 集成点 |
| **B**（后做） | `golden_lifecycle.py`：cron 每周自动 dump baseline → snapshot → 4 周后 GC |
| **C**（视情况） | Longhorn BackingImage 优化（snapshot restore 30s → attach <5s） |

## 8. RBAC

orch SA 需要额外 cluster-scope perm：

```yaml
- apiGroups: ["snapshot.storage.k8s.io"]
  resources: ["volumesnapshotcontents"]
  verbs: ["get", "list", "create", "delete"]
- apiGroups: ["snapshot.storage.k8s.io"]
  resources: ["volumesnapshots"]
  verbs: ["get", "list", "create"]
- apiGroups: [""]
  resources: ["services", "namespaces", "secrets"]
  verbs: ["get", "list", "create"]
- apiGroups: ["discovery.k8s.io"]
  resources: ["endpointslices"]
  verbs: ["get", "list", "create"]
```
