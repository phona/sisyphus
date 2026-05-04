"""recall — surface product knowledge fragments to whichever agent asks."""

from __future__ import annotations

from pathlib import Path

import pytest

from thanatos.runner import recall


@pytest.fixture
def skill_dir(tmp_path: Path) -> Path:
    """A fake .thanatos/ skill directory with a few knowledge files."""
    d = tmp_path / ".thanatos"
    d.mkdir()
    (d / "skill.yaml").write_text("name: t\ndriver: adb\nentry: foo\n", encoding="utf-8")
    (d / "anchors.md").write_text(
        "## Login page widgets\n\n"
        "| name | content-desc |\n"
        "| login button | Login |\n"
        "| forgot password link | forgot password |\n",
        encoding="utf-8",
    )
    (d / "flows.md").write_text(
        "## Login flow\n\n"
        "User taps the login button after entering credentials.\n",
        encoding="utf-8",
    )
    (d / "pitfalls.md").write_text(
        "---\ntags: [legacy]\n---\n\n"
        "## Known issue\n\n"
        "snackbar disappears very quickly on slow devices.\n",
        encoding="utf-8",
    )
    return d


def test_recall_finds_login_widgets(skill_dir: Path) -> None:
    hits = recall(str(skill_dir / "skill.yaml"), "login button widgets")
    assert hits, "expected at least one hit for 'login button widgets'"
    kinds = {h["kind"] for h in hits}
    assert "anchors.md" in kinds


def test_recall_returns_empty_for_unknown_intent(skill_dir: Path) -> None:
    hits = recall(str(skill_dir / "skill.yaml"), "kubernetes ingress controller")
    # No file mentions kubernetes — overlap should be zero.
    assert hits == []


def test_recall_respects_tag_filter(skill_dir: Path) -> None:
    hits = recall(str(skill_dir / "skill.yaml"), "snackbar disappears", tags=["legacy"])
    assert hits, "expected pitfalls.md to surface under tags=['legacy']"
    assert all(h["kind"] == "pitfalls.md" for h in hits)


def test_recall_filters_out_files_without_matching_tags(skill_dir: Path) -> None:
    # anchors.md / flows.md have no frontmatter → not in tag set → excluded.
    hits = recall(str(skill_dir / "skill.yaml"), "login button", tags=["legacy"])
    assert all(h["kind"] == "pitfalls.md" for h in hits)


def test_recall_returns_empty_when_skill_dir_missing(tmp_path: Path) -> None:
    hits = recall(str(tmp_path / "does-not-exist" / "skill.yaml"), "anything")
    assert hits == []


def test_recall_respects_limit(skill_dir: Path) -> None:
    hits = recall(str(skill_dir / "skill.yaml"), "login user button", limit=1)
    assert len(hits) <= 1
