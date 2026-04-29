# Archive Automation Spec

## Scenarios

### ARCH-AUTO-S1: ARCHIVING state removed

Given: state.py defines ReqState enum
When: ARCHIVING is removed
Then: no transition references ARCHIVING
And: no code references ARCHIVING state

### ARCH-AUTO-S2: PR_MERGED goes directly to DONE

Given: REQ in PENDING_USER_REVIEW state
When: PR_MERGED event received
Then: state transitions to DONE (not ARCHIVING)
And: no done_archive action is triggered

### ARCH-AUTO-S3: archive runs as background task on DONE

Given: transition to DONE
When: engine.step transitions to DONE
Then: _auto_archive is triggered as fire-and-forget task
And: archive failure does not block state machine

### ARCH-AUTO-S4: openspec archive in runner pod

Given: _auto_archive triggered
When: runner pod executes openspec archive script
Then: `openspec archive REQ --yes` runs for each repo
And: `git add openspec/ && git commit` runs
And: no push to main

### ARCH-AUTO-S5: archive failure logged but not blocking

Given: _auto_archive fails
When: runner script exits non-zero
Then: log.warning("archive.failed") is emitted
And: REQ state remains DONE
