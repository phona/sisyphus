"""Contract tests for REQ-np-demo4-1779013770.

Black/white-box behavioral contracts derived from:
  openspec/changes/REQ-np-demo4-1779013770/specs/np-demo4/spec.md

Scenarios covered:
  NPD4-S1   module exposes NP_DEMO4_REQ without side effects
  NPD4-S2   NP_DEMO4_REQ is a str matching ^REQ-np-demo4-\\d+$
  NPD4-S3   all four pipeline-marker constants coexist with expected literal values
"""
from __future__ import annotations

import importlib
import re

NPD4_PATTERN = re.compile(r"^REQ-np-demo4-\d+$")


def test_npd4_s1_module_exposes_np_demo4_req() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    assert mod is not None
    assert hasattr(mod, "NP_DEMO4_REQ")


def test_npd4_s2_constant_is_str_and_matches_pattern() -> None:
    from orchestrator import _pipeline_marker

    value = _pipeline_marker.NP_DEMO4_REQ
    assert isinstance(value, str)
    assert NPD4_PATTERN.match(value), (
        f"NP_DEMO4_REQ={value!r} does not match {NPD4_PATTERN.pattern}"
    )


def test_npd4_s3_all_four_constants_coexist() -> None:
    from orchestrator import _pipeline_marker

    assert _pipeline_marker.PIPELINE_VALIDATION_REQ == "REQ-validate-fresh-pipeline-1777123726"
    assert _pipeline_marker.PIPELINE_VALIDATION_REQ_V3 == "REQ-validate-fresh-3-1777132879"
    assert _pipeline_marker.SMOKE_PIPELINE_V3_REQ == "REQ-smoke-pipeline-v3-1777806694"
    assert _pipeline_marker.NP_DEMO4_REQ == "REQ-np-demo4-1779013770"
