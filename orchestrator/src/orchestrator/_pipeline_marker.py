"""Pipeline-validation smoke marker (REQ-validate-fresh-pipeline-1777123726).

Self-dogfood scaffolding: this module exists solely to give every
"validate-fresh-pipeline" smoke REQ a tiny, deterministic, no-op delta. No
production module imports it; the orchestrator's runtime behavior is unchanged
whether this file is present or absent.

See `openspec/changes/REQ-validate-fresh-pipeline-1777123726/proposal.md` for
the rationale.
"""
from __future__ import annotations

PIPELINE_VALIDATION_REQ: str = "REQ-validate-fresh-pipeline-1777123726"
