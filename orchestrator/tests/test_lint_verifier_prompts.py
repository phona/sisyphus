"""Test scripts/lint-verifier-prompts.py.

REQ-fix-verifier-schema-395-1777869659 VDRC-S4..S6: the lint script must pass
on the in-tree prompt suite, and must flag the documented drift modes.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LINT_SCRIPT = REPO_ROOT / "scripts" / "lint-verifier-prompts.py"
PROMPT_DIR = REPO_ROOT / "orchestrator" / "src" / "orchestrator" / "prompts" / "verifier"


def _load_lint_module():
    """Import the lint script as a module (filename has a hyphen)."""
    spec = importlib.util.spec_from_file_location("lint_verifier_prompts", LINT_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lint_passes_on_intree_prompts(capsys):
    """VDRC-S4: HEAD prompt suite passes; final stdout line starts with `OK`."""
    mod = _load_lint_module()
    rc = mod.main([str(LINT_SCRIPT)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1].startswith("OK")


def test_lint_flags_missing_decision_include(monkeypatch, capsys):
    """VDRC-S5: removing the include from a per-stage prompt fails the lint."""
    mod = _load_lint_module()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp) / "verifier"
        shutil.copytree(PROMPT_DIR, tmp_dir)
        # nuke include from one stage prompt
        target = tmp_dir / "analyze_fail.md.j2"
        text = target.read_text(encoding="utf-8")
        new = text.replace(
            '{% include "verifier/_decision.md.j2" %}',
            "<!-- removed for test -->",
        )
        assert new != text, "fixture missing the include we expected"
        target.write_text(new, encoding="utf-8")

        monkeypatch.setattr(mod, "PROMPT_DIR", tmp_dir)
        rc = mod.main([str(LINT_SCRIPT)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "analyze_fail.md.j2" in out
        assert "missing decision include" in out


@pytest.mark.parametrize(
    "phrase", ["HARD CONSTRAINT", "decision:", "最后一条 assistant message"],
)
def test_lint_flags_missing_mandate_phrase(monkeypatch, capsys, phrase):
    """VDRC-S6: removing any of the 3 mandate phrases from _decision.md.j2 fails."""
    mod = _load_lint_module()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp) / "verifier"
        shutil.copytree(PROMPT_DIR, tmp_dir)
        decision = tmp_dir / "_decision.md.j2"
        text = decision.read_text(encoding="utf-8")
        # Replace every occurrence so the phrase is fully gone
        new = text.replace(phrase, "REMOVED")
        assert new != text, f"fixture _decision.md.j2 missing phrase {phrase!r}"
        decision.write_text(new, encoding="utf-8")

        monkeypatch.setattr(mod, "PROMPT_DIR", tmp_dir)
        rc = mod.main([str(LINT_SCRIPT)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "_decision.md.j2" in out
        assert f"missing mandate phrase: {phrase}" in out


def test_lint_flags_missing_action_literal(monkeypatch, capsys):
    """Removing one of the 4 action JSON literals from _decision.md.j2 fails."""
    mod = _load_lint_module()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp) / "verifier"
        shutil.copytree(PROMPT_DIR, tmp_dir)
        decision = tmp_dir / "_decision.md.j2"
        text = decision.read_text(encoding="utf-8")
        new = text.replace('"retry"', '"REMOVED"')
        assert new != text
        decision.write_text(new, encoding="utf-8")

        monkeypatch.setattr(mod, "PROMPT_DIR", tmp_dir)
        rc = mod.main([str(LINT_SCRIPT)])
        assert rc == 1
        out = capsys.readouterr().out
        assert 'missing required action literal: "retry"' in out


def teardown_module(_mod):
    """Drop our hand-loaded lint module so other tests don't see it cached."""
    sys.modules.pop("lint_verifier_prompts", None)
