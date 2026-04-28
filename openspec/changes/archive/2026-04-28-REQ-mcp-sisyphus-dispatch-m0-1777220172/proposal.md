# feat(mcp): sisyphus-dispatch MCP server M0

## Why

Operators and Claude-Code IDE agents inspect REQ state today by either
hitting `orchestrator.admin` HTTP endpoints by hand or shelling into the
Postgres pod and writing SQL. Neither is ergonomic for an interactive
agent loop, and both require credentials wired up case-by-case.

The medium-term goal is a stable **MCP** surface — `sisyphus-dispatch` —
that any MCP-speaking client (Claude Code, custom dev tooling) can
attach to and call tools like "what's REQ X doing right now?", "list
in-flight REQs", and (in later milestones) "force-resume", "open a fresh
intake". M0 is the foundation milestone: ship the package shape, the
server entrypoint, and **read-only inspection tools** so future
milestones can layer mutating operations on top without re-litigating
package layout, transport choice, or DB-pool wiring.

> **Assumption flagged for reviewer.** The intent issue title is the
> entire spec we received. We are interpreting "sisyphus-dispatch MCP
> server M0" as "a server-side MCP that exposes sisyphus orchestrator
> state to MCP clients, scaffolded with the smallest useful read-only
> tool set". If the intent was different (e.g. an MCP **client** for
> dispatching to other systems, or a replacement for `bkd_mcp.py` which
> is BKD's old MCP **client**), call this out on the PR — the package
> name `dispatch_mcp` is the only thing M1 would need to rename.

## What Changes

- **New Python package** `orchestrator/src/orchestrator/dispatch_mcp/`
  with three modules:
  - `queries.py` — pure async DB-read helpers
    (`fetch_req_state(pool, req_id)`,
    `fetch_reqs(pool, *, state, limit)`) returning JSON-serialisable
    dicts. No FastMCP coupling so unit tests stay fast.
  - `server.py` — constructs a `mcp.server.fastmcp.FastMCP` instance,
    registers two `@mcp.tool()` wrappers around the queries, and
    exposes `run_stdio()` that lazily inits the asyncpg pool from
    `settings.pg_dsn` before handing control to the SDK's stdio
    transport.
  - `__main__.py` — `python -m orchestrator.dispatch_mcp` entrypoint
    that calls `run_stdio()`. M0 ships **stdio only**; HTTP/SSE
    transport and auth are explicitly deferred to M1+.

- **New dep** `mcp>=1.2` in `orchestrator/pyproject.toml`. We use
  FastMCP because handcrafting JSON-RPC + tool-list + initialize
  handshakes for M0 just to throw it away in M1 has zero upside.

- **Two read-only tools** exposed by the M0 server:
  - `get_req_state(req_id: str)` — returns
    `{req_id, project_id, state, created_at, updated_at, last_event,
    context_keys}`. We return `context_keys` (top-level keys list) not
    full `context` because contexts hold tokens / prompts / agent
    output that an IDE agent has no reason to slurp wholesale.
  - `list_reqs(state: str | None = None, limit: int = 50)` — returns a
    list of `{req_id, project_id, state, updated_at}`. `limit`
    clamped to `[1, 200]` server-side; `state` validated against the
    `ReqState` enum so a typo gets a clean error instead of silently
    returning everything.

- **Unit tests** `orchestrator/tests/test_dispatch_mcp.py` covering
  the queries module against a fake asyncpg pool (same pattern as
  `tests/test_admission.py`). No FastMCP harness — the queries are
  plain async functions and that's where the logic lives.

## Impact

- **Affected specs**: new capability `dispatch-mcp` (purely additive).
- **Affected code**: new package + tests; one-line dep bump in
  `pyproject.toml`. No existing module modified.
- **Deployment / migration**: zero ops. The MCP server is **not**
  auto-started by the orchestrator FastAPI app — operators who want it
  run `python -m orchestrator.dispatch_mcp` next to the orchestrator
  pod (or locally, given `SISYPHUS_PG_DSN`). M1 wires this into a Helm
  sidecar / second deployment with HTTP transport.
- **Risk**: very low. Server is isolated (separate process, separate
  asyncpg pool, never imported by `webhook` / `engine` / `actions`).
  Only read paths against `req_state`. If `mcp` SDK install fails on
  the runner, dev_cross_check / staging_test fail loudly — which is
  what we want.
- **Out of scope (deferred to M1+)**:
  - HTTP / SSE transport, OAuth / token auth.
  - Mutating tools (force-resume, cancel, open-intake, follow-up).
  - Reading from `stage_runs` / `verifier_decisions` (M0 sticks to
    `req_state` so we don't have to make UX decisions about which
    columns to surface).
  - Helm chart / sidecar deployment.
  - Resource (`mcp.resource`) URIs — we go tool-only for M0 because
    every IDE client today understands tools; resource support
    varies.
