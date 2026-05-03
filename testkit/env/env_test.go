package env_test

import (
	"testing"

	"github.com/phona/sisyphus/testkit/env"
)

func TestGetEnv(t *testing.T) {
	t.Setenv("TESTKIT_ENV_X", "hello")

	if got := env.GetEnv("TESTKIT_ENV_X", "default"); got != "hello" {
		t.Fatalf("expected hello, got %q", got)
	}
	if got := env.GetEnv("TESTKIT_ENV_UNSET_12345", "default"); got != "default" {
		t.Fatalf("expected default, got %q", got)
	}
}

func TestGetEnv_EmptyVarUsesDefault(t *testing.T) {
	t.Setenv("TESTKIT_ENV_EMPTY", "")
	if got := env.GetEnv("TESTKIT_ENV_EMPTY", "fallback"); got != "fallback" {
		t.Fatalf("expected fallback for empty var, got %q", got)
	}
}

func TestGetEnvInt(t *testing.T) {
	t.Setenv("TESTKIT_INT_X", "42")

	if got := env.GetEnvInt("TESTKIT_INT_X", 0); got != 42 {
		t.Fatalf("expected 42, got %d", got)
	}
	if got := env.GetEnvInt("TESTKIT_INT_UNSET_12345", 99); got != 99 {
		t.Fatalf("expected default 99, got %d", got)
	}
}

func TestGetEnvInt_InvalidValue(t *testing.T) {
	t.Setenv("TESTKIT_INT_INVALID", "notanint")
	if got := env.GetEnvInt("TESTKIT_INT_INVALID", 7); got != 7 {
		t.Fatalf("expected default 7 for invalid value, got %d", got)
	}
}
