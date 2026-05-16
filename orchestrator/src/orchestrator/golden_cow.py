"""Golden CoW per-REQ ephemeral 拉起的 ambient context 注入。

详细设计见 ttpos-arch-lab/docs/golden-cow-acceptance-env.md §12（IoC / ambient
context 模式）。本模块是 sisyphus orch 侧的实现：dispatch 时在 per-REQ
namespace 里 apply 一组 k8s 原语，让业务 chart 用默认短名就能"看到"baseline
中间件 + golden snapshot 数据，而完全不感知跨 ns / Longhorn 这些细节。

## 职责

`setup_ephemeral_ns(req_ns, spec)` 一次性把以下塞进 per-REQ ns：

1. **跨 ns import golden VolumeSnapshot** — 取 golden-volumes ns 里 label 匹配
   的最新 VS 的 snapshotHandle，cluster-scope 重建 VolumeSnapshotContent
   (DeletionPolicy=Retain 防 cascade markRemoved source)，target ns 里
   pre-bound VolumeSnapshot 固定命名（让 lab-ephemeral.yaml 引用稳定名字）。

2. **Service + EndpointSlice** — 跟 baseline 中间件同名的 Service（不带
   selector），EndpointSlice 指向 baseline 真 ClusterIP。业务 pod 默认短名
   `{{ .Release.Name }}-<svc>` 会被 k8s DNS 优先解析到本 ns，拿到 ClusterIP
   而不是 FQDN（绕 rocketmq-client-go v2.1.3 不接 FQDN 限制）。

3. **复制 Secret** — 把 baseline 的 ghcr-pull / mariadb auth 等 secret 复制
   到 target ns，让 chart 用同样凭据（不然 chart 重新生成的 secret 跟 golden
   snapshot 里的 mysql.user 表不匹配）。

4. **返回 helm `--set` 列表** — 比如 `erpnext.mariadb.rootPassword=<base64
   decode 的 baseline secret>`，传给 ttpos accept-env.sh 透传给 helm install
   覆盖 chart secret。

## 跟 ttpos accept-env.sh 的契约

ttpos accept-env.sh `cmd_up_ephemeral` 只做 helm install，sisyphus orch 把
extra `--set` 列表通过 env `SISYPHUS_HELM_EXTRA_SETS`（newline-separated 的
key=value）传给 runner。
"""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from kubernetes import client, config
from kubernetes.client import ApiException

from .config import settings

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class GoldenSnapshotRef:
    """一个 golden snapshot 的导入规约。"""
    local_name: str                  # ephemeral ns 内 VS 的固定名（lab-ephemeral.yaml 引）
    source_ns: str                   # golden 所在 ns（通常 golden-volumes）
    source_label_selector: str       # 在 source_ns 选最新 label-match VS 的 selector


@dataclass
class AmbientService:
    """注入到 ephemeral ns 的同名 Service（指向 baseline 真 IP）。"""
    name: str                        # Service 名（跟业务 chart 默认短名一致）
    baseline_ns: str                 # baseline 真 Service 所在 ns
    port: int
    port_name: str = "default"


@dataclass
class SecretCopy:
    """复制 secret 到 ephemeral ns。"""
    name: str
    from_ns: str


@dataclass
class HelmExtraSetFromSecret:
    """从复制进来的 secret 读 key, base64 decode, 输出成 helm --set。"""
    helm_path: str                   # 如 erpnext.mariadb.rootPassword
    secret_name: str                 # 复制后本 ns 的 secret 名
    secret_key: str                  # 如 mariadbRootPassword


@dataclass
class GoldenCowSpec:
    """golden CoW per-REQ 拉起的完整规约。从 yaml 文件加载。"""
    enabled: bool = False
    snapshots: list[GoldenSnapshotRef] = field(default_factory=list)
    ambient_services: list[AmbientService] = field(default_factory=list)
    copy_secrets: list[SecretCopy] = field(default_factory=list)
    helm_extra_sets_from_secret: list[HelmExtraSetFromSecret] = field(default_factory=list)


