"""Contract tests for REQ-thanatos-m2-v2 — THAN-M2-S1 through THAN-M2-S4.

Black-box only: public API, driver protocol, and recall behavior.
All tests marked @pytest.mark.integration.
"""
from __future__ import annotations

import textwrap

import pytest

# ─── THAN-M2-S1: recall returns empty list when no knowledge files ───────────


@pytest.mark.integration
def test_than_m2_s1_recall_empty_when_no_kb(tmp_path):
    """THAN-M2-S1: recall with no .md files in skill directory returns []."""
    from thanatos.runner import recall

    skill_path = tmp_path / "skill.yaml"
    skill_path.write_text("name: test\ndriver: http\nentry: /\n", encoding="utf-8")
    result = recall(str(skill_path), "login flow")
    assert result == []


# ─── THAN-M2-S2: recall returns snippets matching intent ─────────────────────


@pytest.mark.integration
def test_than_m2_s2_recall_finds_snippets(tmp_path):
    """THAN-M2-S2: recall returns [{kind, snippet, freshness}] for matching .md files."""
    from thanatos.runner import recall

    skill_dir = tmp_path / ".thanatos"
    skill_dir.mkdir()
    skill_path = skill_dir / "skill.yaml"
    skill_path.write_text("name: test\ndriver: http\nentry: /\n", encoding="utf-8")

    anchors = skill_dir / "anchors.md"
    anchors.write_text(
        textwrap.dedent("""\
            # Anchors

            ## Login button
            The login button has resource-id `btn_login`.

            ## Search field
            The search field has resource-id `et_search`.
        """),
        encoding="utf-8",
    )

    result = recall(str(skill_path), "login button resource")
    assert len(result) > 0
    assert result[0]["kind"] == "anchors.md"
    assert "login button" in result[0]["snippet"].lower()
    assert "freshness" in result[0]


# ─── THAN-M2-S3: AdbDriver preflight returns PreflightResult ─────────────────


@pytest.mark.integration
async def test_than_m2_s3_adb_preflight_returns_typed_result():
    """THAN-M2-S3: AdbDriver.preflight returns a PreflightResult (ok=False is fine)."""
    from thanatos.drivers import AdbDriver
    from thanatos.drivers.base import PreflightResult

    driver = AdbDriver()
    result = await driver.preflight("localhost:5555")
    assert isinstance(result, PreflightResult)
    # Without a real adb/redroid instance preflight is expected to fail gracefully
    assert result.ok is False or result.a11y_node_count is not None


# ─── THAN-M2-S4: AdbDriver act/assert return typed results ───────────────────


@pytest.mark.integration
async def test_than_m2_s4_adb_act_assert_typed_results():
    """THAN-M2-S4: AdbDriver.act and assert_ return ActResult / AssertResult."""
    from thanatos.drivers import AdbDriver
    from thanatos.drivers.base import ActResult, AssertResult

    driver = AdbDriver()
    act_result = await driver.act('tap "Submit"')
    assert isinstance(act_result, ActResult)

    assert_result = await driver.assert_('element "Submit" is visible')
    assert isinstance(assert_result, AssertResult)


# ─── THAN-M2-S5: recall recursively searches subdirectories ──────────────────


@pytest.mark.integration
def test_than_m2_s5_recall_recursive_subdirs(tmp_path):
    """THAN-M2-S5: recall finds .md files nested under skill directory."""
    from thanatos.runner import recall

    skill_dir = tmp_path / ".thanatos"
    skill_dir.mkdir()
    skill_path = skill_dir / "skill.yaml"
    skill_path.write_text("name: test\ndriver: http\nentry: /\n", encoding="utf-8")

    nested = skill_dir / "kb"
    nested.mkdir()
    kb_file = nested / "rules.md"
    kb_file.write_text("# Rules\n\n## Checkout flow\nThe checkout button is blue.\n", encoding="utf-8")

    result = recall(str(skill_path), "checkout button blue")
    assert len(result) > 0
    assert result[0]["kind"] == "rules.md"
    assert "checkout" in result[0]["snippet"].lower()


# ─── THAN-M2-S6: recall tags filter by YAML frontmatter ──────────────────────


@pytest.mark.integration
def test_than_m2_s6_recall_tags_filter(tmp_path):
    """THAN-M2-S6: recall with tags only returns files whose frontmatter matches."""
    from thanatos.runner import recall

    skill_dir = tmp_path / ".thanatos"
    skill_dir.mkdir()
    skill_path = skill_dir / "skill.yaml"
    skill_path.write_text("name: test\ndriver: http\nentry: /\n", encoding="utf-8")

    auth_md = skill_dir / "auth.md"
    auth_md.write_text(
        "---\ntags: [auth, login]\n---\n\n# Auth\n\n## Login button\nThe login button is red.\n",
        encoding="utf-8",
    )

    cart_md = skill_dir / "cart.md"
    cart_md.write_text(
        "---\ntags: [cart, checkout]\n---\n\n# Cart\n\n## Add button\nClick the add button.\n",
        encoding="utf-8",
    )

    # Filter by auth tag — only auth.md should match
    result = recall(str(skill_path), "button", tags=["auth"])
    assert len(result) > 0
    assert all("token" in r["snippet"].lower() or "auth" in r["kind"].lower() for r in result)
    assert not any("cart" in r["kind"].lower() for r in result)

    # Filter by non-existent tag — empty result
    result_empty = recall(str(skill_path), "button", tags=["nonexistent"])
    assert result_empty == []


# ─── THAN-M2-S7: recall limit parameter caps results ─────────────────────────


@pytest.mark.integration
def test_than_m2_s7_recall_limit(tmp_path):
    """THAN-M2-S7: recall limit parameter restricts the number of returned hits."""
    from thanatos.runner import recall

    skill_dir = tmp_path / ".thanatos"
    skill_dir.mkdir()
    skill_path = skill_dir / "skill.yaml"
    skill_path.write_text("name: test\ndriver: http\nentry: /\n", encoding="utf-8")

    # Create multiple files with unique content
    for i in range(5):
        f = skill_dir / f"doc{i}.md"
        f.write_text(f"# Doc {i}\n\nUnique word sequence alpha beta gamma {i}.\n", encoding="utf-8")

    # Default limit (10) should return all 5 hits
    result_all = recall(str(skill_path), "alpha beta gamma")
    assert len(result_all) == 5

    # limit=2 should cap at 2
    result_limited = recall(str(skill_path), "alpha beta gamma", limit=2)
    assert len(result_limited) == 2


# ─── THAN-M2-S8: recall without frontmatter still works ──────────────────────


@pytest.mark.integration
def test_than_m2_s8_recall_no_frontmatter(tmp_path):
    """THAN-M2-S8: recall handles markdown files without YAML frontmatter."""
    from thanatos.runner import recall

    skill_dir = tmp_path / ".thanatos"
    skill_dir.mkdir()
    skill_path = skill_dir / "skill.yaml"
    skill_path.write_text("name: test\ndriver: http\nentry: /\n", encoding="utf-8")

    plain = skill_dir / "plain.md"
    plain.write_text("# Plain\n\nSome content about login forms.\n", encoding="utf-8")

    result = recall(str(skill_path), "login forms")
    assert len(result) > 0
    assert result[0]["kind"] == "plain.md"
