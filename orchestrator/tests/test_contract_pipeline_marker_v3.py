"""Contract tests for REQ-validate-fresh-3-1777132879.

Black/white-box behavioral contracts derived from:
  openspec/changes/REQ-validate-fresh-3-1777132879/specs/pipeline-marker-v3/spec.md

Scenarios covered:
  PVR3-S1   module imports cleanly with zero observable side effects
  PVR3-S2   PIPELINE_VALIDATION_REQ_V3 is a str matching ^REQ-validate-fresh-3-\\d+$
  PVR3-S3   importlib.reload returns the same constant value
  PVR3-S4   v3 constant is not re-exported from the orchestrator package
"""
from __future__ import annotations

import importlib
import re

PVR3_PATTERN = re.compile(r"^REQ-validate-fresh-3-\d+$")


def test_pvr3_s1_module_imports_cleanly() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    assert mod is not None
    assert hasattr(mod, "PIPELINE_VALIDATION_REQ_V3")


def test_pvr3_s2_constant_is_str_and_matches_req_pattern() -> None:
    from orchestrator import _pipeline_marker

    value = _pipeline_marker.PIPELINE_VALIDATION_REQ_V3
    assert isinstance(value, str)
    assert PVR3_PATTERN.match(value), (
        f"PIPELINE_VALIDATION_REQ_V3={value!r} does not match {PVR3_PATTERN.pattern}"
    )


def test_pvr3_s3_module_has_no_side_effects_on_reimport() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    first = mod.PIPELINE_VALIDATION_REQ_V3
    reloaded = importlib.reload(mod)
    second = reloaded.PIPELINE_VALIDATION_REQ_V3
    assert first == second


def test_pvr3_s4_constant_is_not_re_exported_from_package() -> None:
    import orchestrator

    assert "PIPELINE_VALIDATION_REQ_V3" not in dir(orchestrator)
    assert getattr(orchestrator, "PIPELINE_VALIDATION_REQ_V3", None) is None
