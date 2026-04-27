# REQ-bkd-analyze-hang-debug-1777247423: 60-min watchdog detection floor causes multi-hour analyze hangs

## Why

`watchdog_stuck_threshold_sec = 3600` (`config.py:172`) creates a 60-minute
floor for **any** stuck-stage detection — including the case where the BKD
session has already ended but no `session.completed` / `session.failed`
webhook arrived. Sisyphus prod data shows this floor is the dominant cause
of the user-visible "BKD analyze hangs for 60 minutes" symptom in the REQ
title.

### Evidence (sisyphus prod, `artifact_checks` table, 2026-04-25/26)

```
SELECT req_id, COUNT(*) AS hits,
       EXTRACT(EPOCH FROM (MAX(checked_at) - MIN(checked_at)))::INT AS span_sec
  FROM artifact_checks
 WHERE stage = 'watchdog:analyzing'
GROUP BY req_id HAVING COUNT(*) >= 2 ORDER BY hits DESC, last_hit DESC;
```

Excerpt:

| req_id                                      | hits | span_sec |
|---------------------------------------------|-----:|---------:|
| REQ-init-orphan-cleanup-1777189271          |    3 |     7254 |
| REQ-accept-env-gc-skeleton-1777148964       |    3 |     7249 |
| REQ-fixer-prompt-ruff-fix-all-1777148964    |    3 |     7249 |
| REQ-runner-uv-onefile-1777146833            |    3 |     3602 |
| REQ-runner-kubectl-onefile-1777145823       |    3 |     3602 |
| ... 13 more REQs at 3601-3603s ...          |    3 |    ~3602 |

Every single watchdog escalation row's `stderr_tail` is
`"stuck for 360X seconds in state analyzing"` — the watchdog tick
**immediately following** the 3600-second SQL filter window.

13 REQs hit watchdog 3 times each, exactly 3602s apart per consecutive
hit. That's the auto-resume retry loop at work:

1. Watchdog detects stuck @ 3600s → emits `SESSION_FAILED`
2. `escalate.py` sees `body.event == "watchdog.stuck"` →
   `_is_transient` returns `True` → BKD `follow_up_issue` "continue,
   you were interrupted" (auto_retry_count = 1)
3. Same broken environment → next death goes undetected for another
   3600s → watchdog hits again (auto_retry_count = 2)
4. 3rd watchdog hit → `session-failed-after-2-retries` → real escalate

Three back-to-back 60-min waits = up to **3 hours** of user-perceived hang
on a session that died after the first attempt. REQ-init-orphan-cleanup
and the other 3-hit rows confirm this with span ≈ 7250s
(2 × 3600 + 60s grace).

### Why the floor exists

`config.py:172` says: *"sonnet analyze long tail 经常 25-35min；30 min
阈值会 false-escalate 大量 dogfood REQ；60 min 仍能兜真死"*. So 60 min
was set to avoid false-positive escalates on slow-but-alive analyze
sessions. The trade-off works for that one class of sessions, but it
ignores a structural property of the watchdog: **the code already
discriminates running from ended sessions** (`watchdog.py:150–165`,
`if issue.session_status == "running": skip`). The 60-min SQL prefilter
is therefore unnecessarily strict for the dead-session case — a dead
session's webhook has already failed; waiting longer can't change that.

### What slipped through previously

This investigation is dogfooding the M8 watchdog (PR #75). REQ-watchdog-threshold-bump-1777147857
on 2026-04-25 raised the floor from 30 min → 60 min to suppress the
false-escalate noise; nobody quantified the dead-session-detection cost
at the same time.

## What Changes

Split the watchdog SQL prefilter into a fast lane (default **5 min**) for
candidate inspection, and keep `watchdog_stuck_threshold_sec = 3600` only
as the **slow lane safety**:

- **Fast lane** (`watchdog_session_ended_threshold_sec`, new, default 300s):
  rows older than 5 min are pulled into watchdog tick; for each, BKD is
  queried. If `session_status != "running"` (= ended without webhook), the
  watchdog escalates immediately. This is the dominant prod signal —
  expected hang shrinks from 60 min to ≤5 min.

- **Slow lane** (`watchdog_stuck_threshold_sec`, unchanged 3600s): a row
  whose BKD says `session_status == "running"` is still skipped on every
  tick. Long-tail real analyze (25–35 min) is unaffected. This setting now
  has no operational role except as the upper-bound symbolic threshold
  documented in code; we leave it for future "running-but-truly-stuck"
  detection work.

Defensive `if running: skip` and the `_is_intake_no_result_tag` branch
remain. The `_TRANSIENT_REASONS` and `_HARD_REASONS` machinery in
`escalate.py` is unchanged — auto-resume on transient `watchdog.stuck`
remains the same; the change is *when* the watchdog fires, not *how*
escalate reacts.

### Out of scope

- Reducing `_MAX_AUTO_RETRY` (currently 2). Three attempts of 5 min each
  ≈ 15 min total bound on the dead-session pathology — still 12× faster
  than today's worst case. Tuning retry count is a separate REQ.
- Killing the auto-resume mechanism. It IS the right action when the
  underlying cause is a transient BKD spawn flake; this REQ doesn't
  argue against it.
- Adding a slow-lane "BKD reports running for >60 min, escalate anyway"
  override. Today's behaviour trusts BKD's `running` indefinitely; we
  preserve that. If BKD lies about session status, that's a different
  fix.
- Changing intake-no-result-tag detection. Already path-independent of
  the prefilter window — only its activation latency improves as a
  side effect of the smaller fast lane.

## Impact

- **Affected systems**: orchestrator (`watchdog.py`, `config.py`),
  helm values (operators may want to tune `watchdog_session_ended_threshold_sec`).
- **User-visible**: hangs caused by lost BKD webhooks shrink from
  ~60 min to ~5 min per retry round (15 min worst case across all 3
  retries vs. ~3 hours today).
- **Rollback**: set `SISYPHUS_WATCHDOG_SESSION_ENDED_THRESHOLD_SEC=3600`
  to restore current behavior. No DB schema change. No state-machine
  change. No prompt change.
- **Cost**: extra BKD `GET /api/projects/{p}/issues/{i}` calls per tick
  (one per stuck-but-running row). For the typical 5–10 in-flight REQs,
  this is ≤10 GETs / 60 s = trivial against localhost BKD.
