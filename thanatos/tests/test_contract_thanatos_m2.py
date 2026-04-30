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
