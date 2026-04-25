"""Challenger contract tests for REQ-validate-fresh-3-1777132879.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-validate-fresh-3-1777132879/specs/pipeline-marker-v3/spec.md

Scenarios covered:
  PVR3-S1  module imports cleanly; PIPELINE_VALIDATION_REQ_V3 exposed; zero side effects
  PVR3-S2  PIPELINE_VALIDATION_REQ_V3 is str matching ^REQ-validate-fresh-3-\\d+$
  PVR3-S3  importlib.reload returns the same constant value (no randomness)
  PVR3-S4  PIPELINE_VALIDATION_REQ_V3 NOT re-exported from orchestrator package namespace
  PVR3-S1+ PIPELINE_VALIDATION_REQ (v1) still present and unmodified (coexistence contract)
"""
from __future__ import annotations

import importlib
import re

PVR3_PATTERN = re.compile(r"^REQ-validate-fresh-3-\d+$")


def test_pvr3_s1_module_imports_cleanly_and_exposes_v3_constant() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    assert mod is not None
    assert mod.__name__ == "orchestrator._pipeline_marker"
    assert hasattr(mod, "PIPELINE_VALIDATION_REQ_V3"), (
        "module orchestrator._pipeline_marker must expose PIPELINE_VALIDATION_REQ_V3"
    )


def test_pvr3_s2_v3_constant_is_str_and_matches_req_pattern() -> None:
    from orchestrator import _pipeline_marker

    value = _pipeline_marker.PIPELINE_VALIDATION_REQ_V3
    assert isinstance(value, str), (
        f"PIPELINE_VALIDATION_REQ_V3 must be str, got {type(value).__name__!r}"
    )
    assert PVR3_PATTERN.match(value), (
        f"PIPELINE_VALIDATION_REQ_V3={value!r} does not match pattern {PVR3_PATTERN.pattern!r}"
    )


def test_pvr3_s3_reload_returns_same_constant_value() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    first = mod.PIPELINE_VALIDATION_REQ_V3
    reloaded = importlib.reload(mod)
    second = reloaded.PIPELINE_VALIDATION_REQ_V3
    assert first == second, (
        f"PIPELINE_VALIDATION_REQ_V3 changed across reload: {first!r} != {second!r}"
    )


def test_pvr3_s4_v3_constant_not_in_orchestrator_package_namespace() -> None:
    import orchestrator

    assert "PIPELINE_VALIDATION_REQ_V3" not in dir(orchestrator), (
        "PIPELINE_VALIDATION_REQ_V3 must not appear in dir(orchestrator)"
    )
    assert getattr(orchestrator, "PIPELINE_VALIDATION_REQ_V3", None) is None, (
        "PIPELINE_VALIDATION_REQ_V3 must not be accessible via orchestrator package object"
    )


def test_pvr3_s1_plus_original_constant_coexists_unmodified() -> None:
    from orchestrator import _pipeline_marker

    assert hasattr(_pipeline_marker, "PIPELINE_VALIDATION_REQ"), (
        "original PIPELINE_VALIDATION_REQ must still be present alongside V3 (coexistence)"
    )
    assert isinstance(_pipeline_marker.PIPELINE_VALIDATION_REQ, str), (
        "original PIPELINE_VALIDATION_REQ must remain a str"
    )
