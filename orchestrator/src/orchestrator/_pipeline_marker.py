"""Pipeline-validation smoke markers.

Self-dogfood scaffolding: this module exists solely to give every
"validate-fresh" smoke REQ a tiny, deterministic, no-op delta. No
production module imports it; the orchestrator's runtime behavior is unchanged
whether this file is present or absent.

See `openspec/changes/REQ-validate-fresh-pipeline-1777123726/proposal.md` for
the original rationale.
"""
from __future__ import annotations

PIPELINE_VALIDATION_REQ: str = "REQ-validate-fresh-pipeline-1777123726"
PIPELINE_VALIDATION_REQ_V3: str = "REQ-validate-fresh-3-1777132879"