def load_spec(path: str | Path | None = None) -> GoldenCowSpec:
    """加载 golden CoW spec。path 为 None 时用 settings.golden_cow_spec_path。
    文件不存在 → 返回 enabled=False 的空 spec（safe-disabled）。
    """
    p = Path(path or settings.golden_cow_spec_path)
    if not p.exists():
        logger.info("golden_cow spec %s not found, disabled", p)
        return GoldenCowSpec(enabled=False)
    raw = yaml.safe_load(p.read_text())
    if not raw or not raw.get("enabled", False):
        return GoldenCowSpec(enabled=False)
    return GoldenCowSpec(
        enabled=True,
        snapshots=[GoldenSnapshotRef(**s) for s in raw.get("snapshots", [])],
        ambient_services=[AmbientService(**s) for s in raw.get("ambient_services", [])],
        copy_secrets=[SecretCopy(**s) for s in raw.get("copy_secrets", [])],
        helm_extra_sets_from_secret=[
            HelmExtraSetFromSecret(**s) for s in raw.get("helm_extra_sets_from_secret", [])
        ],
    )


# ──────────────────────────────────────────────────────────────────────────
# K8s clients (lazy init, 复用 k8s_runner 的 in-cluster / kubeconfig 配置)
# ──────────────────────────────────────────────────────────────────────────

_clients_init = False
_core_v1: client.CoreV1Api | None = None
_custom: client.CustomObjectsApi | None = None
_discovery_v1: client.DiscoveryV1Api | None = None
_k8s_lock = asyncio.Lock()


def _ensure_clients() -> None:
    global _clients_init, _core_v1, _custom, _discovery_v1
    if _clients_init:
        return
    if settings.k8s_in_cluster:
        config.load_incluster_config()
    else:
        config.load_kube_config()
    _core_v1 = client.CoreV1Api()
    _custom = client.CustomObjectsApi()
    _discovery_v1 = client.DiscoveryV1Api()
    _clients_init = True


async def _k8s(fn, *args, **kwargs) -> Any:
    """Serialize K8s API calls, run in thread (跟 k8s_runner 同款 pattern)。"""
    _ensure_clients()
    async with _k8s_lock:
        return await asyncio.to_thread(fn, *args, **kwargs)


# ──────────────────────────────────────────────────────────────────────────
# Step 1: cross-ns import golden VolumeSnapshot
# ──────────────────────────────────────────────────────────────────────────

SNAPSHOT_GROUP = "snapshot.storage.k8s.io"
SNAPSHOT_VERSION = "v1"
LONGHORN_DRIVER = "driver.longhorn.io"
LONGHORN_SNAPSHOT_CLASS = "longhorn-snap"


async def _latest_source_snapshot(snap_ref: GoldenSnapshotRef) -> tuple[str, str]:
    """在 source ns 按 label selector 找最新 VolumeSnapshot, 返回 (VSC 名, snapshotHandle)。"""
    res = await _k8s(
        _custom.list_namespaced_custom_object,
        group=SNAPSHOT_GROUP, version=SNAPSHOT_VERSION,
        namespace=snap_ref.source_ns, plural="volumesnapshots",
        label_selector=snap_ref.source_label_selector,
    )
    items = res.get("items", [])
    if not items:
        raise RuntimeError(
            f"golden_cow: no VolumeSnapshot in ns={snap_ref.source_ns} "
            f"matches {snap_ref.source_label_selector}"
        )
    # 按 creationTimestamp 取最新
    items.sort(key=lambda x: x["metadata"]["creationTimestamp"], reverse=True)
    latest = items[0]
    vsc_name = latest.get("status", {}).get("boundVolumeSnapshotContentName")
    if not vsc_name:
        raise RuntimeError(f"golden_cow: latest VS {latest['metadata']['name']} not bound yet")
    vsc = await _k8s(
        _custom.get_cluster_custom_object,
        group=SNAPSHOT_GROUP, version=SNAPSHOT_VERSION,
        plural="volumesnapshotcontents", name=vsc_name,
    )
    handle = vsc.get("status", {}).get("snapshotHandle")
    if not handle:
        raise RuntimeError(f"golden_cow: VSC {vsc_name} has no snapshotHandle")
    return vsc_name, handle


