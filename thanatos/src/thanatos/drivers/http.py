"""HTTP driver — REST/JSON API.

Atomic-MCP era: this driver still implements the three-method Driver Protocol
(``preflight`` / ``observe`` / ``capture_evidence``). Per-step actions are not
exposed via MCP — challenger writes contract tests directly against proto /
OpenAPI signatures rather than asking thanatos to issue ad-hoc REST calls.
"""

from __future__ import annotations

from typing import Any

import httpx

from thanatos.drivers.base import (
    Evidence,
    PreflightResult,
    SemanticTree,
)


class HttpDriver:
    name: str = "http"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._last_response: httpx.Response | None = None
        self._last_request_info: dict[str, Any] | None = None
        self._endpoint: str | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self._client

    async def preflight(self, endpoint: str) -> PreflightResult:
        self._endpoint = endpoint.rstrip("/")
        client = await self._get_client()
        try:
            resp = await client.get(f"{self._endpoint}/healthz")
            if resp.status_code == 200:
                return PreflightResult(ok=True)
            return PreflightResult(
                ok=False,
                failure_hint=f"healthz returned {resp.status_code}",
            )
        except Exception as exc:
            return PreflightResult(
                ok=False,
                failure_hint=f"healthz failed: {exc}",
            )

    async def observe(self) -> SemanticTree:
        if self._last_response is None:
            return SemanticTree(kind="http", payload={})
        payload = {
            "status_code": self._last_response.status_code,
            "headers": dict(self._last_response.headers),
            "body": self._safe_json_body(self._last_response),
        }
        return SemanticTree(kind="http", payload=payload)

    def _safe_json_body(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except Exception:
            return None

    async def capture_evidence(self) -> Evidence:
        network: list[dict[str, Any]] = []
        if self._last_request_info is not None:
            entry: dict[str, Any] = {"request": self._last_request_info.copy()}
            if self._last_response is not None:
                entry["response"] = {
                    "status_code": self._last_response.status_code,
                    "headers": dict(self._last_response.headers),
                    "body": self._safe_json_body(self._last_response),
                }
            network.append(entry)
        return Evidence(network=network)
