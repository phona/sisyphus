# REQ-escalate-reason-verifier-1777076876: fix escalated_reason for verifier decisions

## Problem

When a verifier agent returns `action=escalate`, the webhook handler invokes `engine.step`
with `event=VERIFY_ESCALATE` but never sets `ctx.escalated_reason` beforehand. The `escalate`
action reads `ctx.escalated_reason` to pick the termination reason; without it, the action
falls back to `body.event.replace(".", "-")` = `"session-completed"`, which is semantically
meaningless and was not guarded by `_is_transient` as an explicit non-retryable reason.

## Solution

In `webhook.py`, after processing the verifier decision payload (section 5.6) and before
calling `engine.step`, check `if event == Event.VERIFY_ESCALATE` and patch ctx with
`escalated_reason = "verifier-decision"`. Update `_is_transient` in `escalate.py` to use
`"verifier-decision"` (matching the new canonical value).

## Scope

Single repo: `phona/sisyphus` (orchestrator only).

## Risks

Low. The fix is additive: adds one context key. Existing `_is_transient` already returns
`False` for `"verifier-decision"` via the fallthrough path; updating the explicit check is
a cosmetic consistency fix.
