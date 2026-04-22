"""K8s runner controller 单测。

不打真 K8s：mock CoreV1Api。验证：
- 名字规范（pod_name / pvc_name）
- Spec builder 生成的 Pod / PVC 对象结构
- ensure_runner 幂等（409 Conflict 被吃掉）
- destroy / pause / resume 流程
- exec marker parse
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from kubernetes.client import ApiException

from orchestrator.k8s_runner import (
    RunnerController,
    _parse_exit_marker,
    _strip_exit_marker,
)


def _make_controller(core_v1: MagicMock | None = None) -> RunnerController:
    return RunnerController(
        namespace="sisyphus-runners",
        runner_image="ghcr.io/phona/sisyphus-runner-go:main",
        runner_sa="sisyphus-runner-sa",
        storage_class="local-path",
        workspace_size="10Gi",
        runner_secret_name="sisyphus-runner-secrets",
        image_pull_secrets=[],
        ready_timeout_sec=5,
        core_v1=core_v1 or MagicMock(),
    )


# ─── naming ────────────────────────────────────────────────────────────


def test_pod_and_pvc_names_lowercase():
    rc = _make_controller()
    assert rc.pod_name("REQ-997") == "runner-req-997"
    assert rc.pvc_name("REQ-997") == "workspace-req-997"


# ─── spec builders ─────────────────────────────────────────────────────


def test_build_pvc_has_correct_shape():
    rc = _make_controller()
    pvc = rc.build_pvc("REQ-997")
    assert pvc.metadata.name == "workspace-req-997"
    assert pvc.metadata.labels["sisyphus/req-id"] == "req-997"
    assert pvc.metadata.labels["sisyphus/role"] == "workspace"
    assert pvc.spec.access_modes == ["ReadWriteOnce"]
    assert pvc.spec.storage_class_name == "local-path"
    assert pvc.spec.resources.requests == {"storage": "10Gi"}


def test_build_pod_mounts_pvc_and_fuse():
    rc = _make_controller()
    pod = rc.build_pod("REQ-997")
    assert pod.metadata.name == "runner-req-997"
    assert pod.spec.restart_policy == "Always"
    assert pod.spec.service_account_name == "sisyphus-runner-sa"

    c = pod.spec.containers[0]
    assert c.image == "ghcr.io/phona/sisyphus-runner-go:main"
    assert c.security_context.privileged is True
    assert c.working_dir == "/workspace"

    mount_paths = [m.mount_path for m in c.volume_mounts]
    assert "/workspace" in mount_paths
    assert "/dev/fuse" in mount_paths
    assert "/root/.kube" in mount_paths

    # PVC 正确挂到 workspace
    workspace_vol = next(v for v in pod.spec.volumes if v.name == "workspace")
    assert workspace_vol.persistent_volume_claim.claim_name == "workspace-req-997"

    # fuse hostPath
    fuse_vol = next(v for v in pod.spec.volumes if v.name == "fuse")
    assert fuse_vol.host_path.path == "/dev/fuse"
    assert fuse_vol.host_path.type == "CharDevice"

    # GH token 从 secret 注入（optional，不在就算了）
    env_names = [e.name for e in c.env]
    assert "GH_TOKEN" in env_names
    assert "SISYPHUS_REQ_ID" in env_names


def test_build_pod_no_image_pull_secrets_when_empty():
    rc = _make_controller()
    pod = rc.build_pod("REQ-1")
    assert pod.spec.image_pull_secrets is None


def test_build_pod_with_image_pull_secrets():
    core = MagicMock()
    rc = RunnerController(
        namespace="sisyphus-runners",
        runner_image="img",
        runner_sa="sa",
        storage_class="local-path",
        workspace_size="5Gi",
        runner_secret_name="s",
        image_pull_secrets=["ghcr-creds"],
        core_v1=core,
    )
    pod = rc.build_pod("REQ-1")
    assert pod.spec.image_pull_secrets[0].name == "ghcr-creds"


def test_kubeconfig_mounts_from_same_secret():
    """verify kubeconfig is mounted from the same runner_secret_name (not a separate secret)。"""
    rc = _make_controller()
    pod = rc.build_pod("REQ-1")
    kubeconfig_vol = next(v for v in pod.spec.volumes if v.name == "kubeconfig")
    assert kubeconfig_vol.secret.secret_name == "sisyphus-runner-secrets"
    # items 映射 key=kubeconfig → path=config（让 ~/.kube/config 正确）
    items = kubeconfig_vol.secret.items or []
    assert any(i.key == "kubeconfig" and i.path == "config" for i in items)


# ─── lifecycle: ensure_runner 幂等 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_runner_creates_fresh(monkeypatch):
    core = MagicMock()
    # PVC + Pod 新建：两次 create 不抛
    core.create_namespaced_persistent_volume_claim = MagicMock(return_value=None)
    core.create_namespaced_pod = MagicMock(return_value=None)
    # wait_ready: 立刻 Ready
    ready = MagicMock(status=MagicMock(phase="Running",
                                       conditions=[MagicMock(type="Ready", status="True")]))
    core.read_namespaced_pod_status = MagicMock(return_value=ready)

    rc = _make_controller(core)
    pod_name = await rc.ensure_runner("REQ-1", wait_ready=True)
    assert pod_name == "runner-req-1"
    core.create_namespaced_persistent_volume_claim.assert_called_once()
    core.create_namespaced_pod.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_runner_idempotent_on_409():
    core = MagicMock()
    # 两个 create 都返 409 (Conflict, 已存在)
    core.create_namespaced_persistent_volume_claim = MagicMock(
        side_effect=ApiException(status=409, reason="Conflict")
    )
    core.create_namespaced_pod = MagicMock(
        side_effect=ApiException(status=409, reason="Conflict")
    )
    ready = MagicMock(status=MagicMock(phase="Running",
                                       conditions=[MagicMock(type="Ready", status="True")]))
    core.read_namespaced_pod_status = MagicMock(return_value=ready)

    rc = _make_controller(core)
    # 不抛 = 幂等通过
    await rc.ensure_runner("REQ-1", wait_ready=True)


@pytest.mark.asyncio
async def test_ensure_runner_raises_on_other_api_error():
    core = MagicMock()
    core.create_namespaced_persistent_volume_claim = MagicMock(
        side_effect=ApiException(status=500, reason="server error")
    )
    rc = _make_controller(core)
    with pytest.raises(ApiException):
        await rc.ensure_runner("REQ-1", wait_ready=False)


# ─── M9: ensure_runner 多次 attempt ────────────────────────────────────


def _make_controller_with_attempts(core_v1: MagicMock, attempts: int) -> RunnerController:
    return RunnerController(
        namespace="sisyphus-runners",
        runner_image="img",
        runner_sa="sa",
        storage_class="local-path",
        workspace_size="5Gi",
        runner_secret_name="s",
        image_pull_secrets=[],
        ready_timeout_sec=1,      # 每轮 1s 超时，测试快
        ready_attempts=attempts,
        core_v1=core_v1,
    )


@pytest.mark.asyncio
async def test_ensure_runner_multi_attempt_succeeds_on_second(monkeypatch):
    """第 1 次 _wait_pod_ready 抛 TimeoutError → 第 2 次 Ready → ensure_runner 返回 pod_name。"""
    core = MagicMock()
    core.create_namespaced_persistent_volume_claim = MagicMock(return_value=None)
    core.create_namespaced_pod = MagicMock(return_value=None)
    rc = _make_controller_with_attempts(core, attempts=3)

    calls = {"n": 0}

    async def fake_wait(pod_name, timeout_sec):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError(f"Pod {pod_name} not ready in {timeout_sec}s")
        return None

    monkeypatch.setattr(rc, "_wait_pod_ready", fake_wait)

    pod_name = await rc.ensure_runner("REQ-1", wait_ready=True)
    assert pod_name == "runner-req-1"
    assert calls["n"] == 2, "should retry once then succeed"


@pytest.mark.asyncio
async def test_ensure_runner_multi_attempt_all_fail_raises_last_timeout(monkeypatch):
    """所有 attempts 都超时 → 抛最后一次 TimeoutError（engine retry policy 接手）。"""
    core = MagicMock()
    core.create_namespaced_persistent_volume_claim = MagicMock(return_value=None)
    core.create_namespaced_pod = MagicMock(return_value=None)
    rc = _make_controller_with_attempts(core, attempts=3)

    calls = {"n": 0}

    async def fake_wait(pod_name, timeout_sec):
        calls["n"] += 1
        raise TimeoutError(f"Pod {pod_name} not ready (attempt {calls['n']})")

    monkeypatch.setattr(rc, "_wait_pod_ready", fake_wait)

    with pytest.raises(TimeoutError) as exc_info:
        await rc.ensure_runner("REQ-1", wait_ready=True)
    assert calls["n"] == 3
    # 最后一次的错误 message（attempt 3）
    assert "attempt 3" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ensure_runner_attempts_override_via_kwarg(monkeypatch):
    """attempts 参数可以覆盖 controller 默认值（给单次 ensure 临时延长耐心）。"""
    core = MagicMock()
    core.create_namespaced_persistent_volume_claim = MagicMock(return_value=None)
    core.create_namespaced_pod = MagicMock(return_value=None)
    rc = _make_controller_with_attempts(core, attempts=1)

    calls = {"n": 0}

    async def fake_wait(pod_name, timeout_sec):
        calls["n"] += 1
        if calls["n"] < 4:
            raise TimeoutError("slow node")
        return None

    monkeypatch.setattr(rc, "_wait_pod_ready", fake_wait)

    await rc.ensure_runner("REQ-1", wait_ready=True, attempts=5)
    assert calls["n"] == 4


@pytest.mark.asyncio
async def test_ensure_runner_single_attempt_still_works_when_ready(monkeypatch):
    """attempts=1 + 立即 Ready：不多做无谓循环。"""
    core = MagicMock()
    core.create_namespaced_persistent_volume_claim = MagicMock(return_value=None)
    core.create_namespaced_pod = MagicMock(return_value=None)
    rc = _make_controller_with_attempts(core, attempts=1)

    calls = {"n": 0}

    async def fake_wait(pod_name, timeout_sec):
        calls["n"] += 1
        return None

    monkeypatch.setattr(rc, "_wait_pod_ready", fake_wait)

    await rc.ensure_runner("REQ-1", wait_ready=True)
    assert calls["n"] == 1


# ─── pause / resume / destroy ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_deletes_pod_keeps_pvc():
    core = MagicMock()
    core.delete_namespaced_pod = MagicMock(return_value=None)
    rc = _make_controller(core)
    assert (await rc.pause("REQ-1")) is True
    core.delete_namespaced_pod.assert_called_once_with("runner-req-1", "sisyphus-runners")
    # PVC 不该被动
    core.delete_namespaced_persistent_volume_claim.assert_not_called()


@pytest.mark.asyncio
async def test_pause_404_returns_false():
    core = MagicMock()
    core.delete_namespaced_pod = MagicMock(
        side_effect=ApiException(status=404, reason="Not Found")
    )
    rc = _make_controller(core)
    assert (await rc.pause("REQ-1")) is False


@pytest.mark.asyncio
async def test_destroy_deletes_both_idempotent():
    core = MagicMock()
    # pod 已不在（404），PVC 还在（删成功）
    core.delete_namespaced_pod = MagicMock(
        side_effect=ApiException(status=404, reason="Not Found")
    )
    core.delete_namespaced_persistent_volume_claim = MagicMock(return_value=None)
    rc = _make_controller(core)
    await rc.destroy("REQ-1")
    core.delete_namespaced_pod.assert_called_once()
    core.delete_namespaced_persistent_volume_claim.assert_called_once()


# ─── M10: cleanup_runner ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_runner_default_deletes_both():
    """retain_pvc 默认 False → 删 pod + PVC（done 路径）。"""
    core = MagicMock()
    core.delete_namespaced_pod = MagicMock(return_value=None)
    core.delete_namespaced_persistent_volume_claim = MagicMock(return_value=None)
    rc = _make_controller(core)
    await rc.cleanup_runner("REQ-9")
    core.delete_namespaced_pod.assert_called_once_with("runner-req-9", "sisyphus-runners")
    core.delete_namespaced_persistent_volume_claim.assert_called_once_with(
        "workspace-req-9", "sisyphus-runners",
    )


@pytest.mark.asyncio
async def test_cleanup_runner_retain_pvc_only_deletes_pod():
    """retain_pvc=True → 只删 pod，PVC 留给人翻（escalated 路径）。"""
    core = MagicMock()
    core.delete_namespaced_pod = MagicMock(return_value=None)
    core.delete_namespaced_persistent_volume_claim = MagicMock(return_value=None)
    rc = _make_controller(core)
    await rc.cleanup_runner("REQ-9", retain_pvc=True)
    core.delete_namespaced_pod.assert_called_once_with("runner-req-9", "sisyphus-runners")
    core.delete_namespaced_persistent_volume_claim.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_runner_idempotent_on_404():
    """pod / PVC 已不在 → 吃 404，不抛。"""
    core = MagicMock()
    core.delete_namespaced_pod = MagicMock(
        side_effect=ApiException(status=404, reason="Not Found")
    )
    core.delete_namespaced_persistent_volume_claim = MagicMock(
        side_effect=ApiException(status=404, reason="Not Found")
    )
    rc = _make_controller(core)
    await rc.cleanup_runner("REQ-1")  # 不抛 = ok
    core.delete_namespaced_pod.assert_called_once()
    core.delete_namespaced_persistent_volume_claim.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_runner_raises_on_other_api_error():
    core = MagicMock()
    core.delete_namespaced_pod = MagicMock(
        side_effect=ApiException(status=500, reason="server error")
    )
    rc = _make_controller(core)
    with pytest.raises(ApiException):
        await rc.cleanup_runner("REQ-1")


# ─── gc_orphans ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gc_orphans_removes_not_in_keep_set():
    core = MagicMock()

    def _pvc(req_label):
        return MagicMock(metadata=MagicMock(labels={
            "sisyphus/req-id": req_label, "sisyphus/role": "workspace",
        }))

    core.list_namespaced_persistent_volume_claim = MagicMock(return_value=MagicMock(
        items=[_pvc("req-1"), _pvc("req-2"), _pvc("req-3")],
    ))
    core.delete_namespaced_pod = MagicMock(return_value=None)
    core.delete_namespaced_persistent_volume_claim = MagicMock(return_value=None)
    rc = _make_controller(core)

    # keep REQ-1 only；REQ-2 + REQ-3 应被清
    cleaned = await rc.gc_orphans({"REQ-1"})
    assert sorted(cleaned) == ["REQ-2", "REQ-3"]
    # 两次 destroy = 两次 pod delete + 两次 pvc delete
    assert core.delete_namespaced_pod.call_count == 2
    assert core.delete_namespaced_persistent_volume_claim.call_count == 2


# ─── exec marker parsing ───────────────────────────────────────────────


def test_parse_exit_marker_happy():
    stdout = "hello\nworld\n__SISY_EXEC_EXIT__:0\n"
    assert _parse_exit_marker(stdout) == 0


def test_parse_exit_marker_nonzero():
    stdout = "oops\n__SISY_EXEC_EXIT__:127\n"
    assert _parse_exit_marker(stdout) == 127


def test_parse_exit_marker_missing():
    assert _parse_exit_marker("no marker here\n") is None


def test_parse_exit_marker_trailing_whitespace():
    stdout = "ok\n__SISY_EXEC_EXIT__:42   \n"
    assert _parse_exit_marker(stdout) == 42


def test_strip_exit_marker_cleans_tail():
    stdout = "line1\nline2\n__SISY_EXEC_EXIT__:0\n"
    assert _strip_exit_marker(stdout) == "line1\nline2\n"


def test_strip_exit_marker_no_op_when_missing():
    stdout = "line1\nline2\n"
    assert _strip_exit_marker(stdout) == "line1\nline2\n"
