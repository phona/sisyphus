# sisyphus/testkit

Zero-coupling Go test utilities for integration test suites. No business names, no auth logic, no DB setup — those stay in the consuming repo.

## Packages

| Package | Purpose |
|---|---|
| `testkit/env` | `GetEnv` / `GetEnvInt` — typed env helpers with default |
| `testkit/httpx` | HTTP test client with fluent assertions; `NewServiceClient` factory |
| `testkit/resource` | Cross-process MySQL advisory-lock resource budget |
| `testkit/scripts` | `fix-coverage-paths.sh` — normalize Go coverage paths for SonarQube |

## Red line

**No business names leak into testkit.** CI enforces this with:

```
make test-no-business-leak
```

Any match on `ttpos|shop_|cashier|company_uuid|staff_uuid|TTPOS` in `*.go` files fails the build.

## Quick start

```go
import (
    "testing"
    "github.com/phona/sisyphus/testkit/httpx"
    "github.com/phona/sisyphus/testkit/env"
)

func TestMyService(t *testing.T) {
    client := httpx.NewServiceClient(env.GetEnv("SERVICE_URL", "http://localhost:8080"))
    client.WithToken("test-token")
    resp := client.Get(t, "/health")
    resp.AssertOK(t)
}
```

For multi-service setups, wrap `NewServiceClient` in your own factory:

```go
// In your repo's tests/fixture/http.go (Phase D — stays in business repo)
func NewErpClient() *httpx.HTTPClient {
    return httpx.NewServiceClient(env.GetEnv("ERP_URL", "http://localhost:14021"))
}
```

## Resource budget

Limit parallel test packages that hold expensive infra (MySQL DBs, ports, etc.):

```go
// In TestMain
release, err := resource.AcquireResourceBudget(db, "integration", 2, 10*time.Minute)
if err != nil { log.Fatal(err) }
defer release()
```

Requires a `*sql.DB` backed by MySQL (advisory lock mechanism).
