# Design — Watchdog fast detection for ended-session hangs

## Investigation summary

Filed against the symptom "BKD analyze 60min hang" (REQ title). Root
cause is **not** an analyze-stage agent bug; it is a structural property
of the watchdog SQL prefilter:

```sql
-- orchestrator/src/orchestrator/watchdog.py:_tick (current)
SELECT req_id, project_id, state, context,
       EXTRACT(EPOCH FROM (NOW() - updated_at))::BIGINT AS stuck_sec
  FROM req_state
 WHERE state <> ALL($1::text[])
   AND updated_at < NOW() - INTERVAL '1 second' * $2  -- $2 = 3600 today
```

The Python loop after this SQL already separates running from ended
sessions:

```python
# orchestrator/src/orchestrator/watchdog.py:148-165
async with BKDClient(...) as bkd:
    issue = await bkd.get_issue(project_id, issue_id)
if issue.session_status == "running":
    still_running = True
    log.debug("watchdog.still_running", ...)
...
if still_running:
    return False
```

Therefore: lowering `$2` does **not** false-escalate genuinely-running
analyze sessions — they continue to be skipped by the in-loop check.
The current 3600s value is a conservative SQL prefilter, not a
correctness guard.

## Decision: introduce a separate fast threshold

```python
# config.py — new
watchdog_session_ended_threshold_sec: int = 300  # 5 min
# config.py — unchanged
watchdog_stuck_threshold_sec: int = 3600
```

`_tick` uses the **smaller** of the two as the SQL prefilter. The
existing skip-if-running logic preserves the 3600-second protection for
running sessions (they get checked every 60s tick once 5 min stale, but
the in-code skip drops them).

This shape was chosen over alternatives because:

| Option                                              | Rejected because                                                        |
|-----------------------------------------------------|-------------------------------------------------------------------------|
| Lower `watchdog_stuck_threshold_sec` to 300s        | Conflates two concepts; future "running stuck" detection has no knob.   |
| Drop SQL prefilter entirely                         | Whole `req_state` table scanned every 60s — wasteful at scale.          |
| Move ended-session detection to BKD webhook handler | The hang IS that the webhook never arrived. Adding webhook dedup unhelpful. |
| Reduce `_MAX_AUTO_RETRY` from 2 to 0                | Hides flaky BKD restarts that legitimately succeed on retry.            |

## Behavioral matrix (after fix)

| stuck_sec | session_status | Outcome                | Notes                                          |
|----------:|---------------:|------------------------|------------------------------------------------|
| < 300     | any            | Skip (SQL filters out) | Below fast threshold, nothing to detect yet.   |
| 300+      | running        | Skip (in-loop)         | BKD says alive — trust it indefinitely.        |
| 300+      | failed/completed/cancelled/None | **Escalate**  | Lost-webhook fast path. New behavior.          |
| 300+      | (BKD unreachable)               | Escalate     | Conservative — already current behaviour.      |
| 3600+     | running        | Skip (in-loop)         | Unchanged. Setting now mostly documentation.   |

Per-row BKD `GET` call count rises proportionally to the size of the
fast-window backlog. For prod (≤10 in-flight REQs), that is ≤10 calls
per 60s tick against `localhost:3000` — well under any meaningful BKD
budget.

## Why fast threshold = 300s and not lower

- 60s would match the watchdog tick interval, but BKD itself may
  legitimately take a few seconds to flush a `session.completed`
  webhook after the agent returns. A grace window prevents the
  watchdog from racing webhook-arrival on fast-finishing agents.
- 300s is large enough that a `session.completed` webhook either
  arrived already or won't arrive at all (BKD's internal retry policy
  expires within seconds, not minutes).
- The constant is exposed via `SISYPHUS_WATCHDOG_SESSION_ENDED_THRESHOLD_SEC`
  for operators who want to tune. The default lives in `config.py`.

## Compatibility / rollback

- No DB schema change.
- No state-machine event change. `Event.SESSION_FAILED` still fires
  the same `escalate` path; only the wall-clock between `updated_at`
  and the firing changes.
- Existing tests that monkey-patch `settings.watchdog_stuck_threshold_sec`
  continue to work (that knob is still consumed for the slow lane).
- Helm `values.yaml` need no change to enable the fix — the new
  setting takes its 300s default. Operators wanting the legacy 3600s
  behaviour set `SISYPHUS_WATCHDOG_SESSION_ENDED_THRESHOLD_SEC=3600`.

## Risks

1. **More BKD GET calls per tick.** Mitigated above (≤10 per 60s).
2. **A flapping BKD `session_status` field could escalate a session
   that's transiently reported non-running.** This is the same risk
   the current code carries (only the latency changes). If observed,
   the right fix is BKD-side: stabilize `sessionStatus` reporting.
3. **Concurrent agents (challenger / verifier / fixer / etc.) running on
   the same REQ already use this watchdog tick — they all benefit
   equally from the fast path; nothing dispatch-specific.**
