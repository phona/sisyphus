# tasks: REQ-pr-label-sisyphus-auto-opened-1777217850

## Stage: contract / spec
- [x] author specs/sisyphus-auto-label/spec.md (delta + scenarios SAL-S1..S6)

## Stage: implementation — orchestrator
- [x] add module-level constant `SISYPHUS_TAG = "sisyphus"` + `_ensure_sisyphus_tag()` helper in `orchestrator/src/orchestrator/bkd_rest.py`
- [x] in `BKDRestClient.create_issue`, prepend `SISYPHUS_TAG` to `tags` if not already present
- [x] mirror auto-inject in `BKDMcpClient.create_issue` (`bkd_mcp.py`) for transport symmetry (imports `_ensure_sisyphus_tag` from bkd_rest)
- [x] in `actions/start_intake.py`, include `"sisyphus"` in the `update_issue(tags=...)` call

## Stage: implementation — prompts
- [x] update `_shared/tools_whitelist.md.j2`: curl POST sub-issue example tags array includes `"sisyphus"`
- [x] update `analyze.md.j2`: new section B.6 requiring (a) `gh label create sisyphus --force` then `gh pr create --label sisyphus` for every PR, (b) sub-issue fan-out tags includes `"sisyphus"`

## Stage: unit test
- [x] update `tests/test_bkd_rest.py::test_create_issue_payload_shape` to expect `"sisyphus"` prepended
- [x] add `tests/test_bkd_rest.py::test_create_issue_auto_injects_sisyphus_tag` (SAL-S1)
- [x] add `tests/test_bkd_rest.py::test_create_issue_does_not_duplicate_sisyphus_tag` (SAL-S2)
- [x] add `tests/test_bkd_rest.py::test_ensure_sisyphus_tag_helper_idempotent` (helper unit)
- [x] add `tests/test_bkd_rest.py::test_mcp_create_issue_auto_injects_sisyphus_tag` (SAL-S3)
- [x] update `tests/test_intake.py::test_start_intake` to assert `"sisyphus"` in tags (SAL-S4)
- [x] add `tests/test_prompts_sisyphus_label.py` covering rendered analyze prompt invariants (SAL-S5, SAL-S6)
- [x] full `make ci-unit-test` green: 931 passed (no regressions)
- [x] `make ci-integration-test` clean (no integration tests in scope; exit 5 → pass)
- [x] `make ci-lint` clean on changed files (ruff)
- [x] `openspec validate REQ-pr-label-sisyphus-auto-opened-1777217850` valid
- [x] `scripts/check-scenario-refs.sh .` OK (293 definitions found)

## Stage: PR
- [x] git push feat/REQ-pr-label-sisyphus-auto-opened-1777217850
- [x] gh label create sisyphus --color "6E5494" --description "Opened by sisyphus pipeline" --force (eat own dog food)
- [x] gh pr create --label sisyphus
