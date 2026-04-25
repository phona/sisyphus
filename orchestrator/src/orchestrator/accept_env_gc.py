"""accept_env_gc — file-skeleton placeholder (REQ-accept-env-gc-skeleton-v2-1777158943).

Future GC subsystem will scan `accept-req-*` Kubernetes namespaces left behind
by best-effort `make accept-env-down` and cascade-delete the ones whose REQ has
reached a terminal state. This module currently only locks the public coroutine
API surface (`gc_once`, `run_loop`) so the follow-up implementation REQ can land
real K8s + DB logic without changing callers.

Both stubs raise `NotImplementedError` when awaited so any accidental wiring
fails loud rather than silently no-oping (or, worse, hanging in an infinite
sleep loop). The follow-up implementation REQ will replace both bodies and
retire the throwaway skeleton-only contract test that pins this behaviour.
"""

from __future__ import annotations

_SKELETON_MARKER = "accept_env_gc skeleton — implementation deferred to follow-up REQ"


async def gc_once() -> None:
    raise NotImplementedError(_SKELETON_MARKER)


async def run_loop() -> None:
    raise NotImplementedError(_SKELETON_MARKER)
