"""Driver Protocol — five-method async contract.

The Protocol shape is the *contract* M0 freezes; the three concrete drivers
(``playwright``, ``adb``, ``http``) raise ``NotImplementedError`` on every
method until M1 fills them in.
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
    ok: bool
    failure_hint: str | None = None


@dataclass
class AssertResult:
    ok: bool
    failure_hint: str | None = None


@dataclass
class Evidence:
    """Driver-captured evidence attached to a step / scenario result."""

    dom: str | None = None
    network: list[dict[str, Any]] = field(default_factory=list)
    screenshot: str | None = None  # base64-png or url


class DriverError(Exception):
    """Base class for driver-level errors. M0 doesn't subclass this anywhere."""


@runtime_checkable
class Driver(Protocol):
    """The five-method async contract every driver implements.

    M0 freezes the shape. Real implementations land in M1.
    """

    name: str

    async def preflight(self, endpoint: str) -> PreflightResult:  # pragma: no cover
        ...

    async def observe(self) -> SemanticTree:  # pragma: no cover
        ...

    async def act(self, step: str) -> ActResult:  # pragma: no cover
        ...

    async def assert_(self, step: str) -> AssertResult:  # pragma: no cover
        ...

    async def capture_evidence(self) -> Evidence:  # pragma: no cover
        ...
