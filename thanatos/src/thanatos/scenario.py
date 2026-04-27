"""Parse `#### Scenario:` blocks out of an openspec spec.md.

Two block formats are supported:

A. gherkin code-block (API/back-end specs)::

    #### Scenario: REQ-1004-S1 — short description
    ```gherkin
    Given foo
    When bar
    Then baz
    ```

B. markdown bullet (UI specs)::

    #### Scenario: Desktop collapse/expand
    - **GIVEN** ...
    - **WHEN** ...
    - **THEN** ...

The two formats are mutually exclusive within a single block. Mixing them
raises ``ScenarioFormatError``. Empty blocks (no GIVEN at all) raise
``EmptyScenarioError``. Duplicate scenario ids raise ``ScenarioFormatError``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Heading: `#### Scenario: <ID>[ <description>]`
# - exactly four `#`, exactly the literal "Scenario:" (case-sensitive — sisyphus
#   check-scenario-refs.sh is also case-sensitive on this token)
# - id = first whitespace-separated token after the colon
# - everything after the id is the description (with an optional em-dash /
#   hyphen separator stripped at the start)
_SCENARIO_HEADING = re.compile(
    r"^####\s+Scenario:\s+(?P<id>\S+)(?:\s+(?P<rest>.*?))?\s*$"
)
_DESC_SEPARATOR = re.compile(r"^[—-]\s+")

# Bullet step:  - **GIVEN** ...   |   - **When** ...
_BULLET_STEP = re.compile(
    r"^\s*-\s+\*\*(?P<kw>given|when|then|and|but)\*\*\s+(?P<text>.+?)\s*$",
    re.IGNORECASE,
)

# Plain gherkin step inside a fenced code block
_GHERKIN_STEP = re.compile(
    r"^\s*(?P<kw>given|when|then|and|but)\b\s+(?P<text>.+?)\s*$",
    re.IGNORECASE,
)


class ScenarioParseError(Exception):
    """Base class for scenario.py parser errors."""


class ScenarioFormatError(ScenarioParseError):
    """Block uses both gherkin and bullet, or two blocks share an id."""


class EmptyScenarioError(ScenarioParseError):
    """Block has no recognisable GIVEN/WHEN/THEN steps at all."""


SourceFormat = Literal["gherkin", "bullet"]


@dataclass
class ParsedScenario:
    scenario_id: str
    description: str
    given: list[str] = field(default_factory=list)
    when: list[str] = field(default_factory=list)
    then: list[str] = field(default_factory=list)
    source_format: SourceFormat = "bullet"


def parse_spec_text(text: str) -> list[ParsedScenario]:
    """Parse spec markdown text and return all scenarios in source order.

    Lines inside fenced code blocks that aren't ``gherkin`` (e.g. ``json``,
    ``yaml``, ``python``) are skipped — only ``#### Scenario:`` headings sitting
    at top-level markdown count, and only ``gherkin`` fences inside an active
    scenario block contribute steps.
    """
    lines = text.splitlines()
    scenarios: list[ParsedScenario] = []
    seen_ids: dict[str, int] = {}  # id -> first line number

    i = 0
    in_code_fence: str | None = None  # info string, e.g. "gherkin" / "json" / ""
    fence_start_line = 0
    while i < len(lines):
        raw = lines[i]
        line_no = i + 1

        # Track fenced code blocks at top-level: a line starting with ``` opens
        # or closes a fence. We *don't* match `#### Scenario:` while inside a
        # non-gherkin fence (that's just example markdown / json).
        stripped = raw.lstrip()
        if stripped.startswith("```"):
            info = stripped[3:].strip()
            if in_code_fence is None:
                in_code_fence = info
                fence_start_line = line_no
            else:
                in_code_fence = None
            i += 1
            continue

        if in_code_fence is not None:
            # heading inside a fence is just example text — skip
            i += 1
            continue

        m = _SCENARIO_HEADING.match(raw)
        if m is None:
            i += 1
            continue

        scen_id = m.group("id")
        rest = (m.group("rest") or "").strip()
        desc = _DESC_SEPARATOR.sub("", rest, count=1).strip()

        if scen_id in seen_ids:
            raise ScenarioFormatError(
                f"duplicate scenario id {scen_id!r}: "
                f"first at line {seen_ids[scen_id]}, again at line {line_no}"
            )
        seen_ids[scen_id] = line_no

        scen, consumed = _parse_block_body(lines, i + 1, scen_id, desc)
        scenarios.append(scen)
        i = consumed

    if in_code_fence is not None:
        # unterminated fence isn't fatal for scenario parsing — just warn-by-ignore
        del fence_start_line  # keep `fence_start_line` referenced
    return scenarios


def parse_spec_file(path: str | Path) -> list[ParsedScenario]:
    """Convenience wrapper: read a path and feed it to :func:`parse_spec_text`."""
    return parse_spec_text(Path(path).read_text(encoding="utf-8"))


def _parse_block_body(
    lines: list[str], start: int, scen_id: str, desc: str
) -> tuple[ParsedScenario, int]:
    """Parse the body of a scenario block until the next ``####``-heading or EOF."""
    given: list[str] = []
    when: list[str] = []
    then: list[str] = []
    saw_bullet = False
    saw_gherkin = False
    in_gherkin_fence = False
    in_other_fence = False

    j = start
    while j < len(lines):
        raw = lines[j]
        stripped = raw.lstrip()

        # next scenario / next top-level heading at same depth ends this block
        if not in_gherkin_fence and not in_other_fence:
            if stripped.startswith("#### ") or stripped.startswith("### ") or stripped.startswith("## ") or stripped.startswith("# "):
                break

        if stripped.startswith("```"):
            info = stripped[3:].strip().lower()
            if not in_gherkin_fence and not in_other_fence:
                if info == "gherkin":
                    in_gherkin_fence = True
                    saw_gherkin = True
                else:
                    in_other_fence = True
            elif in_gherkin_fence:
                in_gherkin_fence = False
            elif in_other_fence:
                in_other_fence = False
            j += 1
            continue

        if in_gherkin_fence:
            m = _GHERKIN_STEP.match(raw)
            if m is not None:
                _route_step(m.group("kw"), m.group("text"), given, when, then)
            j += 1
            continue

        if in_other_fence:
            j += 1
            continue

        m = _BULLET_STEP.match(raw)
        if m is not None:
            saw_bullet = True
            _route_step(m.group("kw"), m.group("text"), given, when, then)

        j += 1

    if saw_bullet and saw_gherkin:
        raise ScenarioFormatError(
            f"scenario {scen_id!r} mixes gherkin code-block and bullet steps "
            f"(line {start})"
        )

    if not (given or when or then):
        raise EmptyScenarioError(
            f"scenario {scen_id!r} has no GIVEN/WHEN/THEN steps (line {start})"
        )

    return (
        ParsedScenario(
            scenario_id=scen_id,
            description=desc,
            given=given,
            when=when,
            then=then,
            source_format="gherkin" if saw_gherkin else "bullet",
        ),
        j,
    )


def _route_step(
    kw: str, text: str, given: list[str], when: list[str], then: list[str]
) -> None:
    kw_l = kw.lower()
    if kw_l == "given":
        given.append(text)
    elif kw_l == "when":
        when.append(text)
    elif kw_l == "then":
        then.append(text)
    elif kw_l in ("and", "but"):
        # "And"/"But" tail-extend whichever bucket we last filled. If nothing has
        # been seen yet, default to GIVEN — same behaviour as cucumber.
        if then:
            then.append(text)
        elif when:
            when.append(text)
        else:
            given.append(text)
