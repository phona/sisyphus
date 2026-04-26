## ADDED Requirements

### Requirement: sisyphus-dispatch MCP server exposes read-only REQ inspection tools over stdio

The sisyphus orchestrator codebase SHALL ship a Python package
`orchestrator.dispatch_mcp` providing an MCP server, runnable as
`python -m orchestrator.dispatch_mcp`, that connects to the
orchestrator's Postgres `req_state` table and MUST register exactly
two read-only tools — `get_req_state` and `list_reqs` — over the
stdio transport defined in the Model Context Protocol specification.
The server MUST NOT register any tool that mutates `req_state`,
`event_log`, `stage_runs`, or `verifier_decisions`; mutating
operations are deferred to later milestones.

The server MUST initialise its own asyncpg pool via
`orchestrator.store.db.init_pool(settings.pg_dsn)` before accepting
requests, and SHALL close that pool when the stdio session ends. The
server MUST NOT reuse the orchestrator FastAPI process's singleton
pool — the MCP server runs as an independent process whose lifecycle
is decoupled from the webhook handler.

#### Scenario: DISPATCH-MCP-S1 get_req_state returns shape for an existing REQ

- **GIVEN** the `req_state` row for `REQ-x` has
  `state=ANALYZING`, `project_id=p1`, `context={"a":1,"b":2}`,
  `history=[{"to":"analyzing","ts":"2026-04-26T10:00:00Z"}]`
- **WHEN** `fetch_req_state(pool, "REQ-x")` is awaited
- **THEN** the returned dict contains keys
  `req_id`, `project_id`, `state`, `created_at`, `updated_at`,
  `last_event`, `context_keys` and `state == "analyzing"` and
  `sorted(context_keys) == ["a", "b"]`

#### Scenario: DISPATCH-MCP-S2 get_req_state returns None for a missing REQ

- **GIVEN** the `req_state` table has no row for `REQ-missing`
- **WHEN** `fetch_req_state(pool, "REQ-missing")` is awaited
- **THEN** the returned value is `None`

#### Scenario: DISPATCH-MCP-S3 list_reqs default limit is 50 and clamped to [1, 200]

- **GIVEN** the caller passes `limit=999`
- **WHEN** `fetch_reqs(pool, state=None, limit=999)` is awaited
- **THEN** the SQL `LIMIT` parameter passed to the pool MUST equal `200`

  AND **GIVEN** the caller passes `limit=0`
- **WHEN** the same function is awaited
- **THEN** the SQL `LIMIT` parameter MUST equal `1`

#### Scenario: DISPATCH-MCP-S4 list_reqs with explicit state filters by ReqState value

- **GIVEN** the caller passes `state="analyzing"`
- **WHEN** `fetch_reqs(pool, state="analyzing", limit=50)` is awaited
- **THEN** the SQL `WHERE` clause MUST filter on `state = $1` and the
  bound `$1` value MUST be the literal string `analyzing`

#### Scenario: DISPATCH-MCP-S5 list_reqs raises ValueError on unknown state

- **GIVEN** the caller passes `state="banana"`
- **WHEN** `fetch_reqs(pool, state="banana", limit=50)` is awaited
- **THEN** the call MUST raise `ValueError` whose message contains
  the substring `unknown state` and the list of valid values

#### Scenario: DISPATCH-MCP-S6 get_req_state redacts context body to keys-only

- **GIVEN** the `req_state.context` for `REQ-redact` contains
  `{"prompt":"<long secret text>","intent":{"k":"v"}}`
- **WHEN** `fetch_req_state(pool, "REQ-redact")` is awaited
- **THEN** the result MUST contain `context_keys` equal to
  `["prompt","intent"]` (in any order) and MUST NOT contain a top-level
  `context` field nor any string value from the original context body
