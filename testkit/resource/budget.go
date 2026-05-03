// Package resource provides cross-process MySQL advisory-lock based resource budgeting
// for integration test suites. Use it from TestMain to bound the number of packages
// that can hold expensive resources (ports, DBs) simultaneously.
//
// Requires a *sql.DB backed by MySQL; the caller is responsible for driver
// registration (e.g. _ "github.com/go-sql-driver/mysql") and DB lifecycle.
package resource

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
	"time"

	"github.com/phona/sisyphus/testkit/env"
)

const (
	defaultBudgetTimeout = 10 * time.Minute
	defaultBudgetPoll    = 250 * time.Millisecond
	lockPrefix           = "testkit_budget"
)

// AcquireResourceBudget acquires one slot from a named advisory-lock budget.
// It polls until a slot is free or timeout is reached.
// Returns a release function that must be called (typically via defer) to free the slot.
//
// db must be a *sql.DB backed by MySQL. A dedicated connection is reserved for the lock
// duration; the caller's pool is not closed.
func AcquireResourceBudget(db *sql.DB, name string, slots int, timeout time.Duration) (func(), error) {
	if strings.TrimSpace(name) == "" {
		return nil, fmt.Errorf("resource budget name cannot be empty")
	}
	if slots <= 0 {
		return nil, fmt.Errorf("resource budget %q must have at least one slot", name)
	}
	if db == nil {
		return nil, fmt.Errorf("resource budget %q: db must not be nil", name)
	}
	if timeout <= 0 {
		timeout = defaultBudgetTimeout
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)

	conn, err := db.Conn(ctx)
	if err != nil {
		cancel()
		return nil, fmt.Errorf("reserve budget lock connection: %w", err)
	}

	deadline := time.Now().Add(timeout)
	pollInterval := time.Duration(env.GetEnvInt("TEST_RESOURCE_BUDGET_POLL_MS", int(defaultBudgetPoll/time.Millisecond))) * time.Millisecond
	if pollInterval <= 0 {
		pollInterval = defaultBudgetPoll
	}

	var acquiredLock string
	for time.Now().Before(deadline) {
		for slot := 0; slot < slots; slot++ {
			lockName := fmt.Sprintf("%s:%s:%d", lockPrefix, SanitizeBudgetName(name), slot)
			var result sql.NullInt64
			if err := conn.QueryRowContext(ctx, "SELECT GET_LOCK(?, 0)", lockName).Scan(&result); err != nil {
				conn.Close()
				cancel()
				return nil, fmt.Errorf("acquire budget lock %q: %w", lockName, err)
			}
			if result.Valid && result.Int64 == 1 {
				acquiredLock = lockName
				released := false
				release := func() {
					if released {
						return
					}
					released = true
					_, _ = conn.ExecContext(context.Background(), "SELECT RELEASE_LOCK(?)", acquiredLock)
					_ = conn.Close()
					cancel()
				}
				return release, nil
			}
		}

		select {
		case <-ctx.Done():
			conn.Close()
			cancel()
			return nil, fmt.Errorf("timeout waiting for resource budget %q (slots=%d)", name, slots)
		case <-time.After(pollInterval):
		}
	}

	conn.Close()
	cancel()
	return nil, fmt.Errorf("timeout waiting for resource budget %q (slots=%d)", name, slots)
}

// AcquireResourceBudgetFromEnv acquires a budget slot using per-budget env overrides:
//
//	TEST_RESOURCE_BUDGET_<NAME>_SLOTS
//	TEST_RESOURCE_BUDGET_<NAME>_TIMEOUT_SEC
func AcquireResourceBudgetFromEnv(db *sql.DB, name string, defaultSlots int, defaultTimeout time.Duration) (func(), error) {
	envKey := strings.ToUpper(SanitizeBudgetName(name))
	slots := env.GetEnvInt(fmt.Sprintf("TEST_RESOURCE_BUDGET_%s_SLOTS", envKey), defaultSlots)
	timeoutSec := env.GetEnvInt(fmt.Sprintf("TEST_RESOURCE_BUDGET_%s_TIMEOUT_SEC", envKey), int(defaultTimeout/time.Second))
	return AcquireResourceBudget(db, name, slots, time.Duration(timeoutSec)*time.Second)
}

// SanitizeBudgetName replaces characters not safe for MySQL lock names with underscores.
func SanitizeBudgetName(name string) string {
	replacer := strings.NewReplacer("/", "_", "\\", "_", ":", "_", " ", "_", "-", "_", ".", "_")
	return replacer.Replace(strings.ToLower(name))
}
