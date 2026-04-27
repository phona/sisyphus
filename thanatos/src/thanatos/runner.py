"""Scenario runner.

M0 dispatch: load skill → pick driver class by ``skill.driver`` → return a
``ScenarioResult`` with ``passed=False`` and a ``failure_hint`` explaining the
M0 stub. The scenario parser *is* called for real (so a malformed spec.md
errors before MCP returns), but no driver method is invoked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thanatos.drivers import AdbDriver, Driver, HttpDriver, PlaywrightDriver
from thanatos.result import ScenarioResult
from thanatos.scenario import parse_spec_file
from thanatos.skill import load_skill

if TYPE_CHECKING:
    pass

_M0_HINT = "M0: thanatos scaffold only, drivers not implemented"


def _pick_driver(driver_name: str) -> Driver:
    if driver_name == "playwright":
        return PlaywrightDriver()
    if driver_name == "adb":
        return AdbDriver()
    if driver_name == "http":
        return HttpDriver()
    raise ValueError(f"unknown driver: {driver_name!r}")


def run_scenario(
    skill_path: str, spec_path: str, scenario_id: str, endpoint: str
) -> ScenarioResult:
    """Run a single scenario by id. M0: stub — parser runs, drivers don't."""
    skill = load_skill(skill_path)
    parsed = parse_spec_file(spec_path)
    found = next((s for s in parsed if s.scenario_id == scenario_id), None)
    if found is None:
        return ScenarioResult(
            scenario_id=scenario_id,
            passed=False,
            failure_hint=f"scenario id {scenario_id!r} not found in {spec_path}",
        )
    # In M1 we'd: pick driver, preflight, then for each step act/assert with
    # capture_evidence on failure. M0 records the dispatch decision without
    # actually invoking the driver.
    _ = _pick_driver(skill.driver)
    return ScenarioResult(
        scenario_id=scenario_id,
        passed=False,
        failure_hint=_M0_HINT,
    )


def run_all(skill_path: str, spec_path: str, endpoint: str) -> list[ScenarioResult]:
    """Run every scenario in a spec. M0: parses then stubs each one."""
    parsed = parse_spec_file(spec_path)
    return [
        run_scenario(skill_path, spec_path, p.scenario_id, endpoint) for p in parsed
    ]


def recall(skill_path: str, intent: str) -> list[dict]:
    """Look up product knowledge by intent. M0: always returns empty list."""
    _ = (skill_path, intent)
    return []
