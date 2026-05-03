"""Contract tests for REQ-smoke-pipeline-v3-1777806694.

Black/white-box behavioral contracts derived from:
  openspec/changes/REQ-smoke-pipeline-v3-1777806694/specs/smoke-pipeline-v3/spec.md

Scenarios covered:
  SPV3-S1   module imports cleanly with zero observable side effects
  SPV3-S2   SMOKE_PIPELINE_V3_REQ is a str matching ^REQ-smoke-pipeline-v3-\\d+$
  SPV3-S3   importlib.reload returns the same constant value
  SPV3-S4   SMOKE_PIPELINE_V3_REQ is not re-exported from the orchestrator package
"""
from __future__ import annotations

import importlib
import re

SPV3_PATTERN = re.compile(r"^REQ-smoke-pipeline-v3-\d+$")


def test_spv3_s1_module_imports_cleanly() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    assert mod is not None
    assert hasattr(mod, "SMOKE_PIPELINE_V3_REQ")


def test_spv3_s2_constant_is_str_and_matches_req_pattern() -> None:
    from orchestrator import _pipeline_marker

    value = _pipeline_marker.SMOKE_PIPELINE_V3_REQ
    assert isinstance(value, str)
    assert SPV3_PATTERN.match(value), (
        f"SMOKE_PIPELINE_V3_REQ={value!r} does not match {SPV3_PATTERN.pattern}"
    )


def test_spv3_s3_module_has_no_side_effects_on_reimport() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    first = mod.SMOKE_PIPELINE_V3_REQ
    reloaded = importlib.reload(mod)
    second = reloaded.SMOKE_PIPELINE_V3_REQ
    assert first == second


def test_spv3_s4_constant_is_not_re_exported_from_package() -> None:
    import orchestrator

    assert "SMOKE_PIPELINE_V3_REQ" not in dir(orchestrator)
    assert getattr(orchestrator, "SMOKE_PIPELINE_V3_REQ", None) is None
