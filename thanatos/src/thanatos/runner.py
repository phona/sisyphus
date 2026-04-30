"""Scenario runner.

M1 dispatch: load skill → pick driver → preflight → for each scenario
execute given/when steps via driver.act and then steps via driver.assert_.
On any assertion failure capture_evidence is called and the scenario is
marked failed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thanatos.drivers import AdbDriver, Driver, HttpDriver, PlaywrightDriver
from thanatos.result import Evidence, ScenarioResult, StepResult
from thanatos.scenario import parse_spec_file
from thanatos.skill import load_skill

if TYPE_CHECKING:
    pass


def _pick_driver(driver_name: str) -> Driver:
    if driver_name == "playwright":
        return PlaywrightDriver()
    if driver_name == "adb":
        return AdbDriver()
    if driver_name == "http":
        return HttpDriver()
    raise ValueError(f"unknown driver: {driver_name!r}")


async def run_scenario(
    skill_path: str, spec_path: str, scenario_id: str, endpoint: str
) -> ScenarioResult:
    """Run a single scenario by id."""
    skill = load_skill(skill_path)
    parsed = parse_spec_file(spec_path)
    found = next((s for s in parsed if s.scenario_id == scenario_id), None)
    if found is None:
        return ScenarioResult(
            scenario_id=scenario_id,
            passed=False,
            failure_hint=f"scenario id {scenario_id!r} not found in {spec_path}",
        )

    driver = _pick_driver(skill.driver)

    # Preflight
    preflight = await driver.preflight(endpoint)
    if not preflight.ok:
        return ScenarioResult(
            scenario_id=scenario_id,
            passed=False,
            failure_hint=f"preflight failed: {preflight.failure_hint}",
        )

    steps: list[StepResult] = []

    # Execute given steps
    for step in found.given:
        act_result = await driver.act(step)
        steps.append(
            StepResult(
                step=step,
                ok=act_result.ok,
                evidence=Evidence() if act_result.ok else await driver.capture_evidence(),
            )
        )
        if not act_result.ok:
            return ScenarioResult(
                scenario_id=scenario_id,
                passed=False,
                failure_hint=f"GIVEN step failed: {act_result.failure_hint}",
                steps=steps,
            )

    # Execute when steps
    for step in found.when:
        act_result = await driver.act(step)
        steps.append(
            StepResult(
                step=step,
                ok=act_result.ok,
                evidence=Evidence() if act_result.ok else await driver.capture_evidence(),
            )
        )
        if not act_result.ok:
            return ScenarioResult(
                scenario_id=scenario_id,
                passed=False,
                failure_hint=f"WHEN step failed: {act_result.failure_hint}",
                steps=steps,
            )

    # Execute then steps
    for step in found.then:
        assert_result = await driver.assert_(step)
        if not assert_result.ok:
            evidence = await driver.capture_evidence()
            steps.append(
                StepResult(
                    step=step,
                    ok=False,
                    evidence=evidence,
                )
            )
            return ScenarioResult(
                scenario_id=scenario_id,
                passed=False,
                failure_hint=f"THEN step failed: {assert_result.failure_hint}",
                steps=steps,
            )
        steps.append(StepResult(step=step, ok=True))

    return ScenarioResult(
        scenario_id=scenario_id,
        passed=True,
        steps=steps,
    )


async def run_all(skill_path: str, spec_path: str, endpoint: str) -> list[ScenarioResult]:
    """Run every scenario in a spec."""
    parsed = parse_spec_file(spec_path)
    return [
        await run_scenario(skill_path, spec_path, p.scenario_id, endpoint) for p in parsed
    ]


def recall(skill_path: str, intent: str) -> list[dict]:
    """Look up product knowledge fragments matching an intent.

    Searches all ``.md`` files in the same directory as ``skill.yaml`` and
    returns snippets ranked by keyword overlap with *intent*.
    """
    from pathlib import Path

    skill_dir = Path(skill_path).parent
    if not skill_dir.is_dir():
        return []

    intent_words = {w.lower() for w in intent.split() if len(w) > 2}
    if not intent_words:
        return []

    hits: list[tuple[float, dict]] = []

    for md_path in skill_dir.glob("*.md"):
        text = md_path.read_text(encoding="utf-8")
        # Split into chunks by double-newline or headings
        chunks = _split_into_chunks(text)
        for chunk in chunks:
            if not chunk.strip():
                continue
            score = _score_chunk(chunk, intent_words)
            if score > 0:
                mtime = md_path.stat().st_mtime
                hits.append(
                    (
                        score,
                        {
                            "kind": md_path.name,
                            "snippet": chunk.strip()[:800],
                            "freshness": mtime,
                        },
                    )
                )

    hits.sort(key=lambda x: x[0], reverse=True)
    return [h[1] for h in hits[:10]]


def _split_into_chunks(text: str) -> list[str]:
    """Split markdown text into chunks by headings or double newlines."""
    import re

    # Split on markdown headings first
    heading_split = re.split(r"\n(?=#+\s+)", text)
    chunks: list[str] = []
    for part in heading_split:
        # Further split on double newlines for long sections
        sub = [s.strip() for s in part.split("\n\n") if s.strip()]
        chunks.extend(sub)
    return chunks


def _score_chunk(chunk: str, intent_words: set[str]) -> float:
    """Simple TF overlap score between chunk and intent words."""
    chunk_words = {w.lower() for w in chunk.split() if len(w) > 2}
    if not chunk_words:
        return 0.0
    overlap = intent_words & chunk_words
    return len(overlap) / len(intent_words)
