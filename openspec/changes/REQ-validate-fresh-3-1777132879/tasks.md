# Tasks: REQ-validate-fresh-3-1777132879

## Stage: contract / spec

- [x] author specs/pipeline-marker-v3/spec.md — delta format, all scenarios with SHALL/MUST in body and `#### Scenario:` headings

## Stage: implementation

- [x] add `PIPELINE_VALIDATION_REQ_V3` constant to `orchestrator/src/orchestrator/_pipeline_marker.py`
- [x] author unit tests in `orchestrator/tests/test_contract_pipeline_marker_v3.py` (PVR3-S1..PVR3-S4)

## Stage: PR

- [x] git push feat/REQ-validate-fresh-3-1777132879
- [x] gh pr create
