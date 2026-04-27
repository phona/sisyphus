"""
Contract tests for REQ-thanatos-m0-scaffold-v6-1777283112 — THAN-S1 through THAN-S7.

Black-box only: public API, CLI, and MCP protocol. No internal implementation details.
All tests are marked @pytest.mark.integration — run via `uv run pytest -m integration`.
"""

import pathlib
import subprocess
import sys
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
CHART_PATH = REPO_ROOT / "deploy" / "charts" / "thanatos"


# ─── THAN-S1: MCP server registers exactly three tools ───────────────────────


@pytest.mark.integration
async def test_than_s1_server_registers_three_tools():
    """THAN-S1: tools/list returns exactly run_scenario, run_all, recall with required params."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "thanatos.server"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            tools = {t.name: t for t in result.tools}

    assert set(tools.keys()) == {"run_scenario", "run_all", "recall"}, (
        f"Expected exactly 3 tools, got: {sorted(tools.keys())}"
    )

    # run_scenario required params per contract.spec.yaml
    rs_schema = tools["run_scenario"].inputSchema
    rs_required = set(rs_schema.get("required", []))
    assert rs_required >= {"skill_path", "spec_path", "scenario_id", "endpoint"}, (
        f"run_scenario missing required params, declared: {rs_required}"
    )

    # run_all required params
    ra_schema = tools["run_all"].inputSchema
    ra_required = set(ra_schema.get("required", []))
    assert ra_required >= {"skill_path", "spec_path", "endpoint"}, (
        f"run_all missing required params, declared: {ra_required}"
    )

    # recall required params
    rc_schema = tools["recall"].inputSchema
    rc_required = set(rc_schema.get("required", []))
    assert rc_required >= {"skill_path", "intent"}, (
        f"recall missing required params, declared: {rc_required}"
    )


# ─── THAN-S2: gherkin code block parses into structured fields ───────────────


@pytest.mark.integration
def test_than_s2_gherkin_block_parses():
    """THAN-S2: gherkin fence → scenario_id, given, when, then, source_format."""
    from thanatos.scenario import parse_spec_text

    doc = textwrap.dedent("""\
        #### Scenario: REQ-1004-S1 basic gherkin

        ```gherkin
        Given foo
        When bar
        Then baz
        ```
    """)
    results = parse_spec_text(doc)
    assert len(results) == 1
    sc = results[0]
    assert sc.scenario_id == "REQ-1004-S1"
    assert sc.given == ["foo"]
    assert sc.when == ["bar"]
    assert sc.then == ["baz"]
    assert sc.source_format == "gherkin"


# ─── THAN-S3: bullet-format with multiple GIVEN entries ──────────────────────


@pytest.mark.integration
def test_than_s3_bullet_multiple_given():
    """THAN-S3: bullet format → given list has length 2, source_format == 'bullet'."""
    from thanatos.scenario import parse_spec_text

    doc = textwrap.dedent("""\
        #### Scenario: THAN-multi multiple given bullets

        - **GIVEN** first condition
        - **GIVEN** second condition
        - **WHEN** something happens
        - **THEN** result expected
    """)
    results = parse_spec_text(doc)
    assert len(results) == 1
    sc = results[0]
    assert len(sc.given) == 2, f"Expected 2 given entries, got {sc.given}"
    assert sc.source_format == "bullet"


# ─── THAN-S4: mixed gherkin + bullet raises ScenarioFormatError ──────────────


@pytest.mark.integration
def test_than_s4_mixed_format_raises():
    """THAN-S4: gherkin fence + bullet step in one block → ScenarioFormatError."""
    from thanatos.scenario import ScenarioFormatError, parse_spec_text

    doc = textwrap.dedent("""\
        #### Scenario: MIX-1 mixed formats

        - **GIVEN** a bullet step

        ```gherkin
        Given also a gherkin step
        ```
    """)
    with pytest.raises(ScenarioFormatError) as exc_info:
        parse_spec_text(doc)
    assert "mixes gherkin" in str(exc_info.value).lower(), (
        f"Expected error containing 'mixes gherkin', got: {exc_info.value}"
    )


# ─── THAN-S5: every M0 driver method raises NotImplementedError ──────────────


@pytest.mark.integration
@pytest.mark.parametrize("driver_name", ["PlaywrightDriver", "AdbDriver", "HttpDriver"])
@pytest.mark.parametrize(
    "method_name", ["preflight", "observe", "act", "assert_", "capture_evidence"]
)
async def test_than_s5_driver_raises_not_implemented(driver_name, method_name):
    """THAN-S5: every M0 driver method raises NotImplementedError('M0: scaffold only')."""
    import importlib

    drivers_mod = importlib.import_module("thanatos.drivers")
    DriverClass = getattr(drivers_mod, driver_name)
    instance = DriverClass()
    method = getattr(instance, method_name)

    if method_name == "preflight":
        coro = method("http://localhost")
    elif method_name in ("act", "assert_"):
        coro = method("some step")
    else:
        coro = method()

    with pytest.raises(NotImplementedError) as exc_info:
        await coro
    assert str(exc_info.value) == "M0: scaffold only", (
        f"{driver_name}.{method_name}: got '{exc_info.value}'"
    )


# ─── THAN-S6: driver=adb → two-container Pod (redroid + thanatos) ─────────────


@pytest.mark.integration
def test_than_s6_helm_adb_two_containers():
    """THAN-S6: helm template --set driver=adb → Deployment with redroid + thanatos."""
    import yaml

    if not CHART_PATH.exists():
        pytest.skip(f"helm chart not found at {CHART_PATH}")

    result = subprocess.run(
        [
            "helm",
            "template",
            "thanatos",
            str(CHART_PATH),
            "--set",
            "driver=adb",
            "--set",
            "redroid.image=redroid/redroid:13.0.0-amd64",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"helm template failed:\n{result.stderr}"

    deployments = [
        m for m in yaml.safe_load_all(result.stdout) if m and m.get("kind") == "Deployment"
    ]
    assert deployments, "No Deployment found in rendered chart"

    containers = deployments[0]["spec"]["template"]["spec"]["containers"]
    names = [c["name"] for c in containers]
    assert len(containers) == 2, f"Expected 2 containers, got {names}"
    assert "redroid" in names, f"'redroid' not in containers: {names}"
    assert "thanatos" in names, f"'thanatos' not in containers: {names}"

    redroid = next(c for c in containers if c["name"] == "redroid")
    assert redroid.get("securityContext", {}).get("privileged") is True, (
        "redroid container must have securityContext.privileged: true"
    )

    thanatos = next(c for c in containers if c["name"] == "thanatos")
    env_map = {e["name"]: e.get("value", "") for e in thanatos.get("env", [])}
    assert "ADB_SERVER_ADDR" in env_map, (
        f"ADB_SERVER_ADDR not in thanatos env: {sorted(env_map)}"
    )


# ─── THAN-S7: driver=playwright → single thanatos container ──────────────────


@pytest.mark.integration
def test_than_s7_helm_playwright_single_container():
    """THAN-S7: helm template --set driver=playwright → exactly one container named thanatos."""
    import yaml

    if not CHART_PATH.exists():
        pytest.skip(f"helm chart not found at {CHART_PATH}")

    result = subprocess.run(
        ["helm", "template", "thanatos", str(CHART_PATH), "--set", "driver=playwright"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"helm template failed:\n{result.stderr}"

    deployments = [
        m for m in yaml.safe_load_all(result.stdout) if m and m.get("kind") == "Deployment"
    ]
    assert deployments, "No Deployment found in rendered chart"

    containers = deployments[0]["spec"]["template"]["spec"]["containers"]
    assert len(containers) == 1, (
        f"Expected 1 container, got {[c['name'] for c in containers]}"
    )
    assert containers[0]["name"] == "thanatos", (
        f"Expected container named 'thanatos', got '{containers[0]['name']}'"
    )
    assert containers[0].get("command") == ["python", "-m", "thanatos.server"], (
        f"thanatos command mismatch: {containers[0].get('command')}"
    )
