#!/usr/bin/env python3
"""Static lint for the verifier prompt suite.

Guards three drift modes that historically caused #802-style stuck verifier
issues (REQ-fix-verifier-schema-395-1777869659, refs phona/sisyphus#395):

1. ``_decision.md.j2`` MUST keep all 4 valid action literals as JSON examples
   (``"pass"``, ``"fix"``, ``"escalate"``, ``"retry"``). If one drifts out, the
   schema docs in the prompt no longer match what ``router.validate_decision``
   accepts and agents start emitting unparseable variants.
2. ``_decision.md.j2`` MUST keep the three mandate phrases that the prompt
   suite (and the matching webhook retry follow-up) tells agents to honor:
   ``HARD CONSTRAINT``, the BKD ``decision:`` tag mandate, and the
   ``最后一条 assistant message`` rule.
3. Every per-stage verifier prompt (file not starting with ``_``) MUST
   ``{% include "verifier/_decision.md.j2" %}`` so the schema rendering is
   identical across all stage / trigger combinations.

Stdlib-only so CI can run it without extra deps. Exits non-zero on any
violation; prints one diagnostic line per violation, plus a final ``OK`` line
when clean.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIR = REPO_ROOT / "orchestrator" / "src" / "orchestrator" / "prompts" / "verifier"
DECISION_FILE = "_decision.md.j2"

# JSON literals (with surrounding double-quotes so we match JSON examples,
# not stray prose like "and pass it on").
REQUIRED_ACTION_LITERALS = ('"pass"', '"fix"', '"escalate"', '"retry"')

# Mandate phrases that must appear at least once inside _decision.md.j2.
REQUIRED_MANDATE_PHRASES = (
    "HARD CONSTRAINT",
    "decision:",  # plain BKD tag mandate (REQ-fix-verifier-decision-tag)
    "最后一条 assistant message",
)

# Jinja include directive the per-stage prompts must use. Match relaxed on
# whitespace inside braces but require the exact path.
INCLUDE_RE = re.compile(
    r"""\{\%\s*include\s+["']verifier/_decision\.md\.j2["']\s*\%\}""",
)


def _violations() -> list[str]:
    out: list[str] = []
    if not PROMPT_DIR.is_dir():
        return [f"FATAL: prompt dir not found: {PROMPT_DIR}"]

    decision_path = PROMPT_DIR / DECISION_FILE
    if not decision_path.is_file():
        out.append(f"FATAL: missing required partial: {decision_path}")
    else:
        text = decision_path.read_text(encoding="utf-8")
        for lit in REQUIRED_ACTION_LITERALS:
            if lit not in text:
                out.append(
                    f"{DECISION_FILE}: missing required action literal: {lit}",
                )
        for phrase in REQUIRED_MANDATE_PHRASES:
            if phrase not in text:
                out.append(
                    f"{DECISION_FILE}: missing mandate phrase: {phrase}",
                )

    stage_files = sorted(
        p for p in PROMPT_DIR.glob("*.md.j2") if not p.name.startswith("_")
    )
    if not stage_files:
        out.append(
            f"FATAL: no per-stage verifier prompts found under {PROMPT_DIR}",
        )
    for p in stage_files:
        text = p.read_text(encoding="utf-8")
        if not INCLUDE_RE.search(text):
            out.append(f"{p.name}: missing decision include")

    return out


def main(argv: list[str]) -> int:
    violations = _violations()
    for v in violations:
        print(v)
    if violations:
        return 1
    stage_count = sum(
        1 for p in PROMPT_DIR.glob("*.md.j2") if not p.name.startswith("_")
    )
    print(f"OK: {stage_count} verifier stage prompt(s) checked, {DECISION_FILE} contract intact")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
