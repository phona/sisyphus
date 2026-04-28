## ADDED Requirements

### Requirement: RunnerController serializes all K8s API calls to prevent thread-unsafe ApiClient race

`RunnerController` SHALL hold an `asyncio.Lock` (`_k8s_api_lock`) and route every
call to `self.core_v1.<method>` through a `_k8s()` helper that acquires this lock
before delegating to `asyncio.to_thread`. The lock MUST be held for the full duration
of each `asyncio.to_thread` invocation and released immediately after it returns, so
that at most one `CoreV1Api` call runs in a thread at any given moment.

This requirement exists because `kubernetes-python`'s `ApiClient` is not thread-safe
when shared across concurrent `asyncio.to_thread` calls: concurrent threads corrupt
the client's internal HTTP/WebSocket dispatch state, causing normal HTTP 200 JSON
responses to be misidentified as failed WebSocket handshakes and raising
`ApiException(status=0)`.

#### Scenario: KRRACE-S1 two concurrent ensure_runner calls both succeed without ApiException(status=0)

- **GIVEN** a `RunnerController` backed by a thread-unsafe fake `CoreV1Api` that raises
  `ApiException(status=0)` if any two of its methods execute concurrently in separate threads
- **WHEN** `asyncio.gather(ensure_runner("REQ-A"), ensure_runner("REQ-B"))` is awaited
  with `wait_ready=True`
- **THEN** both coroutines return the correct pod names (`"runner-req-a"`, `"runner-req-b"`)
  without raising any exception, because `_k8s_api_lock` serializes calls and the fake
  client never sees concurrent thread access

#### Scenario: KRRACE-S2 _k8s_api_lock is present as an asyncio.Lock on every RunnerController instance

- **GIVEN** a `RunnerController` constructed with any valid parameters
- **WHEN** the instance attribute `_k8s_api_lock` is inspected
- **THEN** it is an instance of `asyncio.Lock`
