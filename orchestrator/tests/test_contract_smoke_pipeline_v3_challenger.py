"""Challenger contract tests for REQ-smoke-pipeline-v3-1777806694.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-smoke-pipeline-v3-1777806694/specs/smoke-pipeline-v3/spec.md

Scenarios covered:
  SPV3-S1   module imports cleanly; SMOKE_PIPELINE_V3_REQ exposed; zero side effects
  SPV3-S2   SMOKE_PIPELINE_V3_REQ is str matching ^REQ-smoke-pipeline-v3-\\d+$
  SPV3-S3   importlib.reload returns the same constant value (no randomness)
  SPV3-S4   SMOKE_PIPELINE_V3_REQ NOT re-exported from orchestrator package namespace
  SPV3-S1+  PIPELINE_VALIDATION_REQ (v1) and PIPELINE_VALIDATION_REQ_V3 still
            present and unmodified (coexistence contract from spec body)
  SPV3-S1++ production code path (engine / router / actions / checkers / store)
            does NOT import SMOKE_PIPELINE_V3_REQ (spec body invariant)
"""
from __future__ import annotations

import importlib
import io
import logging
import pathlib
import re
import sys

SPV3_PATTERN = re.compile(r"^REQ-smoke-pipeline-v3-\d+$")


def test_spv3_s1_module_imports_cleanly_and_exposes_constant() -> None:
    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        sys.modules.pop("orchestrator._pipeline_marker", None)
        mod = importlib.import_module("orchestrator._pipeline_marker")
    finally:
        root.removeHandler(handler)

    assert mod is not None
    assert mod.__name__ == "orchestrator._pipeline_marker"
    assert hasattr(mod, "SMOKE_PIPELINE_V3_REQ"), (
        "module orchestrator._pipeline_marker must expose SMOKE_PIPELINE_V3_REQ"
    )
    assert log_buf.getvalue() == "", (
        f"importing _pipeline_marker must not emit log lines, got: {log_buf.getvalue()!r}"
    )


def test_spv3_s2_constant_is_str_and_matches_req_pattern() -> None:
    from orchestrator import _pipeline_marker

    value = _pipeline_marker.SMOKE_PIPELINE_V3_REQ
    assert isinstance(value, str), (
        f"SMOKE_PIPELINE_V3_REQ must be str, got {type(value).__name__!r}"
    )
    assert SPV3_PATTERN.match(value), (
        f"SMOKE_PIPELINE_V3_REQ={value!r} does not match pattern {SPV3_PATTERN.pattern!r}"
    )


def test_spv3_s3_reload_returns_same_constant_value() -> None:
    mod = importlib.import_module("orchestrator._pipeline_marker")
    first = mod.SMOKE_PIPELINE_V3_REQ
    reloaded = importlib.reload(mod)
    second = reloaded.SMOKE_PIPELINE_V3_REQ
    assert first == second, (
        f"SMOKE_PIPELINE_V3_REQ changed across reload: {first!r} != {second!r}"
    )


def test_spv3_s4_constant_not_in_orchestrator_package_namespace() -> None:
    import orchestrator

    assert "SMOKE_PIPELINE_V3_REQ" not in dir(orchestrator), (
        "SMOKE_PIPELINE_V3_REQ must not appear in dir(orchestrator)"
    )
    assert getattr(orchestrator, "SMOKE_PIPELINE_V3_REQ", None) is None, (
        "SMOKE_PIPELINE_V3_REQ must not be accessible via orchestrator package object"
    )


def test_spv3_s1_plus_prior_constants_coexist_unmodified() -> None:
    from orchestrator import _pipeline_marker

    assert hasattr(_pipeline_marker, "PIPELINE_VALIDATION_REQ"), (
        "original PIPELINE_VALIDATION_REQ must still be present alongside SMOKE_PIPELINE_V3_REQ"
    )
    assert isinstance(_pipeline_marker.PIPELINE_VALIDATION_REQ, str), (
        "original PIPELINE_VALIDATION_REQ must remain a str"
    )

    assert hasattr(_pipeline_marker, "PIPELINE_VALIDATION_REQ_V3"), (
        "PIPELINE_VALIDATION_REQ_V3 must still be present alongside SMOKE_PIPELINE_V3_REQ"
    )
    assert isinstance(_pipeline_marker.PIPELINE_VALIDATION_REQ_V3, str), (
        "PIPELINE_VALIDATION_REQ_V3 must remain a str"
    )


def test_spv3_s1_plus_plus_constant_not_imported_by_production_code() -> None:
    """Spec body invariant: SMOKE_PIPELINE_V3_REQ MUST NOT be imported anywhere
    in the production code path (engine / router / actions / checkers / store).

    Static grep over the orchestrator source tree, excluding tests/ and the
    marker module itself, asserts the constant name appears nowhere else.
    """
    src_root = pathlib.Path(__file__).resolve().parents[1] / "src" / "orchestrator"
    assert src_root.is_dir(), f"expected orchestrator src root at {src_root}"

    offenders: list[str] = []
    for py_path in src_root.rglob("*.py"):
        rel = py_path.relative_to(src_root)
        if rel.parts and rel.parts[0] == "tests":
            continue
        if py_path.name == "_pipeline_marker.py":
            continue
        try:
            text = py_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "SMOKE_PIPELINE_V3_REQ" in text:
            offenders.append(str(rel))

    assert not offenders, (
        "SMOKE_PIPELINE_V3_REQ must not be referenced from production code, "
        f"found references in: {offenders!r}"
    )
