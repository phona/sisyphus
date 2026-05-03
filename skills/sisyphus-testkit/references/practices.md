# Integration test practices

Distilled from ttpos-server-go; apply when bootstrapping a new repo with sisyphus-testkit.

## Build tags

Use `//go:build integration` on TestMain files that require Docker infra.
Unit tests must run without any tag: `go test ./...`.
Integration suite: `go test -tags integration ./tests/...`.

## tmpfs for MySQL

Mount MySQL data dir as tmpfs (`/var/lib/mysql`):
- CREATE DATABASE: ~5ms (vs ~200ms on disk)
- DROP DATABASE with 200 tables: ~0.3s (vs ~25s on disk)
- No fsync cost; safe because data is ephemeral anyway.

## Advisory lock resource budget

When multiple test packages run in parallel as separate binaries, use `resource.AcquireResourceBudget`
from `testkit/resource` to cap concurrent infra holders. Each package calls it from `TestMain`.

```go
func TestMain(m *testing.M) {
    db := openLockDB()
    release, err := resource.AcquireResourceBudgetFromEnv(db, "integration", 2, 10*time.Minute)
    if err != nil { log.Fatal(err) }
    defer release()
    os.Exit(m.Run())
}
```

Default env overrides:
- `TEST_RESOURCE_BUDGET_INTEGRATION_SLOTS=4`
- `TEST_RESOURCE_BUDGET_INTEGRATION_TIMEOUT_SEC=300`

## Stub priority

WireMock matches stubs in ascending priority order (1 first, 5 default).
- Priority 1: exact URI + body match (specific test scenario)
- Priority 5: catch-all for a URL pattern (default response)

Never use a catch-all without a specific override path — tests become order-dependent.

## Coverage merge

Go 1.20+ coverage binary (`-cover` flag) writes raw profiles to `GOCOVERDIR`.
Merge across multiple runs:

```bash
go tool covdata textfmt -i coverage/ -o merged.out
```

Then fix paths for SonarQube with `testkit/scripts/fix-coverage-paths.sh`.

## Fluent assertion style

Chain assertions on `*httpx.HTTPResponse`:

```go
resp := client.Post(t, "/api/endpoint", payload).
    AssertOK(t).
    AssertSuccess(t)

var result MyType
resp.JSON(t, &result)
```

Use `AssertBodyContains` for partial body checks; use `AssertErrorCode` for API error envelope checks.

## Template DB pattern

For repos with heavy DB schemas:
1. Create a `_template` DB once per process (`sync.Once` + advisory lock)
2. Clone per-test DB via `CREATE TABLE LIKE` (metadata-only, ~10ms for 200 tables)
3. Drop in `t.Cleanup`

This pattern lives in the business repo (Phase D), NOT in testkit (Phase C requires
second consumer before abstracting).
