"""Unit tests for HTTP driver — mock httpx.

All tests mock httpx.AsyncClient to avoid real network calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from thanatos.drivers.http import HttpDriver


@pytest.fixture
def driver():
    return HttpDriver()


@pytest.fixture
def mock_response():
    """Return a mock httpx.Response with configurable attributes."""
    def _make(status_code=200, json_data=None, headers=None, text=""):
        resp = MagicMock()
        resp.status_code = status_code
        resp.headers = headers or {}
        if json_data is not None:
            resp.json.return_value = json_data
        else:
            resp.json.side_effect = Exception("not json")
        resp.text = text
        return resp
    return _make


@pytest.fixture
def mock_client(mock_response):
    """Patch httpx.AsyncClient on the driver instance."""
    def _patch(driver: HttpDriver, response: Any = None, request_exc: Exception | None = None):
        client = AsyncMock()
        if request_exc:
            client.request.side_effect = request_exc
            client.get.side_effect = request_exc
        else:
            client.request.return_value = response
            client.get.return_value = response
        driver._client = client
        return client
    return _patch


# ─── preflight ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preflight_ok(driver, mock_response, mock_client):
    resp = mock_response(status_code=200, json_data={"status": "ok"})
    mock_client(driver, resp)

    result = await driver.preflight("http://example.com")
    assert result.ok is True
    assert result.failure_hint is None


@pytest.mark.asyncio
async def test_preflight_bad_status(driver, mock_response, mock_client):
    resp = mock_response(status_code=503)
    mock_client(driver, resp)

    result = await driver.preflight("http://example.com")
    assert result.ok is False
    assert "503" in (result.failure_hint or "")


@pytest.mark.asyncio
async def test_preflight_network_error(driver, mock_client):
    mock_client(driver, request_exc=ConnectionError("refused"))

    result = await driver.preflight("http://example.com")
    assert result.ok is False
    assert "refused" in (result.failure_hint or "")


# ─── observe ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def testobserve_no_response(driver):
    tree = await driver.observe()
    assert tree.kind == "http"
    assert tree.payload == {}


@pytest.mark.asyncio
async def test_observe_with_response(driver, mock_response, mock_client):
    resp = mock_response(
        status_code=201,
        json_data={"id": 42},
        headers={"content-type": "application/json"},
    )
    mock_client(driver, resp)

    # seed last_response via act
    await driver.act("POST /api/order with body {}")
    tree = await driver.observe()
    assert tree.kind == "http"
    assert tree.payload["status_code"] == 201
    assert tree.payload["body"] == {"id": 42}


# ─── act ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_act_post_with_body(driver, mock_response, mock_client):
    resp = mock_response(status_code=200, json_data={"ok": True})
    client = mock_client(driver, resp)

    result = await driver.act('POST /api/v1/order with body {"foo":"bar"}')
    assert result.ok is True
    client.request.assert_awaited_once_with(
        "POST", "/api/v1/order",
        json={"foo": "bar"}, content=None,
    )


@pytest.mark.asyncio
async def test_act_get_path(driver, mock_response, mock_client):
    resp = mock_response(status_code=200, json_data={"id": 1})
    client = mock_client(driver, resp)

    result = await driver.act("GET /api/v1/order/123")
    assert result.ok is True
    client.request.assert_awaited_once_with(
        "GET", "/api/v1/order/123",
        json=None, content=None,
    )


@pytest.mark.asyncio
async def test_act_unrecognised_step(driver):
    result = await driver.act("Something weird")
    assert result.ok is False
    assert "unrecognised" in (result.failure_hint or "").lower()


@pytest.mark.asyncio
async def test_act_network_error(driver, mock_client):
    mock_client(driver, request_exc=TimeoutError("slow"))

    result = await driver.act("GET /api/v1/order")
    assert result.ok is False
    assert "slow" in (result.failure_hint or "")


# ─── assert_ ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assert_code_equals(driver, mock_response, mock_client):
    resp = mock_response(status_code=200)
    mock_client(driver, resp)
    await driver.act("GET /api/v1/order")

    result = await driver.assert_("Then response code is 200")
    assert result.ok is True


@pytest.mark.asyncio
async def test_assert_code_not_equals(driver, mock_response, mock_client):
    resp = mock_response(status_code=404)
    mock_client(driver, resp)
    await driver.act("GET /api/v1/order")

    result = await driver.assert_("Then response code is 200")
    assert result.ok is False
    assert "404" in (result.failure_hint or "")


@pytest.mark.asyncio
async def test_assert_body_jsonpath(driver, mock_response, mock_client):
    resp = mock_response(status_code=200, json_data={"order": {"id": 42}})
    mock_client(driver, resp)
    await driver.act("GET /api/v1/order")

    result = await driver.assert_("Then response body.order.id is 42")
    assert result.ok is True


@pytest.mark.asyncio
async def test_assert_body_contains(driver, mock_response, mock_client):
    resp = mock_response(status_code=200, json_data={"status": "success"})
    mock_client(driver, resp)
    await driver.act("GET /api/v1/order")

    result = await driver.assert_("Then response body contains success")
    assert result.ok is True


@pytest.mark.asyncio
async def test_assert_no_response(driver):
    result = await driver.assert_("Then response code is 200")
    assert result.ok is False
    assert "no response" in (result.failure_hint or "").lower()


@pytest.mark.asyncio
async def test_assert_unrecognised_target(driver, mock_response, mock_client):
    resp = mock_response(status_code=200)
    mock_client(driver, resp)
    await driver.act("GET /api/v1/order")

    result = await driver.assert_("Then response header x-foo is bar")
    assert result.ok is False
    assert "unrecognised" in (result.failure_hint or "").lower()


# ─── capture_evidence ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_capture_evidence_empty(driver):
    evidence = await driver.capture_evidence()
    assert evidence.network == []


@pytest.mark.asyncio
async def test_capture_evidence_with_request(driver, mock_response, mock_client):
    resp = mock_response(status_code=200, json_data={"ok": True}, headers={"h": "v"})
    mock_client(driver, resp)
    await driver.act("POST /api/v1/order with body {}")

    evidence = await driver.capture_evidence()
    assert len(evidence.network) == 1
    entry = evidence.network[0]
    assert entry["request"]["method"] == "POST"
    assert entry["response"]["status_code"] == 200
    assert entry["response"]["body"] == {"ok": True}


# ─── coercion / comparison edge cases ───────────────────────────────────────


@pytest.mark.asyncio
async def test_assert_coerce_string_quoted(driver, mock_response, mock_client):
    resp = mock_response(status_code=200, json_data={"name": "alice"})
    mock_client(driver, resp)
    await driver.act("GET /api/v1/user")

    result = await driver.assert_('Then response body.name is "alice"')
    assert result.ok is True


@pytest.mark.asyncio
async def test_assert_coerce_boolean(driver, mock_response, mock_client):
    resp = mock_response(status_code=200, json_data={"active": True})
    mock_client(driver, resp)
    await driver.act("GET /api/v1/user")

    result = await driver.assert_("Then response body.active is true")
    assert result.ok is True