async def _import_snapshot_to_ns(req_ns: str, snap_ref: GoldenSnapshotRef) -> None:
    """在 req_ns 里创 pre-provisioned VSC + VolumeSnapshot,复用 source snapshot handle。

    DeletionPolicy: Retain —— 防止 ns 删除时 cascade 把 source longhorn snapshot
    markRemoved（参见 e2e PoC 撞过的坑）。
    """
    _, handle = await _latest_source_snapshot(snap_ref)
    vsc_name = f"{req_ns}-{snap_ref.local_name}"

    # 创 cluster-scope VolumeSnapshotContent
    vsc_body = {
        "apiVersion": f"{SNAPSHOT_GROUP}/{SNAPSHOT_VERSION}",
        "kind": "VolumeSnapshotContent",
        "metadata": {"name": vsc_name},
        "spec": {
            "deletionPolicy": "Retain",
            "driver": LONGHORN_DRIVER,
            "source": {"snapshotHandle": handle},
            "volumeSnapshotClassName": LONGHORN_SNAPSHOT_CLASS,
            "volumeSnapshotRef": {"name": snap_ref.local_name, "namespace": req_ns},
        },
    }
    try:
        await _k8s(
            _custom.create_cluster_custom_object,
            group=SNAPSHOT_GROUP, version=SNAPSHOT_VERSION,
            plural="volumesnapshotcontents", body=vsc_body,
        )
    except ApiException as e:
        if e.status != 409:
            raise

    # 创 ns-scope VolumeSnapshot 引上面的 Content
    vs_body = {
        "apiVersion": f"{SNAPSHOT_GROUP}/{SNAPSHOT_VERSION}",
        "kind": "VolumeSnapshot",
        "metadata": {"name": snap_ref.local_name, "namespace": req_ns},
        "spec": {
            "volumeSnapshotClassName": LONGHORN_SNAPSHOT_CLASS,
            "source": {"volumeSnapshotContentName": vsc_name},
        },
    }
    try:
        await _k8s(
            _custom.create_namespaced_custom_object,
            group=SNAPSHOT_GROUP, version=SNAPSHOT_VERSION,
            namespace=req_ns, plural="volumesnapshots", body=vs_body,
        )
    except ApiException as e:
        if e.status != 409:
            raise
    logger.info("golden_cow: imported VS %s/%s (handle=%s)", req_ns, snap_ref.local_name, handle)


# ──────────────────────────────────────────────────────────────────────────
# Step 2: ambient Service + EndpointSlice 注入
# ──────────────────────────────────────────────────────────────────────────

async def _inject_ambient_service(req_ns: str, svc: AmbientService) -> None:
    """在 req_ns 创跟 baseline 同名 Service (无 selector) + EndpointSlice 指 baseline IP。"""
    baseline_svc = await _k8s(
        _core_v1.read_namespaced_service, name=svc.name, namespace=svc.baseline_ns,
    )
    baseline_ip = baseline_svc.spec.cluster_ip
    if not baseline_ip or baseline_ip == "None":
        raise RuntimeError(f"golden_cow: baseline svc {svc.baseline_ns}/{svc.name} has no ClusterIP")

    svc_body = client.V1Service(
        metadata=client.V1ObjectMeta(name=svc.name),
        spec=client.V1ServiceSpec(
            ports=[client.V1ServicePort(name=svc.port_name, port=svc.port,
                                         target_port=svc.port, protocol="TCP")],
        ),
    )
    try:
        await _k8s(_core_v1.create_namespaced_service, namespace=req_ns, body=svc_body)
    except ApiException as e:
        if e.status != 409:
            raise

    eps_body = client.V1EndpointSlice(
        api_version="discovery.k8s.io/v1",
        kind="EndpointSlice",
        metadata=client.V1ObjectMeta(
            name=f"{svc.name}-1",
            labels={"kubernetes.io/service-name": svc.name},
        ),
        address_type="IPv4",
        ports=[client.DiscoveryV1EndpointPort(name=svc.port_name, port=svc.port, protocol="TCP")],
        endpoints=[client.V1Endpoint(addresses=[baseline_ip])],
    )
    try:
        await _k8s(_discovery_v1.create_namespaced_endpoint_slice, namespace=req_ns, body=eps_body)
    except ApiException as e:
        if e.status != 409:
            raise
    logger.info("golden_cow: ambient svc %s/%s -> %s:%d", req_ns, svc.name, baseline_ip, svc.port)


# ──────────────────────────────────────────────────────────────────────────
# Step 3: secret 复制
# ──────────────────────────────────────────────────────────────────────────

async def _copy_secret(req_ns: str, sc: SecretCopy) -> dict[str, bytes]:
    """复制 secret 到 req_ns。返回 base64-decoded data（用于 helm --set 取值）。"""
    src = await _k8s(_core_v1.read_namespaced_secret, name=sc.name, namespace=sc.from_ns)
    body = client.V1Secret(
        metadata=client.V1ObjectMeta(name=sc.name),
        type=src.type,
        data=src.data,
    )
    try:
        await _k8s(_core_v1.create_namespaced_secret, namespace=req_ns, body=body)
    except ApiException as e:
        if e.status != 409:
            raise
    logger.info("golden_cow: copied secret %s to %s (from %s)", sc.name, req_ns, sc.from_ns)
    # decode for helm --set use
    return {k: base64.b64decode(v) for k, v in (src.data or {}).items()}


