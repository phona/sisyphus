"""Contract tests for REQ-thanatos-m1-impl-1777389456 — THAN-M1-S1 through THAN-M1-S6.

Black-box only: public API, driver protocol, and runner behavior. No internal
implementation details. All tests marked @pytest.mark.integration.

Derived from:
  openspec/changes/REQ-thanatos-m1-impl-1777389456/specs/thanatos/spec.md
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

import httpx
import pytest

# ─── Helpers ─────────────────────────────────────────────────────────────────


class _MockHandler(BaseHTTPRequestHandler):
    """Base handler that suppresses request logging."""

    def log_message(self, format, *args):
        pass


class _Healthz200Handler(_MockHandler):
    """Returns 200 on /healthz, 404 on everything else."""

    def do_GET(self):
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


class _All200Handler(_MockHandler):
    """Returns 200 for any GET or POST."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        self.send_response(200)
        self.end_headers()


class _CaptureRequestHandler(_MockHandler):
    """Captures the last request for inspection."""

    last_request: dict | None = None

    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b""
        _CaptureRequestHandler.last_request = {
            "method": "POST",
            "path": self.path,
            "body": body,
        }
        self.send_response(200)
        self.end_headers()


class _JSONResponseHandler(_MockHandler):
    """Returns configurable JSON responses."""

    responses: ClassVar[dict[str, tuple[int, dict]]] = {}

    def do_GET(self):
        status, data = self.responses.get(self.path, (200, {}))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


class _ThreadedMockServer:
    def __init__(self, handler_class):
        self.server = HTTPServer(("127.0.0.1", 0), handler_class)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"


def _make_skill_file(path, driver: str = "http", entry: str = ""):
    path.write_text(
        f"name: test-skill\ndriver: {driver}\nentry: {entry}\n",
        encoding="utf-8",
    )


def _make_spec_file(path, scenario_id: str, given: list[str], when: list[str], then: list[str]):
    lines = [f"#### Scenario: {scenario_id}", ""]
    for step in given:
        lines.append(f"- **GIVEN** {step}")
    for step in when:
        lines.append(f"- **WHEN** {step}")
    for step in then:
        lines.append(f"- **THEN** {step}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─── THAN-M1-S1: HTTP preflight returns ok on 200 healthz ────────────────────


@pytest.mark.integration
async def test_than_m1_s1_http_preflight_healthz_200():
    """THAN-M1-S1: HttpDriver.preflight returns ok=True when /healthz returns 200."""
    from thanatos.drivers import HttpDriver
    from thanatos.drivers.base import PreflightResult

    driver = HttpDriver()
    mock_transport = httpx.MockTransport(lambda req: httpx.Response(200))
    mock_client = httpx.AsyncClient(transport=mock_transport)
    driver._client = mock_client
    try:
        result = await driver.preflight("http://localhost:8080")
        assert isinstance(result, PreflightResult)
        assert result.ok is True
    finally:
        await mock_client.aclose()


# ─── THAN-M1-S2: HTTP act executes POST with JSON body ───────────────────────


@pytest.mark.integration
async def test_than_m1_s2_http_act_post_json():
    """THAN-M1-S2: act('POST /api/order with body {"id":1}') sends correct request."""
    from thanatos.drivers import HttpDriver

    captured: dict = {}

    def handler(req):
        captured["method"] = req.method
        captured["path"] = req.url.path
        captured["body"] = req.content
        return httpx.Response(200)

    driver = HttpDriver()
    mock_transport = httpx.MockTransport(handler)
    mock_client = httpx.AsyncClient(transport=mock_transport)
    driver._client = mock_client
    try:
        result = await driver.act('POST /api/order with body {"id":1}')
        assert result.ok is True
        assert captured.get("method") == "POST"
        assert captured.get("path") == "/api/order"
        assert json.loads(captured.get("body", b"{}")) == {"id": 1}
    finally:
        await mock_client.aclose()


# ─── THAN-M1-S3: HTTP assert_ checks status code ─────────────────────────────


@pytest.mark.integration
async def test_than_m1_s3_http_assert_status_code():
    """THAN-M1-S3: assert_('response code is 201') returns ok=True when response is 201."""
    from thanatos.drivers import HttpDriver

    driver = HttpDriver()
    mock_transport = httpx.MockTransport(lambda req: httpx.Response(201))
    mock_client = httpx.AsyncClient(transport=mock_transport)
    driver._client = mock_client
    try:
        await driver.act("GET /something")
        result = await driver.assert_("response code is 201")
        assert result.ok is True
    finally:
        await mock_client.aclose()


# ─── THAN-M1-S4: HTTP assert_ checks JSON body path ──────────────────────────


@pytest.mark.integration
async def test_than_m1_s4_http_assert_json_path():
    """THAN-M1-S4: assert_('response body.order.id is 42') returns ok=True."""
    from thanatos.drivers import HttpDriver

    driver = HttpDriver()
    mock_transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"order": {"id": 42}})
    )
    mock_client = httpx.AsyncClient(transport=mock_transport)
    driver._client = mock_client
    try:
        await driver.act("GET /something")
        result = await driver.assert_("response body.order.id is 42")
        assert result.ok is True
    finally:
        await mock_client.aclose()


# ─── THAN-M1-S5: runner executes all steps and reports pass ──────────────────


@pytest.mark.integration
async def test_than_m1_s5_runner_all_steps_pass(tmp_path):
    """THAN-M1-S5: run_all with a passing scenario returns passed=True with step results."""
    from thanatos.runner import run_all

    server = _ThreadedMockServer(_All200Handler).start()
    try:
        skill_path = tmp_path / "skill.yaml"
        _make_skill_file(skill_path, driver="http", entry=server.url)

        spec_path = tmp_path / "spec.md"
        _make_spec_file(
            spec_path,
            scenario_id="TEST-PASS-S1",
            given=[f"GET {server.url}/healthz"],
            when=[f"POST {server.url}/api/test with body {{}}"],
            then=["response code is 200"],
        )

        results = await run_all(str(skill_path), str(spec_path), server.url)
        assert len(results) == 1
        result = results[0]
        assert result.passed is True
        assert len(result.steps) == 3
        assert all(s.ok for s in result.steps)
    finally:
        server.stop()


# ─── THAN-M1-S6: runner captures evidence on assert failure ──────────────────


@pytest.mark.integration
async def test_than_m1_s6_runner_captures_evidence_on_failure(tmp_path):
    """THAN-M1-S6: run_scenario with a failing THEN step returns passed=False and attaches evidence."""
    from thanatos.runner import run_scenario

    server = _ThreadedMockServer(_Healthz200Handler).start()
    try:
        skill_path = tmp_path / "skill.yaml"
        _make_skill_file(skill_path, driver="http", entry=server.url)

        spec_path = tmp_path / "spec.md"
        _make_spec_file(
            spec_path,
            scenario_id="TEST-FAIL-S1",
            given=[f"GET {server.url}/healthz"],
            when=[],
            then=["response code is 404"],
        )

        result = await run_scenario(str(skill_path), str(spec_path), "TEST-FAIL-S1", server.url)
        assert result.passed is False
        assert result.steps
        failing_step = result.steps[-1]
        assert failing_step.ok is False
        assert failing_step.evidence is not None
    finally:
        server.stop()
