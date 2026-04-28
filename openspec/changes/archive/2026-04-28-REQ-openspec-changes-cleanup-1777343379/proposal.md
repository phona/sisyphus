# Proposal: openspec/changes Orphan Cleanup

## Problem

`openspec/changes/` accumulates orphan REQ-* directories when:
1. A REQ is escalated but `escalate.py` never cleaned the `changes/` dir from the PR branch
2. A REQ is redispatched as vN, leaving the old `changes/REQ-XXX/` as a permanent orphan
3. A PR is merged manually without running `done_archive` (which performs `openspec apply` + rm)

Root cause analysis identified 60 orphan directories on main as of 2026-04-28.

## Solution

Three complementary fixes (all in a single PR):

**A. escalate cleanup (forward-looking):** When a REQ is escalated, automatically run
`rm -rf openspec/changes/REQ-XXX/ && git add openspec/ && git commit` in each cloned repo
inside the runner pod. Fail-open: cleanup failure never blocks escalate transition.

**B. Historical cleanup script:** `orchestrator/scripts/cleanup_orphan_openspec_changes.py`
queries PG for each `openspec/changes/REQ-*/` dir and classifies: done/escalated/not-found → delete,
in-flight → keep. Dry-run by default; `--apply` commits deletions (no push).

**D. Dispatch dedup (resume path):** In `start_analyze`, before BKD dispatch, scan each cloned
repo for `openspec/changes/` dirs with the same base slug (stripping `-vN` suffix) and move them
to `openspec/changes/_superseded/<old-dir>/` with a commit. Prevents vN redispatch from
creating a second orphan.

## Not in scope

- GHA "PR merge → openspec apply" hook (separate REQ)
- Changing `done_archive` main path (already works correctly)
- Bulk historical PR history changes
