# tasks: REQ-mcp-sisyphus-dispatch-m0-1777220172

## Stage: contract / spec

- [x] author `specs/dispatch-mcp/spec.md` with delta `## ADDED Requirements`
- [x] write 6 scenarios `DISPATCH-MCP-S{1..6}` covering: get_req_state
      happy / get_req_state missing / list_reqs default limit /
      list_reqs filtered by state / list_reqs invalid state error /
      get_req_state redacts context to keys-only

## Stage: implementation

- [x] add `mcp>=1.2` to `orchestrator/pyproject.toml` dependencies
- [x] `orchestrator/src/orchestrator/dispatch_mcp/__init__.py`: package marker
- [x] `orchestrator/src/orchestrator/dispatch_mcp/queries.py`:
      `fetch_req_state(pool, req_id)` and
      `fetch_reqs(pool, *, state, limit)` returning JSON-serialisable dicts
- [x] `orchestrator/src/orchestrator/dispatch_mcp/server.py`:
      FastMCP instance + two `@mcp.tool()` wrappers + `run_stdio()`
- [x] `orchestrator/src/orchestrator/dispatch_mcp/__main__.py`:
      stdio entrypoint
- [x] `orchestrator/tests/test_dispatch_mcp.py`: unit tests for the
      6 scenarios against a fake asyncpg pool

## Stage: PR

- [x] git push `feat/REQ-mcp-sisyphus-dispatch-m0-1777220172`
- [x] gh pr create
