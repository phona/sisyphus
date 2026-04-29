# REQ-fix-intent-issue-hijacking-1777427339: Fix intent issue being hijacked as analyze issue

## Problem

`start_analyze` directly transforms the user's original BKD intent issue (the issue they created to express their requirement) into an analyze-agent working issue: renaming it to `[ANALYZE]`, changing tags, and setting status=working. The user's original issue effectively "disappears".

### Specific manifestations

1. User creates a BKD issue expressing their requirement
2. sisyphus receives webhook, initializes req_state
3. `start_analyze` updates the issue: `update_issue(title="[REQ-xxx] [ANALYZE]...", tags=["analyze", ...])`
4. analyze-agent works on this same issue
5. After analyze completes, `_push_upstream_status` pushes the issue to done
6. User wants to review their original requirement, but can't find it in todo/working/review columns — they have to dig through the done column

## Solution

1. **Keep intent issue intact**: Don't change title, don't change original tags (except add sisyphus-related tags), don't change status
2. **Create new analyze sub-agent issue**: Like challenger, verifier, and fixer, analyze should also create an independent BKD issue, not hijack the user's original issue
3. **Backward compatibility**: Already-running REQs are unaffected; existing `analyze` tags on intent issues are preserved
4. **Intent issue for subsequent stages**: In PENDING_USER_REVIEW stage, users still interact on the intent issue (changing statusId), so the intent issue should remain visible

## Scope

- Modified: `orchestrator/src/orchestrator/actions/start_analyze.py` — core: create analyze sub-agent issue instead of modifying intent issue
- Modified: `orchestrator/src/orchestrator/prompts/analyze.md.j2` — fix pre-existing UndefinedError when `intake_summary` is absent (direct analyze path)
- Modified: `orchestrator/tests/test_actions_start_analyze.py` — update tests for new behavior
- Modified: `orchestrator/tests/test_actions_smoke.py` — update smoke tests
- Modified: `orchestrator/tests/test_actions_start_analyze_supersede.py` — add missing mocks
- Modified: `orchestrator/tests/test_contract_multi_repo_e2e.py` — add missing mocks
