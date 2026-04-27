"""HTTP driver — REST/JSON API — M0 stub."""

from __future__ import annotations

from thanatos.drivers.base import (
    ActResult,
    AssertResult,
    Evidence,
    PreflightResult,
    SemanticTree,
)

_M0_MSG = "M0: scaffold only"


class HttpDriver:
    name: str = "http"

    async def preflight(self, endpoint: str) -> PreflightResult:
        raise NotImplementedError(_M0_MSG)

    async def observe(self) -> SemanticTree:
        raise NotImplementedError(_M0_MSG)

    async def act(self, step: str) -> ActResult:
        raise NotImplementedError(_M0_MSG)

    async def assert_(self, step: str) -> AssertResult:
        raise NotImplementedError(_M0_MSG)

    async def capture_evidence(self) -> Evidence:
        raise NotImplementedError(_M0_MSG)
