"""HTTP driver — REST/JSON API — M1 implementation."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from thanatos.drivers.base import (
    ActResult,
    AssertResult,
    Evidence,
    PreflightResult,
    SemanticTree,
)

# When POST /api/v1/order with body {"foo":"bar"}
# When GET /api/v1/order/{id}
_ACT_RE = re.compile(
    r"^(?:When\s+)?"  # optional "When " prefix
    r"(?P<method>GET|POST|PUT|PATCH|DELETE)\s+"
    r"(?P<path>\S+)"
    r"(?:\s+with\s+body\s+(?P<body>.+))?",
    re.IGNORECASE,
)

# Then response code is 200
# Then response body contains "foo"
# Then response body order_id > 0
_ASSERT_RE = re.compile(
    r"^(?:Then\s+)?"
    r"response\s+(?P<target>\S+)\s+"
    r"(?P<op>is|contains|equals|==|!=|>|<|>=|<=)\s*"
    r"(?P<expected>.+)?",
    re.IGNORECASE,
)

# Dot-path for JSON body: body.order_id or body.items.0.name
_DOT_PATH = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*|\.[0-9]+)*$")


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

    async def act(self, step: str) -> ActResult:
        m = _ACT_RE.match(step.strip())
        if m is None:
            return ActResult(ok=False, failure_hint=f"unrecognised HTTP step: {step!r}")

        method = m.group("method").upper()
        path = m.group("path")
        body_str = m.group("body")

        body: Any = None
        if body_str is not None:
            body_str = body_str.strip()
            try:
                body = json.loads(body_str)
            except json.JSONDecodeError:
                body = body_str

        client = await self._get_client()

        url = path
        if url.startswith("/") and self._endpoint is not None:
            url = f"{self._endpoint}{url}"

        try:
            resp = await client.request(method, url, json=body if isinstance(body, (dict, list)) else None, content=body if isinstance(body, str) else None)
        except Exception as exc:
            self._last_response = None
            self._last_request_info = {"method": method, "path": path, "error": str(exc)}
            return ActResult(ok=False, failure_hint=f"{method} {path} failed: {exc}")

        self._last_response = resp
        self._last_request_info = {"method": method, "path": path, "status_code": resp.status_code}
        return ActResult(ok=True)

    async def assert_(self, step: str) -> AssertResult:
        m = _ASSERT_RE.match(step.strip())
        if m is None:
            return AssertResult(ok=False, failure_hint=f"unrecognised assert step: {step!r}")

        target = m.group("target").lower()
        op = (m.group("op") or "is").lower()
        expected_raw = (m.group("expected") or "").strip()

        # "code" is a special target
        if target == "code":
            return self._assert_code(op, expected_raw)

        # "body" or "body.xxx" targets
        if target.startswith("body"):
            return self._assert_body(target, op, expected_raw)

        return AssertResult(
            ok=False,
            failure_hint=f"unsupported assert target: {target!r}",
        )

    def _assert_code(self, op: str, expected_raw: str) -> AssertResult:
        if self._last_response is None:
            return AssertResult(ok=False, failure_hint="no response to assert on")

        try:
            expected_val = int(expected_raw)
        except ValueError:
            return AssertResult(
                ok=False,
                failure_hint=f"expected status code must be int, got {expected_raw!r}",
            )

        actual = self._last_response.status_code
        ok = self._compare(actual, op, expected_val)
        if not ok:
            return AssertResult(
                ok=False,
                failure_hint=f"status code {actual} {op} {expected_val} is false",
            )
        return AssertResult(ok=True)

    def _assert_body(self, target: str, op: str, expected_raw: str) -> AssertResult:
        if self._last_response is None:
            return AssertResult(ok=False, failure_hint="no response to assert on")

        body = self._safe_json_body(self._last_response)
        if body is None:
            return AssertResult(ok=False, failure_hint="response body is not JSON")

        # target = "body" or "body.x.y.z"
        if target == "body":
            value = body
        else:
            # strip "body." prefix and walk
            path = target[5:]  # after "body."
            value = self._walk_path(body, path)
            if value is self._NOT_FOUND:
                return AssertResult(
                    ok=False,
                    failure_hint=f"JSON path '{path}' not found in body",
                )

        expected = self._coerce(expected_raw)
        ok = self._compare(value, op, expected)
        if not ok:
            return AssertResult(
                ok=False,
                failure_hint=f"body {target} = {value!r} {op} {expected!r} is false",
            )
        return AssertResult(ok=True)

    def _compare(self, actual: Any, op: str, expected: Any) -> bool:
        if op in ("is", "equals", "=="):
            return actual == expected
        if op == "!=":
            return actual != expected
        if op == ">":
            return actual is not None and expected is not None and actual > expected
        if op == "<":
            return actual is not None and expected is not None and actual < expected
        if op == ">=":
            return actual is not None and expected is not None and actual >= expected
        if op == "<=":
            return actual is not None and expected is not None and actual <= expected
        if op == "contains":
            if isinstance(actual, str) and isinstance(expected, str):
                return expected in actual
            if isinstance(actual, list):
                return expected in actual
            if isinstance(actual, dict):
                return expected in actual.values()
            return False
        return False

    def _coerce(self, raw: str) -> Any:
        raw = raw.strip()
        # JSON literal
        if raw == "true":
            return True
        if raw == "false":
            return False
        if raw == "null" or raw == "None":
            return None
        # Quoted string
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            return raw[1:-1]
        # Integer
        try:
            return int(raw)
        except ValueError:
            pass
        # Float
        try:
            return float(raw)
        except ValueError:
            pass
        return raw

    _NOT_FOUND = object()

    def _walk_path(self, obj: Any, path: str) -> Any:
        parts = path.split(".")
        current = obj
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part, self._NOT_FOUND)
            elif isinstance(current, list):
                try:
                    idx = int(part)
                    current = current[idx] if 0 <= idx < len(current) else self._NOT_FOUND
                except ValueError:
                    return self._NOT_FOUND
            else:
                return self._NOT_FOUND
            if current is self._NOT_FOUND:
                return self._NOT_FOUND
        return current

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
