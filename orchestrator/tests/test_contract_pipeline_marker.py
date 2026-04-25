"""Contract tests for REQ-validate-fresh-pipeline-1777123726.

Black/white-box behavioral contracts derived from:
  openspec/changes/REQ-validate-fresh-pipeline-1777123726/specs/pipeline-marker/spec.md

Scenarios covered:
  PVR-S1   module imports cleanly with zero observable side effects
  PVR-S2   PIPELINE_VALIDATION_REQ is a str matching ^REQ-validate-fresh-pipeline-\\d+$
  PVR-S3   importlib.reload returns the same constant value
  PVR-S4   constant is not re-exported from the orchestrator package
"""
from __future__ import annotations

import importlib
import re

PVR_PATTERN = re.compile(r"^REQ-validate-fresh-pipeline-\d+$")


def test_pvr_s1_module_imports_cleanly() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    assert mod is not None
    assert mod.__name__ == "orchestrator._pipeline_marker"


def test_pvr_s2_constant_is_str_and_matches_req_pattern() -> None:
    from orchestrator import _pipeline_marker

    value = _pipeline_marker.PIPELINE_VALIDATION_REQ
    assert isinstance(value, str)
    assert PVR_PATTERN.match(value), (
        f"PIPELINE_VALIDATION_REQ={value!r} does not match {PVR_PATTERN.pattern}"
    )


def test_pvr_s3_module_has_no_side_effects_on_reimport() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    first = mod.PIPELINE_VALIDATION_REQ
    reloaded = importlib.reload(mod)
    second = reloaded.PIPELINE_VALIDATION_REQ
    assert first == second


def test_pvr_s4_constant_is_not_re_exported_from_package() -> None:
    import orchestrator

    assert "PIPELINE_VALIDATION_REQ" not in dir(orchestrator)
    assert getattr(orchestrator, "PIPELINE_VALIDATION_REQ", None) is None
