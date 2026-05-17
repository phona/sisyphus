## ADDED Requirements

### Requirement: testkit/httpx MUST 提供 NodePort URL 构造函数

The package `testkit/httpx` SHALL expose a package-level function `NodePortURL`
with signature `func NodePortURL(nodeIP string, nodePort int) string`. The
function MUST return a string of the form `http://<nodeIP>:<nodePort>` where
`<nodeIP>` is the `nodeIP` argument and `<nodePort>` is the decimal representation
of the `nodePort` argument. The function MUST NOT make network calls, start
goroutines, or produce side effects of any kind.

#### Scenario: BC1 NodePortURL constructs correct http URL

- **GIVEN** any non-empty string `nodeIP` and any positive integer `nodePort`
- **WHEN** the caller invokes `httpx.NodePortURL(nodeIP, nodePort)`
- **THEN** the return value equals `fmt.Sprintf("http://%s:%d", nodeIP, nodePort)`
  with no trailing slash, no scheme other than `http`, and no network activity
  as a side effect

---

### Requirement: testkit/httpx MUST 提供 make endpoint 动态发现工厂

The package `testkit/httpx` SHALL expose a function `NewServiceClientFromMake`
with signature
`func NewServiceClientFromMake(tb testing.TB, makeTarget, dir string, opts ...Option) *HTTPClient`.
The function MUST invoke `make <makeTarget>` as a subprocess with its working
directory set to `dir` (or `"."` when `dir` is the empty string). It MUST
trim leading and trailing whitespace from the subprocess stdout and use the
result as the `baseURL` for a new `HTTPClient` constructed via `NewServiceClient`.
If the `make` invocation exits with a non-zero status, the function MUST call
`tb.Fatalf` and not return an `*HTTPClient`.

#### Scenario: BC2 NewServiceClientFromMake uses make output as base URL

- **GIVEN** a directory `dir` that contains a `Makefile` with target
  `endpoint` that prints a well-formed URL (e.g., `http://10.0.0.1:30080`)
  followed by a newline
- **WHEN** the caller invokes `httpx.NewServiceClientFromMake(t, "endpoint", dir)`
- **THEN** the returned `*HTTPClient` has its base URL set to
  `"http://10.0.0.1:30080"` (whitespace stripped) and can send HTTP requests
  to that base

---

### Requirement: testkit/httpx HTTPClient MUST 提供 no-wait POST 方法

The type `httpx.HTTPClient` SHALL expose a method `PostNoWait` with signature
`func (c *HTTPClient) PostNoWait(tb testing.TB, path string, body any, headers ...map[string]string) int`.
The method MUST send a POST request to `c.baseURL + path` with the same
auth-header and persistent-header logic as `Post`, serialise `body` to JSON
when non-nil, and return the HTTP response status code. The method MUST close
the response body immediately after reading the status code without reading any
response bytes, satisfying fire-and-forget semantics for async trigger endpoints
such as `/api/v1/callboard/device/bind_code`. If the request fails at the
transport layer, the method MUST call `tb.Fatalf`.

#### Scenario: BC3 PostNoWait sends request and returns status without reading body

- **GIVEN** an HTTP server that accepts POST on `/trigger`, records receipt,
  and returns HTTP 202 with a non-empty body
- **WHEN** the caller invokes `client.PostNoWait(t, "/trigger", payload)`
- **THEN** the return value is `202`, the server has received exactly one POST
  request, and the test does not block waiting for the response body to be read

---

### Requirement: _pipeline_marker 模块 MUST 提供 np-demo4 验证常量

The module at `orchestrator/src/orchestrator/_pipeline_marker.py` SHALL expose
a module-level string attribute named `NP_DEMO4_REQ`. The attribute MUST hold
the literal string `"REQ-np-demo4-1779013770"`. It MUST coexist with
`PIPELINE_VALIDATION_REQ`, `PIPELINE_VALIDATION_REQ_V3`, and
`SMOKE_PIPELINE_V3_REQ` without modifying any of them. The module MUST NOT
import `NP_DEMO4_REQ` anywhere in the production code path (engine / router /
actions / checkers / store).

#### Scenario: NPD4-S1 module exposes NP_DEMO4_REQ without side effects

- **GIVEN** a fresh Python interpreter with `orchestrator/src` on `sys.path`
- **WHEN** `importlib.import_module("orchestrator._pipeline_marker")` is called
- **THEN** the module object exposes `NP_DEMO4_REQ` and no log line, network
  call, or filesystem write is observable as a side effect

#### Scenario: NPD4-S2 NP_DEMO4_REQ is str matching REQ-np-demo4 pattern

- **GIVEN** the module `orchestrator._pipeline_marker` has been imported
- **WHEN** `NP_DEMO4_REQ` is read from the module
- **THEN** the value is a `str` instance matching `^REQ-np-demo4-\d+$`

#### Scenario: NPD4-S3 all four pipeline-marker constants coexist unchanged

- **GIVEN** `orchestrator._pipeline_marker` imported
- **WHEN** the test reads all four constants
- **THEN** `PIPELINE_VALIDATION_REQ`, `PIPELINE_VALIDATION_REQ_V3`, and
  `SMOKE_PIPELINE_V3_REQ` retain their original literal values and `NP_DEMO4_REQ`
  equals `"REQ-np-demo4-1779013770"`
