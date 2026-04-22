"""K8s-based runner pod controller（v0.2 架构）.

每个 REQ 对应 sisyphus-runners namespace 下一个 Pod（runner-<REQ>）+ 一个 PVC
（workspace-<REQ>）。Pod 是 privileged + DinD + fuse-overlayfs 的调试环境，PVC 挂
/workspace 持久保存 clone 的 repos + manifest.yaml + 中间产物。

生命周期绑 REQ：
  - analyze 起：ensure_runner 幂等创建 PVC + Pod
  - 中途 Pod 重启：K8s restartPolicy=Always 自动拉起（PVC 不动）
  - pause：delete Pod（PVC 留）
  - resume：重建 Pod（PVC 自动重挂）
  - done/escalate 清理：delete Pod + PVC

所有阻塞 K8s API 走 asyncio.to_thread，不堵事件循环。
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
        in_cluster: bool = True,
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

        if core_v1 is not None:
            # 测试注入 mock client
            self.core_v1 = core_v1
        else:
            if in_cluster:
                config.load_incluster_config()
            else:
                config.load_kube_config()
            self.core_v1 = client.CoreV1Api()

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

        container = client.V1Container(
            name="runner",
            image=self.runner_image,
            command=["/usr/local/bin/sisyphus-entrypoint.sh"],
            args=["sleep", "infinity"],
            # privileged: DinD 必须；fuse-overlayfs 要 /dev/fuse + CAP_SYS_ADMIN
            security_context=client.V1SecurityContext(privileged=True),
            resources=client.V1ResourceRequirements(
                requests={"cpu": "500m", "memory": "1Gi"},
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
    ) -> str:
        """幂等创建 PVC + Pod。返回 pod name。"""
        pod_name = self.pod_name(req_id)

        # PVC 先建（Pod 挂它；PVC 落不下来 Pod 会 Pending）
        try:
            await asyncio.to_thread(
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
            await asyncio.to_thread(
                self.core_v1.create_namespaced_pod,
                self.namespace, self.build_pod(req_id),
            )
            log.info("runner.pod.created", req_id=req_id, pod=pod_name)
        except ApiException as e:
            if e.status != 409:
                raise
            log.debug("runner.pod.exists", req_id=req_id)

        if wait_ready:
            await self._wait_pod_ready(pod_name, timeout_sec or self.ready_timeout_sec)

        return pod_name

    async def _wait_pod_ready(self, pod_name: str, timeout_sec: int) -> None:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                pod = await asyncio.to_thread(
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
            await asyncio.to_thread(
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

    async def destroy(self, req_id: str) -> None:
        """终态清理：删 Pod + PVC。幂等。"""
        pod_deleted = pvc_deleted = False
        try:
            await asyncio.to_thread(
                self.core_v1.delete_namespaced_pod,
                self.pod_name(req_id), self.namespace,
            )
            pod_deleted = True
        except ApiException as e:
            if e.status != 404:
                raise
        try:
            await asyncio.to_thread(
                self.core_v1.delete_namespaced_persistent_volume_claim,
                self.pvc_name(req_id), self.namespace,
            )
            pvc_deleted = True
        except ApiException as e:
            if e.status != 404:
                raise
        log.info("runner.destroyed", req_id=req_id,
                 pod_deleted=pod_deleted, pvc_deleted=pvc_deleted)

    async def get_runner_status(self, req_id: str) -> RunnerStatus | None:
        pod_name = self.pod_name(req_id)
        pvc_name = self.pvc_name(req_id)

        pod_phase = "NotFound"
        created_at: str | None = None
        try:
            pod = await asyncio.to_thread(
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
            pvc = await asyncio.to_thread(
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
        pvcs = await asyncio.to_thread(
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

    async def gc_orphans(self, keep_req_ids: set[str]) -> list[str]:
        """删除 keep_req_ids 之外的所有 runner（pod + pvc）。

        调用方：orchestrator 启动时 + 周期性 task。
        keep_req_ids = req_state 里 state not in (done, escalated 过期) 的 req_id 集合。

        返回被清理的 req_id 列表。
        """
        keep_lower = {r.lower() for r in keep_req_ids}
        cleaned: list[str] = []

        pvcs = await asyncio.to_thread(
            self.core_v1.list_namespaced_persistent_volume_claim,
            self.namespace, label_selector="sisyphus/role=workspace",
        )
        for pvc in pvcs.items:
            req_label = (pvc.metadata.labels or {}).get("sisyphus/req-id", "")
            if not req_label or req_label in keep_lower:
                continue
            req_id = req_label.upper() if req_label.lower().startswith("req-") else req_label
            await self.destroy(req_id)
            cleaned.append(req_id)

        if cleaned:
            log.info("runner.gc.cleaned", count=len(cleaned), reqs=cleaned)
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
        """
        pod_name = self.pod_name(req_id)

        env_prefix = ""
        if env:
            env_prefix = "env " + " ".join(
                f"{k}={_shell_quote(v)}" for k, v in env.items()
            ) + " "

        full_cmd = f"cd {workdir} && {env_prefix}{command}; echo {_EXIT_MARKER}$?"
        exec_argv = ["/bin/bash", "-c", full_cmd]

        started = time.monotonic()
        resp = await asyncio.to_thread(
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
            while resp.is_open() and time.monotonic() < deadline:
                # update 的 timeout 单位是秒（浮点 OK）
                resp.update(timeout=1)
                if resp.peek_stdout():
                    stdout_buf.append(resp.read_stdout())
                if resp.peek_stderr():
                    stderr_buf.append(resp.read_stderr())
                await asyncio.sleep(0.01)
            # 最后拉一次残留
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

        # parse exit code 从 stdout 最后一行
        exit_code = _parse_exit_marker(stdout)
        # 把 marker 从 stdout 去掉（别污染业务日志）
        if exit_code is not None:
            stdout = _strip_exit_marker(stdout)
        else:
            # 没找到 marker = 命令被 truncate / timeout
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
