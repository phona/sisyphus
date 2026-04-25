"""Skeleton-stage contract tests for orchestrator.accept_env_gc.

Pins the public coroutine API surface introduced by
REQ-accept-env-gc-skeleton-v2-1777158943:

- `gc_once` and `run_loop` are async coroutine functions
- both stubs raise `NotImplementedError` when awaited, with a message
  containing the literal substring `accept_env_gc skeleton`

This file is intentionally throwaway: the follow-up implementation REQ that
replaces the stub bodies with real K8s + DB logic MUST also delete this
test (the new behaviour will be covered by a long-lived `accept-env-gc`
capability spec).
"""

from __future__ import annotations

import asyncio

import pytest

from orchestrator import accept_env_gc

_SKELETON_MARKER = "accept_env_gc skeleton"


def test_gc_once_and_run_loop_are_async_coroutine_functions() -> None:
    """AEGS-S1: lock module exposure + async signature."""
    assert asyncio.iscoroutinefunction(accept_env_gc.gc_once)
    assert asyncio.iscoroutinefunction(accept_env_gc.run_loop)


async def test_gc_once_raises_not_implemented_with_skeleton_marker() -> None:
    """AEGS-S2: stub fails loud with skeleton marker."""
    with pytest.raises(NotImplementedError) as exc_info:
        await accept_env_gc.gc_once()
    assert _SKELETON_MARKER in str(exc_info.value)


async def test_run_loop_raises_not_implemented_with_skeleton_marker() -> None:
    """AEGS-S3: stub fails loud with skeleton marker."""
    with pytest.raises(NotImplementedError) as exc_info:
        await accept_env_gc.run_loop()
    assert _SKELETON_MARKER in str(exc_info.value)
