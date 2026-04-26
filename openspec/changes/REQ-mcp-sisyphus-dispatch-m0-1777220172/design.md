# Design: sisyphus-dispatch MCP server M0

## Interpretation of the intent

Title-only intent: `feat(mcp): sisyphus-dispatch MCP server M0`. We
read this as **server-side**, because the only existing MCP code path
in the repo is `orchestrator/src/orchestrator/bkd_mcp.py` which is
already a *client*, and the project philosophy in CLAUDE.md is "走 BKD
REST，不走 MCP". So sisyphus is not consuming MCP — it would be
*producing* MCP, exposing its orchestration surface to other MCP
clients. The "dispatch" name aligns with the verb sisyphus already uses
in spec text ("sisyphus-dispatched sub-agents"): an external MCP client
asking the orchestrator about, and eventually directing, REQ flow.

If the reviewer disagrees with this read, M1's only sunk cost is the
package name — the queries / tests are useful regardless.

## Why FastMCP, not handrolled JSON-RPC

Two reasons:

1. **MCP handshake is non-trivial.** The protocol requires a stateful
   initialize → notifications/initialized → tools/list → tools/call
   sequence over either stdio or HTTP-SSE, with capability
   negotiation. Hand-coding this for M0 means rebuilding it in M1 when
   we add HTTP transport. FastMCP gives both transports for free.
2. **Tool schema generation.** FastMCP introspects type hints and
   docstrings to publish JSON-Schema for each tool. Hand-rolling
   schemas is error-prone and would diverge from the Python signatures.

The cost is one new dep (`mcp`). The orchestrator runner image already
caches uv wheels on PVC (REQ-runner-cache-on-pvc-1777198512), so this
is an install-once hit.

## Why stdio only in M0

The two transports MCP supports are stdio (one client per process,
parent launches child) and Streamable HTTP / SSE (long-running
server, multiple clients).

- stdio fits the "developer launches `python -m
  orchestrator.dispatch_mcp` from Claude Code config" use case
  with zero deployment work — Claude Code already knows how to spawn
  stdio MCP servers.
- HTTP needs a port, a service, an ingress, and an auth model. Doing
  any of those in M0 forces decisions (port number, token store,
  multi-tenant isolation) that we don't yet have signal on.

So M0 ships stdio. M1 adds Streamable HTTP for an in-cluster sidecar.

## Why two tools and not more

- `get_req_state` answers "what's REQ X doing?" — the single most
  common question.
- `list_reqs` answers "what's running right now?" — the second most
  common.

Together they cover ~all read-only inspection an interactive operator
needs from an IDE. `stage_runs` / `verifier_decisions` views require
deciding which columns to surface and how to render them, which is its
own design conversation; we defer rather than guess.

## Why redact `context` to keys-only

`req_state.context` is a free-form JSONB blob holding agent prompts,
finalized intent JSON, escalate reasons, and (for some past REQs) raw
agent output text. An MCP tool result is shown directly to the IDE
client. Returning the full context unconditionally:

- bloats tool results past sane sizes (some contexts run >50 KB),
- could leak prompts the user didn't intend to share with whoever is
  attached to the MCP server,
- and ships unstructured text where the M0 contract is "JSON-shaped
  state".

Keys-only gives the agent enough hint to ask follow-up tools in later
milestones (e.g. `get_req_context_field(req_id, key)`). Cheap to
return, predictable shape, no leakage.

## Why no FastMCP test harness

`@mcp.tool()` registers the function in FastMCP's internal tool dict
without modifying it. The Python function is still callable directly
with the same signature. Our tests therefore call the **queries**
module — pure async functions — and skip the registration layer
entirely. This:

- avoids depending on `mcp` SDK internals in tests,
- keeps tests fast (no SDK init, no transport boot),
- and means a future SDK upgrade that changes the decorator
  internals doesn't break our unit suite.

A future integration test (M18 challenger) can drive the full
stdio transport end-to-end if we want black-box coverage.

## DB pool ownership

The MCP server runs as a **separate process** from the orchestrator
FastAPI app. It has its own asyncpg pool. We don't reach into
`store.db._pool` because that singleton is owned by the FastAPI
lifecycle. Instead `run_stdio()` calls `init_pool(settings.pg_dsn)`
itself before invoking `mcp.run("stdio")`, and `close_pool()` on
shutdown. Tests pass a fake pool directly into the queries — they
never touch the singleton.
