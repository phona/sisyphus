"""Driver Protocol — atomic-MCP shape.

Atomic MCP era: drivers expose preflight / observe / capture_evidence as the
shared contract. All other operations (tap / type / wait / etc.) are
domain-specific and live on each concrete driver — they are surfaced through
MCP atomic tools, not through a generic ``act(step: str)`` regex dispatch.

``ActResult`` is kept as the canonical "operation may fail with a hint" return
type used by the adb atomic methods. There is no ``AssertResult`` anymore —
verification is done by the accept-agent, not by the driver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass
class PreflightResult:
    """Returned by :py:meth:`Driver.preflight`."""

    ok: bool
    failure_hint: str | None = None
    a11y_node_count: int | None = None


@dataclass
class SemanticTree:
    """A driver-specific snapshot of the semantic layer.

    For ``playwright``: a11y snapshot dict.
    For ``adb``: parsed uiautomator XML.
    For ``http``: response body + headers.
    """

    kind: Literal["a11y", "uiautomator", "http"]
    payload: Any
    captured_at_ms: int = 0


@dataclass
class ActResult:
    """Outcome of a driver operation that may fail with a human-readable hint."""

    ok: bool
    failure_hint: str | None = None


@dataclass
class Evidence:
    """Driver-captured evidence attached to an operation result."""

    dom: str | None = None
    network: list[dict[str, Any]] = field(default_factory=list)
    screenshot: str | None = None  # base64-png or url


class DriverError(Exception):
    """Base class for driver-level errors."""


@runtime_checkable
class Driver(Protocol):
    """Three-method async contract every driver implements.

    Atomic operations (tap / type / wait / ...) are domain-specific and live on
    each concrete driver, not on the Protocol.
    """

    name: str

    async def preflight(self, endpoint: str) -> PreflightResult:  # pragma: no cover
        ...

    async def observe(self) -> SemanticTree:  # pragma: no cover
        ...

    async def capture_evidence(self) -> Evidence:  # pragma: no cover
        ...
