"""K8s-based runner pod controller（v0.2 架构）.

每个 REQ 对应 sisyphus-runners namespace 下一个 Pod（runner-<REQ>）+ 一个 PVC
（workspace-<REQ>）。Pod 是 privileged + DinD + fuse-overlayfs 的调试环境，PVC 挂
/workspace 持久保存 clone 的 repos + 中间产物。

生命周期绑 REQ：
  - analyze 起：ensure_runner 幂等创建 PVC + Pod
  - 中途 Pod 重启：K8s restartPolicy=Always 自动拉起（PVC 不动）
  - pause：delete Pod（PVC 留）
  - resume：重建 Pod（PVC 自动重挂）
  - done/escalate 清理：delete Pod + PVC

所有阻塞 K8s API 走 asyncio.to_thread，不堵事件循环。
_k8s_api_lock 序列化全部 core_v1 调用——kubernetes-python 共享 ApiClient 在并发
asyncio.to_thread 下会随机走 ws_client 路径，把正常 HTTP 200 JSON 响应当成
WebSocket 握手失败，抛 ApiException(status=0)。锁住后串行调用彻底规避。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import structlog
from kubernetes import client, config
from kubernetes.client import ApiException
from kubernetes.stream import stream

log = structlog.get_logger(__name__)


# ── module-level singleton（让 actions 拿一个公共 controller）──────────
# main.py startup 时 set_controller 注入；action 调 get_controller 拿。
# 测试场景可直接 set_controller(mock) 替换。
_controller: RunnerController | None = None


def set_controller(controller: RunnerController | None) -> None:
    global _controller
    _controller = controller


def get_controller() -> RunnerController:
    if _controller is None:
        raise RuntimeError("RunnerController 未初始化；main.py startup 应调 set_controller()")
    return _controller


@dataclass(frozen=True)
class ExecResult:
    """kubectl exec 的聚合结果。"""
    exit_code: int
    stdout: str
    stderr: str
    duration_sec: float


@dataclass(frozen=True)
class RunnerStatus:
    """单个 runner 的 pod + pvc 快照。"""
    req_id: str
    pod_name: str
    pvc_name: str
    pod_phase: str   # Pending / Running / Succeeded / Failed / Unknown / NotFound
    pvc_phase: str   # Pending / Bound / Lost / NotFound
    created_at: str | None   # Pod.metadata.creation_timestamp（ISO）


# ── Exit-code marker: 附在 exec 命令末尾，读 stdout 回来 parse 出 exit code ──
# K8s Python client 的 stream() 返回 WSClient，`.returncode` 在不同版本行为不一致，
# 用 marker 最稳。
_EXIT_MARKER = "__SISY_EXEC_EXIT__:"


def _shell_quote(s: str) -> str:
    """POSIX shell 单引号 quote（防 env 值注入）。"""
    return "'" + s.replace("'", "'\\''") + "'"


class RunnerController:
    """封装 K8s API 调用，业务层只跟 req_id 打交道。"""

    def __init__(
        self,
        namespace: str,
        runner_image: str,
        runner_sa: str,
        storage_class: str,
        workspace_size: str,
        runner_secret_name: str,
        image_pull_secrets: list[str] | None = None,
        ready_timeout_sec: int = 120,
        ready_attempts: int = 3,
        in_cluster: bool = True,
        kvm_enabled: bool = False,
        core_v1: client.CoreV1Api | None = None,
    ):
        self.namespace = namespace
        self.runner_image = runner_image
        self.runner_sa = runner_sa
        self.storage_class = storage_class
        self.workspace_size = workspace_size
        # 单一 secret：env 注入 gh_token/ghcr_user/ghcr_token；文件挂载 kubeconfig
        self.runner_secret_name = runner_secret_name
        self.image_pull_secrets = list(image_pull_secrets or [])
        self.ready_timeout_sec = ready_timeout_sec
        # M9：Pod Ready 外层 attempts（每次等 ready_timeout_sec）。超全部 attempts 抛
        # TimeoutError，让 engine.step 的 retry policy 接手决策 retry/escalate。
        self.ready_attempts = max(1, ready_attempts)
        # KVM device passthrough — 启用后 build_pod 会挂宿主 /dev/kvm 进 runner pod，
        # 给 Android emulator 走硬件加速（冷启 ~30s vs 软 CPU 5-10min）。默认 off
        # 兼容没暴露 /dev/kvm 的节点（嵌套虚拟化里跑的 K8s）。
        self.kvm_enabled = kvm_enabled

        # Serializes all core_v1 calls: kubernetes-python ApiClient is not thread-safe
        # when shared across concurrent asyncio.to_thread calls. Without this lock,
        # concurrent callers randomly enter ws_client.websocket_call and treat a normal
        # HTTP 200 JSON response as a failed WebSocket handshake → ApiException(status=0).
        self._k8s_api_lock = asyncio.Lock()

        if core_v1 is not None:
            # 测试注入 mock client
            self.core_v1 = core_v1
        else:
            if in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config()
            self.core_v1 = client.CoreV1Api()

    # ── K8s API serialization helper ────────────────────────────────────

    async def _k8s(self, fn, *args, **kwargs):
        """Serialize K8s API calls through _k8s_api_lock, then run in thread."""
        async with self._k8s_api_lock:
            return await asyncio.to_thread(fn, *args, **kwargs)

    # ── naming helpers ──────────────────────────────────────────────────
    # K8s 名字小写 + `-` / 数字，REQ 本来就是 `REQ-N` 格式，转成 `req-n` 合法

    def pod_name(self, req_id: str) -> str:
        return f"runner-{req_id.lower()}"

    def pvc_name(self, req_id: str) -> str:
        return f"workspace-{req_id.lower()}"

    # ── spec 构造（纯函数，便于 unit test 验）────────────────────────────

    def build_pvc(self, req_id: str) -> client.V1PersistentVolumeClaim:
        return client.V1PersistentVolumeClaim(
            api_version="v1",
            kind="PersistentVolumeClaim",
            metadata=client.V1ObjectMeta(
                name=self.pvc_name(req_id),
                labels={
                    "sisyphus/req-id": req_id.lower(),
                    "sisyphus/role": "workspace",
                },
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                storage_class_name=self.storage_class,
                resources=client.V1VolumeResourceRequirements(
                    requests={"storage": self.workspace_size},
                ),
            ),
        )

    def build_pod(self, req_id: str) -> client.V1Pod:
        pod_name = self.pod_name(req_id)
        pvc_name = self.pvc_name(req_id)

        # 运行时 env：业务 agent 在 prompt 里通过 kubectl exec 动态加更多，
        # 这里只注入 pod 全生命周期都要的
        env_vars = [
            client.V1EnvVar(name="SISYPHUS_REQ_ID", value=req_id),
            client.V1EnvVar(name="SISYPHUS_RUNNER", value="1"),
            # 全局压住 runner pod 内 go 进程内存峰值（pod limit 8 GiB），
            # 业务 Makefile 无感:
            # - GOMEMLIMIT 触发 GC 而非 OOM,硬卡死单进程堆上限
            # - GOGC 更激进 GC,RSS -20-30%,trade CPU 换内存
            # - GOFLAGS 让 go test/build 默认 -p=2,限制并行包数
            # 注意: 仅对 runner pod 内直接执行的 go 进程生效;
            # DinD 子容器内 go (docker compose --build / 容器内 go test) 不受控,
            # 靠 pod cgroup 8 GiB 兜底
            client.V1EnvVar(name="GOMEMLIMIT", value="2GiB"),
            client.V1EnvVar(name="GOGC", value="50"),
            client.V1EnvVar(name="GOFLAGS", value="-p=2"),
            # Toolchain 包/构建/下载 cache 全部重定向到 PVC 挂载点
            # `/workspace/.cache/`。默认位置（GOPATH/pkg/mod = /root/go/pkg/mod、
            # ~/.cache/go-build、~/.npm、~/.cache/uv）落在容器可写层 / ephemeral
            # storage：pod 重启 / 节点压力下被驱逐就丢，每次重新下载 modules
            # 又拖累 dev_cross_check / staging_test 时长，还吃 node /var/lib
            # 空间。PVC 挂的 /workspace 跟 pod lifecycle 解耦（OOM 重启不掉），
            # 跨 stage 的 lint/test 复用同一份 cache。
            # 注：per-REQ PVC 仅 REQ 内共享，不跨 REQ；跨 REQ 共享 cache 是另一
            # 个话题（需要 RWX volume），不在本次范围。
            client.V1EnvVar(name="GOMODCACHE", value="/workspace/.cache/go/mod"),
            client.V1EnvVar(name="GOCACHE", value="/workspace/.cache/go/build"),
            client.V1EnvVar(name="npm_config_cache", value="/workspace/.cache/npm"),
            client.V1EnvVar(name="UV_CACHE_DIR", value="/workspace/.cache/uv"),
        ]
        # 从 runner-secrets 注入 GitHub 凭证（optional，secret 缺了 pod 还能起）
        for env_name, secret_key in (
            ("GH_TOKEN", "gh_token"),
            ("SISYPHUS_GHCR_USER", "ghcr_user"),
            ("SISYPHUS_GHCR_TOKEN", "ghcr_token"),
        ):
            env_vars.append(client.V1EnvVar(
                name=env_name,
                value_from=client.V1EnvVarSource(
                    secret_key_ref=client.V1SecretKeySelector(
                        name=self.runner_secret_name, key=secret_key, optional=True,
                    ),
                ),
            ))

        volume_mounts = [
            client.V1VolumeMount(name="workspace", mount_path="/workspace"),
            # /dev/fuse 是 DinD fuse-overlayfs 必需（v0.1.1 已验）
            client.V1VolumeMount(name="fuse", mount_path="/dev/fuse"),
            # kubeconfig：accept / staging 阶段 agent 要起 helm / kubectl
            client.V1VolumeMount(
                name="kubeconfig",
                mount_path="/root/.kube",
                read_only=True,
            ),
        ]

        volumes = [
            client.V1Volume(
                name="workspace",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=pvc_name,
                ),
            ),
            client.V1Volume(
                name="fuse",
                host_path=client.V1HostPathVolumeSource(
                    path="/dev/fuse", type="CharDevice",
                ),
            ),
            client.V1Volume(
                name="kubeconfig",
                secret=client.V1SecretVolumeSource(
                    secret_name=self.runner_secret_name,
                    optional=True,
                    items=[client.V1KeyToPath(key="kubeconfig", path="config")],
                ),
            ),
        ]

        # /dev/kvm passthrough（Android emulator 硬件加速）—— 见 __init__ kvm_enabled
        # 注释。仅在 operator 显式 opt-in 时挂；missing 时不动 spec，保持现有部署兼容。
        if self.kvm_enabled:
            volume_mounts.append(
                client.V1VolumeMount(name="kvm", mount_path="/dev/kvm"),
            )
            volumes.append(
                client.V1Volume(
                    name="kvm",
                    host_path=client.V1HostPathVolumeSource(
                        path="/dev/kvm", type="CharDevice",
                    ),
                ),
            )

        container = client.V1Container(
            name="runner",
            image=self.runner_image,
            # Always pull —— runner image 是 :main 浮动 tag，IfNotPresent 时节点
            # 缓存的旧 image 永远不会被刷新（实测：PR #34 加 sisyphus-clone-repos.sh
            # + check-scenario-refs --specs-search-path 后，runner pod 仍跑老 script
            # 报 'cd: --: invalid option'，因为节点缓存没更新）。
            image_pull_policy="Always",
            command=["/usr/local/bin/sisyphus-entrypoint.sh"],
            args=["sleep", "infinity"],
            # privileged: DinD 必须；fuse-overlayfs 要 /dev/fuse + CAP_SYS_ADMIN
            security_context=client.V1SecurityContext(privileged=True),
            resources=client.V1ResourceRequirements(
                # 2026-04 实证：vm-node04 5991Mi 总内存，1Gi request 只能塞 1 个 runner，
                # 多 REQ 并发时撞 FailedScheduling: Insufficient memory。
                # 降到 512Mi/250m，能塞 2-3 个并发 runner。
                # limit 保持 8Gi（runner 跑 docker build 高峰时需要）。
                requests={"cpu": "250m", "memory": "512Mi"},
                limits={"cpu": "4", "memory": "8Gi"},
            ),
            env=env_vars,
            volume_mounts=volume_mounts,
            working_dir="/workspace",
        )

        return client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={
                    "sisyphus/req-id": req_id.lower(),
                    "sisyphus/role": "runner",
                },
            ),
            spec=client.V1PodSpec(
                restart_policy="Always",
                service_account_name=self.runner_sa,
                containers=[container],
                volumes=volumes,
                image_pull_secrets=[
                    client.V1LocalObjectReference(name=s) for s in self.image_pull_secrets
                ] or None,
            ),
        )

    # ── lifecycle ───────────────────────────────────────────────────────

    async def ensure_runner(
        self, req_id: str, *, wait_ready: bool = True,
        timeout_sec: int | None = None,
        attempts: int | None = None,
    ) -> str:
        """幂等创建 PVC + Pod。返回 pod name。

        M9：wait_ready=True 时外层跑 `attempts` 轮 _wait_pod_ready，每轮
        timeout_sec 秒。总等待 ≈ attempts × timeout_sec。全超时后抛最后一次的
        TimeoutError，由 engine.step retry policy 决定 retry / escalate。
        """
        pod_name = self.pod_name(req_id)

        # PVC 先建（Pod 挂它；PVC 落不下来 Pod 会 Pending）
        try:
            await self._k8s(
                self.core_v1.create_namespaced_persistent_volume_claim,
                self.namespace, self.build_pvc(req_id),
            )
            log.info("runner.pvc.created", req_id=req_id, pvc=self.pvc_name(req_id))
        except ApiException as e:
            if e.status != 409:   # Conflict = 已存在，正常跳过
                raise
            log.debug("runner.pvc.exists", req_id=req_id)

        # Pod
        try:
            await self._k8s(
                self.core_v1.create_namespaced_pod,
                self.namespace, self.build_pod(req_id),
            )
            log.info("runner.pod.created", req_id=req_id, pod=pod_name)
        except ApiException as e:
            if e.status != 409:
                raise
            log.debug("runner.pod.exists", req_id=req_id)

        if wait_ready:
            per_attempt = timeout_sec or self.ready_timeout_sec
            total_attempts = attempts or self.ready_attempts
            last_err: TimeoutError | None = None
            for attempt in range(total_attempts):
                try:
                    await self._wait_pod_ready(pod_name, per_attempt)
                    return pod_name
                except TimeoutError as e:
                    last_err = e
                    log.warning(
                        "runner.ready_attempt_failed",
                        req_id=req_id, pod=pod_name,
                        attempt=attempt + 1, total=total_attempts,
                        timeout_sec=per_attempt,
                    )
            # 所有 attempts 都超时：抛最后一次的 TimeoutError（engine retry 会 catch）
            assert last_err is not None
            raise last_err

        return pod_name

    async def _wait_pod_ready(self, pod_name: str, timeout_sec: int) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                pod = await self._k8s(
                    self.core_v1.read_namespaced_pod_status,
                    pod_name, self.namespace,
                )
            except ApiException as e:
                if e.status != 404:
                    raise
                # 可能 etcd 同步延迟，稍等
                await asyncio.sleep(1)
                continue

            phase = pod.status.phase if pod.status else None
            if phase == "Failed":
                reason = pod.status.reason if pod.status else "unknown"
                raise RuntimeError(f"Pod {pod_name} failed: {reason}")

            if pod.status and pod.status.conditions:
                for cond in pod.status.conditions:
                    if cond.type == "Ready" and cond.status == "True":
                        log.info("runner.pod.ready", pod=pod_name)
                        return

            await asyncio.sleep(2)

        raise TimeoutError(f"Pod {pod_name} not ready in {timeout_sec}s")

    async def pause(self, req_id: str) -> bool:
        """删 Pod（PVC 保留）。已不存在返回 False。"""
        try:
            await self._k8s(
                self.core_v1.delete_namespaced_pod,
                self.pod_name(req_id), self.namespace,
            )
            log.info("runner.paused", req_id=req_id)
            return True
        except ApiException as e:
            if e.status != 404:
                raise
            return False

    async def resume(self, req_id: str) -> str:
        """重建 Pod（PVC 复用）。"""
        return await self.ensure_runner(req_id, wait_ready=True)

    async def cleanup_runner(self, req_id: str, *, retain_pvc: bool = False) -> None:
        """终态清理：删 Pod，PVC 按 retain_pvc 决定。幂等。

        retain_pvc=True：escalated 时保留 PVC，给人翻 workspace；过期由 runner_gc 兜底
        retain_pvc=False：done 时立即销 PVC（无 debug 价值），释放磁盘
        """
        pod_deleted = pvc_deleted = False
        try:
            await self._k8s(
                self.core_v1.delete_namespaced_pod,
                self.pod_name(req_id), self.namespace,
            )
            pod_deleted = True
        except ApiException as e:
            if e.status != 404:
                raise
        if not retain_pvc:
            try:
                await self._k8s(
                    self.core_v1.delete_namespaced_persistent_volume_claim,
                    self.pvc_name(req_id), self.namespace,
                )
                pvc_deleted = True
            except ApiException as e:
                if e.status != 404:
                    raise
        log.info("runner.cleanup", req_id=req_id, retain_pvc=retain_pvc,
                 pod_deleted=pod_deleted, pvc_deleted=pvc_deleted)

    async def destroy(self, req_id: str) -> None:
        """向后兼容：等价 cleanup_runner(retain_pvc=False)。"""
        await self.cleanup_runner(req_id, retain_pvc=False)

    async def get_runner_status(self, req_id: str) -> RunnerStatus | None:
        pod_name = self.pod_name(req_id)
        pvc_name = self.pvc_name(req_id)

        pod_phase = "NotFound"
        created_at: str | None = None
        try:
            pod = await self._k8s(
                self.core_v1.read_namespaced_pod_status,
                pod_name, self.namespace,
            )
            pod_phase = (pod.status.phase or "Unknown") if pod.status else "Unknown"
            if pod.metadata and pod.metadata.creation_timestamp:
                created_at = pod.metadata.creation_timestamp.isoformat()
        except ApiException as e:
            if e.status != 404:
                raise

        pvc_phase = "NotFound"
        try:
            pvc = await self._k8s(
                self.core_v1.read_namespaced_persistent_volume_claim_status,
                pvc_name, self.namespace,
            )
            pvc_phase = (pvc.status.phase or "Unknown") if pvc.status else "Unknown"
        except ApiException as e:
            if e.status != 404:
                raise

        if pod_phase == "NotFound" and pvc_phase == "NotFound":
            return None
        return RunnerStatus(
            req_id=req_id, pod_name=pod_name, pvc_name=pvc_name,
            pod_phase=pod_phase, pvc_phase=pvc_phase, created_at=created_at,
        )

    async def list_runners(self) -> list[RunnerStatus]:
        """列所有 runner（按 label 过滤）。"""
        results: list[RunnerStatus] = []
        pvcs = await self._k8s(
            self.core_v1.list_namespaced_persistent_volume_claim,
            self.namespace, label_selector="sisyphus/role=workspace",
        )
        for pvc in pvcs.items:
            req_label = (pvc.metadata.labels or {}).get("sisyphus/req-id", "")
            if not req_label:
                continue
            # 重新大写对齐 REQ-N 形式（K8s label 必小写存，对外恢复）
            req_id = req_label.upper() if req_label.lower().startswith("req-") else req_label
            s = await self.get_runner_status(req_id)
            if s is not None:
                results.append(s)
        return results

    async def node_disk_usage_ratio(self) -> float:
        """节点磁盘使用率 0.0~1.0。用于 GC 判断磁盘压力。

        通过节点 ephemeral-storage allocatable / capacity 推算（local-path PVC 占 ephemeral）。
        失败时抛异常，让 caller fallback 到正常 retention 模式。
        """
        nodes = await self._k8s(self.core_v1.list_node)
        # 取第一个 ready node（sisyphus 单节点 K3s 场景）
        for node in nodes.items:
            cap = node.status.capacity or {}
            alloc = node.status.allocatable or {}
            cap_eph = cap.get("ephemeral-storage")
            alloc_eph = alloc.get("ephemeral-storage")
            if cap_eph and alloc_eph:
                # K8s 单位 like "50644856Ki"，统一转 KiB
                cap_ki = _parse_k8s_quantity(cap_eph)
                alloc_ki = _parse_k8s_quantity(alloc_eph)
                if cap_ki > 0:
                    used_ratio = 1.0 - (alloc_ki / cap_ki)
                    return max(0.0, min(1.0, used_ratio))
        raise RuntimeError("no node with ephemeral-storage info")

    async def gc_orphan_pods(self, keep_req_ids: set[str]) -> list[str]:
        """按 sisyphus/role=runner label 列 Pod，删 keep_req_ids 之外的 Pod。

        **不动 PVC** —— PVC 由 gc_orphan_pvcs 单独管。Pod 占内存/调度容量，
        keep set（仅 non-terminal）跟 PVC keep set（含 escalated retention）
        不同：escalated Pod 没"人 debug"用例，立即可清。

        覆盖 _cleanup_runner_on_terminal 漏网的 zombie Pod —— 那条 fire-and-
        forget 任务在 K8s API blip / orchestrator restart 下可能没跑完。

        404 视为 no-op（Pod 已被别处删）。返已尝试删除的 req_id 列表。
        """
        keep_lower = {r.lower() for r in keep_req_ids}
        cleaned: list[str] = []

        pods = await self._k8s(
            self.core_v1.list_namespaced_pod,
            self.namespace, label_selector="sisyphus/role=runner",
        )
        for pod in pods.items:
            req_label = (pod.metadata.labels or {}).get("sisyphus/req-id", "")
            if not req_label or req_label in keep_lower:
                continue
            req_id = req_label.upper() if req_label.lower().startswith("req-") else req_label
            try:
                await self._k8s(
                    self.core_v1.delete_namespaced_pod,
                    self.pod_name(req_id), self.namespace,
                )
            except ApiException as e:
                if e.status != 404:
                    raise
            cleaned.append(req_id)

        if cleaned:
            log.info("runner.gc.pods_cleaned", count=len(cleaned), reqs=cleaned)
        return cleaned

    async def gc_orphan_pvcs(self, keep_req_ids: set[str]) -> list[str]:
        """按 sisyphus/role=workspace label 列 PVC，删 keep_req_ids 之外的 PVC。

        **不动 Pod** —— Pod 由 gc_orphan_pods 单独管。如果 PVC 还有 Pod 依附
        （Pod GC 还没扫到），K8s 把 PVC 标 Terminating 等 Pod 走，下轮 GC
        重扫即生效，不阻塞。

        404 视为 no-op。返已尝试删除的 req_id 列表。
        """
        keep_lower = {r.lower() for r in keep_req_ids}
        cleaned: list[str] = []

        pvcs = await self._k8s(
            self.core_v1.list_namespaced_persistent_volume_claim,
            self.namespace, label_selector="sisyphus/role=workspace",
        )
        for pvc in pvcs.items:
            req_label = (pvc.metadata.labels or {}).get("sisyphus/req-id", "")
            if not req_label or req_label in keep_lower:
                continue
            req_id = req_label.upper() if req_label.lower().startswith("req-") else req_label
            try:
                await self._k8s(
                    self.core_v1.delete_namespaced_persistent_volume_claim,
                    self.pvc_name(req_id), self.namespace,
                )
            except ApiException as e:
                if e.status != 404:
                    raise
            cleaned.append(req_id)

        if cleaned:
            log.info("runner.gc.pvcs_cleaned", count=len(cleaned), reqs=cleaned)
        return cleaned

    # ── exec ────────────────────────────────────────────────────────────

    async def exec_in_runner(
        self, req_id: str, command: str,
        *, env: dict[str, str] | None = None,
        workdir: str = "/workspace",
        timeout_sec: int = 300,
    ) -> ExecResult:
        """在 runner Pod 里跑 `bash -c command`，抓 stdout/stderr/exit_code。

        exit_code 用 `; echo __MARKER__:$?` 附在命令末尾，stdout 里 parse。
        (K8s stream client `.returncode` 在不同版本不稳定。)

        race: 偶发 stream `is_open()` 在数据 buffer 之前就 False，loop 立刻退出，
        stdout/stderr 全空、exit_code=-1。实证 2026-04-26 dev_cross_check checker
        1.22s silent fail（verifier byxkqvdf 决策诊断为"pod exec 连接层异常"）。
        防御：内层尝试拿到 marker；若失败 + 极短耗时 + 零输出 → 重试一次（不算重试到底，
        让真业务报错和 race 区分开）。
        """
        attempts = 2
        last_result: ExecResult | None = None
        for attempt in range(attempts):
            result = await self._exec_once(
                req_id, command, env=env, workdir=workdir, timeout_sec=timeout_sec,
            )
            last_result = result
            # marker 拿到（无论成功 0 还是非 0）→ 真业务结果，直接返
            if result.exit_code != -1:
                return result
            # exit_code=-1 但有任何输出 → 不是 race，是真 truncate / timeout，直接返
            if result.stdout or result.stderr:
                return result
            # exit_code=-1 + 全空 + 极短耗时 → 几乎肯定是 stream race
            # （正常命令至少耗时几百 ms 起步；marker echo 本身需要 fork+exec）
            if result.duration_sec > 5:
                return result  # 跑过一阵才 -1，可能是 timeout，不重试
            if attempt < attempts - 1:
                log.warning(
                    "exec_in_runner.stream_race_retry",
                    req_id=req_id, attempt=attempt + 1,
                    duration_sec=round(result.duration_sec, 2),
                )
                await asyncio.sleep(0.5)
                continue
        # 所有重试都 -1+空 → 真异常
        return last_result  # type: ignore[return-value]

    async def _exec_once(
        self, req_id: str, command: str,
        *, env: dict[str, str] | None,
        workdir: str,
        timeout_sec: int,
    ) -> ExecResult:
        pod_name = self.pod_name(req_id)

        env_prefix = ""
        if env:
            env_prefix = "env " + " ".join(
                f"{k}={_shell_quote(v)}" for k, v in env.items()
            ) + " "

        full_cmd = f"cd {workdir} && {env_prefix}{command}; echo {_EXIT_MARKER}$?"
        exec_argv = ["/bin/bash", "-c", full_cmd]

        started = time.monotonic()
        resp = await self._k8s(
            stream,
            self.core_v1.connect_get_namespaced_pod_exec,
            pod_name, self.namespace,
            command=exec_argv,
            stderr=True, stdin=False, stdout=True, tty=False,
            _preload_content=False,
        )

        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        deadline = time.monotonic() + timeout_sec

        try:
            # 防 race：第一次 update 给 stream 真的 warm-up 时间。
            # K8s exec channel 偶发 is_open() 在 buffer 填充前就 False，
            # 之前 loop 立刻退出导致 stdout/stderr 全空。先 update 一次让
            # 服务端 SPDY frame 到位，再进 polling loop。
            resp.update(timeout=2)
            if resp.peek_stdout():
                stdout_buf.append(resp.read_stdout())
            if resp.peek_stderr():
                stderr_buf.append(resp.read_stderr())

            while resp.is_open() and time.monotonic() < deadline:
                resp.update(timeout=1)
                if resp.peek_stdout():
                    stdout_buf.append(resp.read_stdout())
                if resp.peek_stderr():
                    stderr_buf.append(resp.read_stderr())
                await asyncio.sleep(0.01)
            # 最后拉一次残留（含 stream 关闭后 buffer 里剩的 frame）
            if resp.peek_stdout():
                stdout_buf.append(resp.read_stdout())
            if resp.peek_stderr():
                stderr_buf.append(resp.read_stderr())
        finally:
            if resp.is_open():
                resp.close()

        duration = time.monotonic() - started
        stdout = "".join(stdout_buf)
        stderr = "".join(stderr_buf)

        exit_code = _parse_exit_marker(stdout)
        if exit_code is not None:
            stdout = _strip_exit_marker(stdout)
        else:
            exit_code = -1

        return ExecResult(
            exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration,
        )


def _parse_exit_marker(stdout: str) -> int | None:
    """从 stdout 最后几行找 __SISY_EXEC_EXIT__:N 标记。找不到返回 None。"""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith(_EXIT_MARKER):
            try:
                return int(line[len(_EXIT_MARKER):])
            except ValueError:
                return None
    return None


def _strip_exit_marker(stdout: str) -> str:
    """去掉 stdout 里的 __SISY_EXEC_EXIT__:N 尾行。"""
    lines = stdout.splitlines(keepends=True)
    while lines and lines[-1].strip().startswith(_EXIT_MARKER):
        lines.pop()
    return "".join(lines)


_K8S_UNITS = {
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
    "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
    "k": 1000, "m": 0.001,
}


def _parse_k8s_quantity(q: str) -> int:
    """K8s 资源数量字符串解析为 KiB。e.g. "50644856Ki" → 50644856；"5Gi" → 5*1024*1024。

    简化版，只支持常见后缀。无后缀按字节算。失败返 0。
    """
    if not q:
        return 0
    q = q.strip()
    for unit in sorted(_K8S_UNITS.keys(), key=len, reverse=True):
        if q.endswith(unit):
            try:
                num = float(q[:-len(unit)])
                bytes_val = num * _K8S_UNITS[unit]
                return int(bytes_val / 1024)  # 统一返 KiB
            except ValueError:
                return 0
    try:
        return int(int(q) / 1024)
    except ValueError:
        return 0
