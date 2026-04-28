# accept-env-gc-skeleton delta

## ADDED Requirements

### Requirement: orchestrator MUST ship an accept_env_gc skeleton module exposing async gc_once and run_loop stubs

The orchestrator package SHALL ship a module
`orchestrator.accept_env_gc` (file `orchestrator/src/orchestrator/accept_env_gc.py`)
that exposes two top-level coroutine functions: `gc_once()` and `run_loop()`.
Both functions MUST be defined with `async def` so that
`asyncio.iscoroutinefunction(accept_env_gc.gc_once)` and
`asyncio.iscoroutinefunction(accept_env_gc.run_loop)` both return `True`.
This pins the public coroutine API surface of the future GC subsystem so the
follow-up implementation REQ can land business logic without changing the
contract that callers (e.g. `main.py` startup wiring) will rely on. The module
SHALL NOT introduce any imports of `orchestrator.config`,
`orchestrator.k8s_runner`, `orchestrator.store`, or any kubernetes / asyncpg /
httpx client at this skeleton stage, so that `import orchestrator.accept_env_gc`
has zero runtime side effects (no DB pool init, no K8s client creation) and is
safe to import inside unit tests that do not set the production env vars.

#### Scenario: AEGS-S1 module exposes gc_once and run_loop as async coroutine functions

- **GIVEN** a Python interpreter with the `orchestrator` package importable
- **WHEN** the test imports `orchestrator.accept_env_gc as accept_env_gc`
- **THEN** the import MUST succeed without raising
- **AND** `asyncio.iscoroutinefunction(accept_env_gc.gc_once)` MUST be `True`
- **AND** `asyncio.iscoroutinefunction(accept_env_gc.run_loop)` MUST be `True`

### Requirement: gc_once and run_loop MUST raise NotImplementedError until a follow-up REQ lands the implementation

The skeleton coroutines `gc_once()` and `run_loop()` SHALL each raise
`NotImplementedError` when awaited, with a message that explicitly identifies
the function as a skeleton placeholder (containing the literal substring
`accept_env_gc skeleton`). This fail-loud stub SHALL prevent any caller (e.g.
an accidental `main.py` startup task) from silently succeeding or hanging
forever in a no-op infinite loop. The follow-up implementation REQ MUST
replace both bodies with real K8s + DB logic and MUST also retire the
skeleton-only contract test that asserts the `NotImplementedError` raise
(this skeleton-stage test is intentionally throwaway and not part of the
long-lived `accept-env-gc` capability that the implementation REQ will
introduce).

#### Scenario: AEGS-S2 awaiting gc_once raises NotImplementedError with the skeleton marker

- **GIVEN** the freshly imported `orchestrator.accept_env_gc` module
- **WHEN** the test awaits `accept_env_gc.gc_once()`
- **THEN** a `NotImplementedError` MUST be raised
- **AND** the exception message MUST contain the literal substring
  `accept_env_gc skeleton`

#### Scenario: AEGS-S3 awaiting run_loop raises NotImplementedError with the skeleton marker

- **GIVEN** the freshly imported `orchestrator.accept_env_gc` module
- **WHEN** the test awaits `accept_env_gc.run_loop()`
- **THEN** a `NotImplementedError` MUST be raised
- **AND** the exception message MUST contain the literal substring
  `accept_env_gc skeleton`
