"""Challenger contract tests for REQ-accept-env-gc-skeleton-v2-1777158943.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-accept-env-gc-skeleton-v2-1777158943/specs/accept-env-gc-skeleton/spec.md

Scenarios covered:
  AEGS-S1  module imports cleanly; gc_once and run_loop are async coroutine functions
  AEGS-S2  awaiting gc_once() raises NotImplementedError containing "accept_env_gc skeleton"
  AEGS-S3  awaiting run_loop() raises NotImplementedError containing "accept_env_gc skeleton"
"""
from __future__ import annotations

import asyncio
import importlib

import pytest

SKELETON_MARKER = "accept_env_gc skeleton"


def test_aegs_s1_module_imports_cleanly_and_exposes_async_api() -> None:
    """AEGS-S1: import succeeds; gc_once and run_loop are coroutine functions."""
    mod = importlib.import_module("orchestrator.accept_env_gc")
    assert hasattr(mod, "gc_once"), "module must expose gc_once"
    assert hasattr(mod, "run_loop"), "module must expose run_loop"
    assert asyncio.iscoroutinefunction(mod.gc_once), (
        "gc_once must be a coroutine function (defined with async def)"
    )
    assert asyncio.iscoroutinefunction(mod.run_loop), (
        "run_loop must be a coroutine function (defined with async def)"
    )


async def test_aegs_s2_gc_once_raises_not_implemented_with_skeleton_marker() -> None:
    """AEGS-S2: await gc_once() raises NotImplementedError with skeleton marker."""
    from orchestrator import accept_env_gc

    with pytest.raises(NotImplementedError) as exc_info:
        await accept_env_gc.gc_once()
    assert SKELETON_MARKER in str(exc_info.value), (
        f"NotImplementedError message must contain {SKELETON_MARKER!r}, "
        f"got: {exc_info.value!r}"
    )


async def test_aegs_s3_run_loop_raises_not_implemented_with_skeleton_marker() -> None:
    """AEGS-S3: await run_loop() raises NotImplementedError with skeleton marker."""
    from orchestrator import accept_env_gc

    with pytest.raises(NotImplementedError) as exc_info:
        await accept_env_gc.run_loop()
    assert SKELETON_MARKER in str(exc_info.value), (
        f"NotImplementedError message must contain {SKELETON_MARKER!r}, "
        f"got: {exc_info.value!r}"
    )
