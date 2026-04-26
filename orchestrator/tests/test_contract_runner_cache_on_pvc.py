"""Contract tests for REQ-runner-cache-on-pvc-1777198512.

Capability: runner-cache-on-pvc
Author: challenger-agent (black-box, written from spec only)

Dev MUST NOT modify these tests to make them pass — fix the implementation instead.
If a test is genuinely wrong, escalate to spec_fixer to correct the spec.

Scenarios covered:
  RUNNER-CACHE-S1  GOMODCACHE points into /workspace/.cache/go/mod
  RUNNER-CACHE-S2  GOCACHE points into /workspace/.cache/go/build
  RUNNER-CACHE-S3  npm_config_cache points into /workspace/.cache/npm
  RUNNER-CACHE-S4  UV_CACHE_DIR points into /workspace/.cache/uv
"""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_controller():
    from orchestrator.k8s_runner import RunnerController

    return RunnerController(
        core_v1=MagicMock(),
        namespace="sisyphus-runners",
        runner_image="ghcr.io/phona/sisyphus-runner:main",
        runner_sa="sisyphus-runner-sa",
        runner_secret_name="sisyphus-runner-secrets",
        storage_class="local-path",
        workspace_size="10Gi",
    )


def _env_value(pod, name: str) -> str | None:
    """Return the value of env var `name` in the first container, or None."""
    containers = pod.spec.containers
    assert containers, "Pod must have at least one container"
    for entry in containers[0].env or []:
        if getattr(entry, "name", None) == name:
            return getattr(entry, "value", None)
    return None


# ── RUNNER-CACHE-S1: GOMODCACHE ───────────────────────────────────────────────


def test_runner_cache_s1_gomodcache_env_present():
    """S1: build_pod MUST include env var GOMODCACHE."""
    controller = _make_controller()
    pod = controller.build_pod("REQ-1")

    containers = pod.spec.containers
    assert containers, "Pod must have at least one container"
    env_names = [getattr(e, "name", None) for e in (containers[0].env or [])]
    assert "GOMODCACHE" in env_names, (
        "Pod's container env list must contain 'GOMODCACHE' "
        f"(spec RUNNER-CACHE-S1). Present env names: {env_names}"
    )


def test_runner_cache_s1_gomodcache_value():
    """S1: GOMODCACHE MUST equal '/workspace/.cache/go/mod'."""
    controller = _make_controller()
    pod = controller.build_pod("REQ-1")

    value = _env_value(pod, "GOMODCACHE")
    assert value == "/workspace/.cache/go/mod", (
        "GOMODCACHE must be set to '/workspace/.cache/go/mod' so the Go module "
        "download cache lands on the PVC rather than the container writable layer "
        f"(spec RUNNER-CACHE-S1). Got: {value!r}"
    )


# ── RUNNER-CACHE-S2: GOCACHE ──────────────────────────────────────────────────


def test_runner_cache_s2_gocache_env_present():
    """S2: build_pod MUST include env var GOCACHE."""
    controller = _make_controller()
    pod = controller.build_pod("REQ-1")

    containers = pod.spec.containers
    assert containers, "Pod must have at least one container"
    env_names = [getattr(e, "name", None) for e in (containers[0].env or [])]
    assert "GOCACHE" in env_names, (
        "Pod's container env list must contain 'GOCACHE' "
        f"(spec RUNNER-CACHE-S2). Present env names: {env_names}"
    )


def test_runner_cache_s2_gocache_value():
    """S2: GOCACHE MUST equal '/workspace/.cache/go/build'."""
    controller = _make_controller()
    pod = controller.build_pod("REQ-1")

    value = _env_value(pod, "GOCACHE")
    assert value == "/workspace/.cache/go/build", (
        "GOCACHE must be set to '/workspace/.cache/go/build' so the Go build "
        "cache lands on the PVC rather than the container writable layer "
        f"(spec RUNNER-CACHE-S2). Got: {value!r}"
    )


# ── RUNNER-CACHE-S3: npm_config_cache ─────────────────────────────────────────


def test_runner_cache_s3_npm_config_cache_env_present():
    """S3: build_pod MUST include env var npm_config_cache."""
    controller = _make_controller()
    pod = controller.build_pod("REQ-1")

    containers = pod.spec.containers
    assert containers, "Pod must have at least one container"
    env_names = [getattr(e, "name", None) for e in (containers[0].env or [])]
    assert "npm_config_cache" in env_names, (
        "Pod's container env list must contain 'npm_config_cache' "
        f"(spec RUNNER-CACHE-S3). Present env names: {env_names}"
    )


def test_runner_cache_s3_npm_config_cache_value():
    """S3: npm_config_cache MUST equal '/workspace/.cache/npm'."""
    controller = _make_controller()
    pod = controller.build_pod("REQ-1")

    value = _env_value(pod, "npm_config_cache")
    assert value == "/workspace/.cache/npm", (
        "npm_config_cache must be set to '/workspace/.cache/npm' so the npm "
        "package cache lands on the PVC rather than the container writable layer "
        f"(spec RUNNER-CACHE-S3). Got: {value!r}"
    )


# ── RUNNER-CACHE-S4: UV_CACHE_DIR ────────────────────────────────────────────


def test_runner_cache_s4_uv_cache_dir_env_present():
    """S4: build_pod MUST include env var UV_CACHE_DIR."""
    controller = _make_controller()
    pod = controller.build_pod("REQ-1")

    containers = pod.spec.containers
    assert containers, "Pod must have at least one container"
    env_names = [getattr(e, "name", None) for e in (containers[0].env or [])]
    assert "UV_CACHE_DIR" in env_names, (
        "Pod's container env list must contain 'UV_CACHE_DIR' "
        f"(spec RUNNER-CACHE-S4). Present env names: {env_names}"
    )


def test_runner_cache_s4_uv_cache_dir_value():
    """S4: UV_CACHE_DIR MUST equal '/workspace/.cache/uv'."""
    controller = _make_controller()
    pod = controller.build_pod("REQ-1")

    value = _env_value(pod, "UV_CACHE_DIR")
    assert value == "/workspace/.cache/uv", (
        "UV_CACHE_DIR must be set to '/workspace/.cache/uv' so the uv "
        "package cache lands on the PVC rather than the container writable layer "
        f"(spec RUNNER-CACHE-S4). Got: {value!r}"
    )


# ── cross-cut: all four caches are under /workspace/.cache ───────────────────


def test_runner_cache_all_four_vars_point_under_workspace_cache():
    """S1-S4: All four cache env vars MUST have values starting with '/workspace/.cache/'."""
    controller = _make_controller()
    pod = controller.build_pod("REQ-1")

    required = {
        "GOMODCACHE": "/workspace/.cache/go/mod",
        "GOCACHE": "/workspace/.cache/go/build",
        "npm_config_cache": "/workspace/.cache/npm",
        "UV_CACHE_DIR": "/workspace/.cache/uv",
    }
    for var_name, expected in required.items():
        actual = _env_value(pod, var_name)
        assert actual is not None, (
            f"Env var '{var_name}' missing from Pod container spec "
            f"(spec RUNNER-CACHE-S1..S4). "
            "All four toolchain cache dirs must be redirected to PVC."
        )
        assert actual.startswith("/workspace/.cache/"), (
            f"Env var '{var_name}'={actual!r} does not start with '/workspace/.cache/' — "
            "all cache dirs must be PVC-resident (spec RUNNER-CACHE-S1..S4)."
        )
        assert actual == expected, (
            f"Env var '{var_name}': expected {expected!r}, got {actual!r} "
            f"(spec RUNNER-CACHE-S1..S4)."
        )
