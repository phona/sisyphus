"""Tests for thanatos.scenario.parse_spec_text.

Covers gherkin / bullet / mixed-reject / case-insensitivity / multiple
GIVEN-WHEN-THEN / empty-block reject / duplicate-id reject / heading inside
fenced code-block ignored / unicode descriptions.
"""

from __future__ import annotations

import textwrap

import pytest

from thanatos.scenario import (
    EmptyScenarioError,
    ScenarioFormatError,
    parse_spec_text,
)


def _dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


def test_gherkin_codeblock_single_scenario() -> None:
    src = _dedent(
        """
        # Spec

        ## Requirement: foo

        #### Scenario: REQ-1004-S1 — happy path
        ```gherkin
        Given the system is up
        When the client posts /foo
        Then the response is 200
        ```
        """
    )
    out = parse_spec_text(src)
    assert len(out) == 1
    s = out[0]
    assert s.scenario_id == "REQ-1004-S1"
    assert s.description == "happy path"
    assert s.given == ["the system is up"]
    assert s.when == ["the client posts /foo"]
    assert s.then == ["the response is 200"]
    assert s.source_format == "gherkin"


def test_bullet_format_single_scenario() -> None:
    src = _dedent(
        """
        #### Scenario: Desktop collapse/expand
        - **GIVEN** sidebar is expanded
        - **WHEN** user clicks the toggle
        - **THEN** sidebar collapses
        """
    )
    out = parse_spec_text(src)
    assert len(out) == 1
    assert out[0].scenario_id == "Desktop"
    # description picked up from `Scenario: Desktop collapse/expand` is empty
    # (no em-dash separator). Steps are correct.
    assert out[0].source_format == "bullet"
    assert out[0].given == ["sidebar is expanded"]
    assert out[0].when == ["user clicks the toggle"]
    assert out[0].then == ["sidebar collapses"]


def test_multiple_given_when_then_accumulate() -> None:
    src = _dedent(
        """
        #### Scenario: THAN-multi
        - **GIVEN** a
        - **GIVEN** b
        - **WHEN** c
        - **WHEN** d
        - **THEN** e
        - **THEN** f
        """
    )
    s = parse_spec_text(src)[0]
    assert s.given == ["a", "b"]
    assert s.when == ["c", "d"]
    assert s.then == ["e", "f"]


def test_case_insensitive_keywords() -> None:
    src = _dedent(
        """
        #### Scenario: ci-1
        ```gherkin
        given lowercase
        WHEN UPPER
        Then Title
        ```
        """
    )
    s = parse_spec_text(src)[0]
    assert s.given == ["lowercase"]
    assert s.when == ["UPPER"]
    assert s.then == ["Title"]


def test_and_but_extends_last_bucket() -> None:
    src = _dedent(
        """
        #### Scenario: chained
        ```gherkin
        Given foo
        And bar
        When baz
        And qux
        Then result
        And another result
        ```
        """
    )
    s = parse_spec_text(src)[0]
    assert s.given == ["foo", "bar"]
    assert s.when == ["baz", "qux"]
    assert s.then == ["result", "another result"]


def test_mixed_format_within_block_rejected() -> None:
    src = _dedent(
        """
        #### Scenario: MIX-1
        - **GIVEN** mixed bullet
        ```gherkin
        When also gherkin
        Then nope
        ```
        """
    )
    with pytest.raises(ScenarioFormatError, match="mixes gherkin"):
        parse_spec_text(src)


def test_empty_scenario_rejected() -> None:
    src = _dedent(
        """
        #### Scenario: EMPTY-1 — no steps at all
        Just prose, no GIVEN.
        """
    )
    with pytest.raises(EmptyScenarioError):
        parse_spec_text(src)


def test_duplicate_scenario_id_rejected() -> None:
    src = _dedent(
        """
        #### Scenario: DUP-1
        - **GIVEN** first
        - **WHEN** wat
        - **THEN** ok

        #### Scenario: DUP-1
        - **GIVEN** second
        - **WHEN** also wat
        - **THEN** ok
        """
    )
    with pytest.raises(ScenarioFormatError, match="duplicate scenario id"):
        parse_spec_text(src)


def test_scenario_heading_inside_codeblock_ignored() -> None:
    src = _dedent(
        """
        # Some prose

        Here is an example of what scenario syntax looks like:

        ```markdown
        #### Scenario: NOT-A-REAL-ID
        - **GIVEN** this is documentation, not a real scenario
        ```

        #### Scenario: REAL-1
        - **GIVEN** real
        - **WHEN** also real
        - **THEN** real result
        """
    )
    out = parse_spec_text(src)
    assert len(out) == 1
    assert out[0].scenario_id == "REAL-1"


def test_multiple_scenarios_returned_in_source_order() -> None:
    src = _dedent(
        """
        #### Scenario: A1
        - **GIVEN** a
        - **WHEN** b
        - **THEN** c

        Some prose between scenarios.

        #### Scenario: A2
        ```gherkin
        Given x
        When y
        Then z
        ```
        """
    )
    out = parse_spec_text(src)
    assert [s.scenario_id for s in out] == ["A1", "A2"]
    assert out[0].source_format == "bullet"
    assert out[1].source_format == "gherkin"


def test_unicode_description_passes_through() -> None:
    src = _dedent(
        """
        #### Scenario: UNI-1 — 中文 desc with emoji 🚀
        - **GIVEN** unicode body 漢字
        - **WHEN** 处理
        - **THEN** 結果
        """
    )
    s = parse_spec_text(src)[0]
    assert s.description == "中文 desc with emoji 🚀"
    assert s.given == ["unicode body 漢字"]