# ──────────────────────────────────────────────────────────────────────────
# 顶层 entrypoint
# ──────────────────────────────────────────────────────────────────────────

async def _ensure_namespace(req_ns: str) -> None:
    """idempotent 创 ns（接 ttpos accept-env.sh 之前先要 ns 存在）。"""
    try:
        await _k8s(
            _core_v1.create_namespace,
            body=client.V1Namespace(metadata=client.V1ObjectMeta(name=req_ns)),
        )
        logger.info("golden_cow: created ns %s", req_ns)
    except ApiException as e:
        if e.status != 409:
            raise


async def setup_ephemeral_ns(req_ns: str, spec: GoldenCowSpec | None = None) -> list[str]:
    """Setup per-REQ ephemeral ns 的 golden CoW ambient context。

    返回 helm extra `--set` 列表（key=value 字符串），由 caller 透传给 ttpos
    accept-env.sh 的 helm install。

    幂等：所有 create 操作遇 409 当 success。
    """
    spec = spec or load_spec()
    if not spec.enabled:
        return []

    # 0. 确保 ns 存在（ttpos accept-env.sh 跑 helm install 之前要的）
    await _ensure_namespace(req_ns)

    # 1. cross-ns import golden snapshots
    for snap in spec.snapshots:
        await _import_snapshot_to_ns(req_ns, snap)

    # 2. ambient Service + EndpointSlice
    for svc in spec.ambient_services:
        await _inject_ambient_service(req_ns, svc)

    # 3. 复制 secret + 收集 base64-decoded data
    secret_data: dict[str, dict[str, bytes]] = {}
    for sc in spec.copy_secrets:
        secret_data[sc.name] = await _copy_secret(req_ns, sc)

    # 4. 拼 helm --set 列表（从复制进来的 secret 读 key）
    extra_sets: list[str] = []
    for hs in spec.helm_extra_sets_from_secret:
        data = secret_data.get(hs.secret_name)
        if data is None or hs.secret_key not in data:
            raise RuntimeError(
                f"golden_cow: helm_extra_sets_from_secret refers "
                f"{hs.secret_name}.{hs.secret_key} but secret/key missing"
            )
        val = data[hs.secret_key].decode("utf-8")
        # helm --set 里 special char 要 escape；用 \= 等
        extra_sets.append(f"{hs.helm_path}={val}")

    logger.info("golden_cow: setup %s done, %d extra --set", req_ns, len(extra_sets))
    return extra_sets


# ──────────────────────────────────────────────────────────────────────────
# GC: 清孤儿 cluster-scope VolumeSnapshotContent
# ──────────────────────────────────────────────────────────────────────────

async def gc_orphan_vsc(active_ns_prefixes: tuple[str, ...] = ("accept-req-",)) -> list[str]:
    """扫所有 cluster-scope VolumeSnapshotContent, 找 volumeSnapshotRef.namespace 已经
    不存在的, 删之。每个 accept_env_gc tick 调一次。返回 deleted VSC 名列表。
    """
    _ensure_clients()
    existing_ns = {
        ns.metadata.name for ns in (await _k8s(_core_v1.list_namespace)).items
    }
    vscs = await _k8s(
        _custom.list_cluster_custom_object,
        group=SNAPSHOT_GROUP, version=SNAPSHOT_VERSION,
        plural="volumesnapshotcontents",
    )
    deleted = []
    for vsc in vscs.get("items", []):
        ref_ns = vsc.get("spec", {}).get("volumeSnapshotRef", {}).get("namespace", "")
        if not any(ref_ns.startswith(p) for p in active_ns_prefixes):
            continue   # 不是 accept-req-* 的 VSC（golden-volumes 之类的留着）
        if ref_ns in existing_ns:
            continue   # ns 还活着
        name = vsc["metadata"]["name"]
        try:
            await _k8s(
                _custom.delete_cluster_custom_object,
                group=SNAPSHOT_GROUP, version=SNAPSHOT_VERSION,
                plural="volumesnapshotcontents", name=name,
            )
            deleted.append(name)
        except ApiException as e:
            if e.status != 404:
                logger.warning("golden_cow: gc VSC %s failed: %s", name, e)
    if deleted:
        logger.info("golden_cow: gc'd %d orphan VSC: %s", len(deleted), deleted)
    return deleted
