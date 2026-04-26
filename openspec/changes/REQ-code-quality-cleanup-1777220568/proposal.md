# chore(code): unused imports / dead code / lint

## Why

Routine hygiene sweep on `orchestrator/`. Two small but real findings the
project's existing `ruff` + `vulture` scans surface that have no behaviour
attached to them:

1. **`router.derive_event` carries a dead parameter.**
   `def derive_event(event_type, tags, result_tags_only: bool = False)` —
   `result_tags_only` was never wired up. No caller in `src/` or `tests/`
   passes it; the function body never reads it. `git log -S` traces it back
   to the initial Python orchestrator commit (`d5e791b`) and it has been
   unused since. Vulture flags it at 100 % confidence.

2. **`__aexit__(self, *exc)` in two BKD client classes.**
   Both `BKDMcpClient` and `BKDRestClient` close their `httpx` client in
   `__aexit__` and ignore the exception triple. The current `*exc` name
   reads like a real variable; vulture flags it as "unused variable" with
   100 % confidence. The rest of the codebase already uses the
   `*_exc` / `*_a` / `*_` convention to signal "deliberately swallowed
   protocol args" (see e.g. `tests/test_webhook_upstream_done.py:27`,
   `tests/test_contract_escalate_pr_merged_override_challenger.py:73`).

Both are pure cleanup — no caller observes a behaviour change.

## What Changes

- **`orchestrator/src/orchestrator/router.py`** — drop the unused
  `result_tags_only` keyword parameter from `derive_event`. The function
  signature becomes `def derive_event(event_type: str, tags: Iterable[str])
  -> Event | None`. All existing call sites already pass two positional
  arguments only, so this is signature-narrowing without any caller fix-up.

- **`orchestrator/src/orchestrator/bkd_mcp.py`** and
  **`orchestrator/src/orchestrator/bkd_rest.py`** — rename the
  `__aexit__(self, *exc)` slurp to `__aexit__(self, *_exc)` so the
  intent ("absorb and discard the protocol triple") is explicit and
  vulture stops flagging it.

No tests need to change: the unit suite (`pytest -m "not integration"`,
925 tests) keeps passing as-is.

## Impact

- **Affected specs**: new capability `code-quality` (ADDED).
- **Affected code**: three source files in `orchestrator/src/orchestrator/`
  (`router.py`, `bkd_mcp.py`, `bkd_rest.py`). No tests, no migrations,
  no helm / runner / docs changes.
- **Deployment / migration**: zero-ops. Standard orchestrator rollout
  picks up the change with no schema or config touch.
- **Risk**: trivially low. The dropped parameter never had a reader; the
  `*exc` → `*_exc` rename is a parameter-name change in a private slot
  of the async-context-manager protocol and Python never inspects the
  name.
- **Out of scope**: every other vulture finding. The remaining 60 %-
  confidence hits (FastAPI route handlers, `@register`-decorated action
  handlers, pydantic config fields, pytest fixtures, mock attribute
  assignments) are framework-driven false positives. The `ARG001` /
  `ARG002` flags on action-handler parameters are similarly out of scope:
  the `*, body, req_id, tags, ctx` quartet is the registered handler
  contract documented at `actions/__init__.py` top-of-file and individual
  handlers may legitimately ignore some of them.
