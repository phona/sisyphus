"""Unit tests for thanatos.runner — mock driver to verify execute flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from thanatos.drivers.base import ActResult, AssertResult, Evidence, PreflightResult
from thanatos.runner import _pick_driver, run_all, run_scenario


@pytest.fixture
def fake_skill_file(tmp_path):
    p = tmp_path / ".thanatos" / "skill.yaml"
    p.parent.mkdir(parents=True)
    p.write_text(
        'name: "test-skill"\ndriver: http\nentry: /api\n',
        encoding="utf-8",
    )
    return str(p)


@pytest.fixture
def fake_spec_file(tmp_path):
    p = tmp_path / "spec.md"
    p.write_text(
        "#### Scenario: S1\n\n"
        "```gherkin\n"
        "Given setup\n"
        "When POST /api/test with body {}\n"
        "Then response code is 200\n"
        "```\n",
        encoding="utf-8",
    )
    return str(p)


@pytest.fixture
def fake_spec_multi(tmp_path):
    p = tmp_path / "spec_multi.md"
    p.write_text(
        "#### Scenario: S1\n\n"
        "```gherkin\n"
        "Given setup\n"
        "When POST /api/test with body {}\n"
        "Then response code is 200\n"
        "```\n\n"
        "#### Scenario: S2\n\n"
        "```gherkin\n"
        "Given other\n"
        "When GET /api/other\n"
        "Then response code is 404\n"
        "```\n",
        encoding="utf-8",
    )
    return str(p)


class _MockHttpDriver:
    """Stand-in for HttpDriver with async methods."""

    name = "http"

    def __init__(self) -> None:
        self.preflight = AsyncMock(return_value=PreflightResult(ok=True))
        self.act = AsyncMock(return_value=ActResult(ok=True))
        self.assert_ = AsyncMock(return_value=AssertResult(ok=True))
        self.capture_evidence = AsyncMock(return_value=Evidence(network=[{"mock": True}]))
        self.observe = AsyncMock()


@pytest.fixture
def mock_http_driver(monkeypatch):
    """Patch _pick_driver to return a fully mocked HttpDriver."""
    instance = _MockHttpDriver()

    def _pick(name: str) -> Any:
        return instance

    monkeypatch.setattr("thanatos.runner._pick_driver", _pick)
    return instance


# ─── _pick_driver ───────────────────────────────────────────────────────────


def test_pick_driver_http():
    d = _pick_driver("http")
    assert d.name == "http"


def test_pick_driver_playwright():
    d = _pick_driver("playwright")
    assert d.name == "playwright"


def test_pick_driver_unknown():
    with pytest.raises(ValueError, match="unknown driver"):
        _pick_driver("grpc")


# ─── run_scenario ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_scenario_not_found(fake_skill_file, fake_spec_file, mock_http_driver):
    result = await run_scenario(fake_skill_file, fake_spec_file, "MISSING", "http://localhost")
    assert result.passed is False
    assert "not found" in (result.failure_hint or "").lower()


@pytest.mark.asyncio
async def test_run_scenario_happy_path(fake_skill_file, fake_spec_file, mock_http_driver):
    result = await run_scenario(fake_skill_file, fake_spec_file, "S1", "http://localhost")
    assert result.passed is True
    assert result.scenario_id == "S1"
    assert len(result.steps) == 3  # given + when + then

    mock_http_driver.preflight.assert_awaited_once_with("http://localhost")
    mock_http_driver.act.assert_any_await("setup")
    mock_http_driver.act.assert_any_await("POST /api/test with body {}")
    mock_http_driver.assert_.assert_awaited_once_with("response code is 200")


@pytest.mark.asyncio
async def test_run_scenario_preflight_fail(fake_skill_file, fake_spec_file, mock_http_driver):
    mock_http_driver.preflight.return_value = PreflightResult(ok=False, failure_hint="down")

    result = await run_scenario(fake_skill_file, fake_spec_file, "S1", "http://localhost")
    assert result.passed is False
    assert "preflight failed" in (result.failure_hint or "").lower()
    mock_http_driver.act.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_scenario_given_act_fail(fake_skill_file, fake_spec_file, mock_http_driver):
    mock_http_driver.act.side_effect = [
        ActResult(ok=False, failure_hint="bad setup"),
    ]

    result = await run_scenario(fake_skill_file, fake_spec_file, "S1", "http://localhost")
    assert result.passed is False
    assert "given step failed" in (result.failure_hint or "").lower()
    assert len(result.steps) == 1
    assert result.steps[0].ok is False
    # evidence captured on failure
    mock_http_driver.capture_evidence.assert_awaited()


@pytest.mark.asyncio
async def test_run_scenario_then_assert_fail(fake_skill_file, fake_spec_file, mock_http_driver):
    mock_http_driver.assert_.return_value = AssertResult(ok=False, failure_hint="expected 201")

    result = await run_scenario(fake_skill_file, fake_spec_file, "S1", "http://localhost")
    assert result.passed is False
    assert "then step failed" in (result.failure_hint or "").lower()
    assert len(result.steps) == 3
    assert result.steps[2].ok is False
    mock_http_driver.capture_evidence.assert_awaited()


# ─── run_all ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_all_multi(fake_skill_file, fake_spec_multi, mock_http_driver):
    results = await run_all(fake_skill_file, fake_spec_multi, "http://localhost")
    assert len(results) == 2
    assert results[0].scenario_id == "S1"
    assert results[1].scenario_id == "S2"
    assert all(r.passed for r in results)


@pytest.mark.asyncio
async def test_run_all_empty_spec(tmp_path, fake_skill_file, mock_http_driver):
    empty_spec = tmp_path / "empty.md"
    empty_spec.write_text("# no scenarios\n", encoding="utf-8")

    results = await run_all(fake_skill_file, str(empty_spec), "http://localhost")
    assert results == []
