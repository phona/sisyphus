package resource_test

import (
	"testing"
	"time"

	"github.com/phona/sisyphus/testkit/resource"
)

func TestSanitizeBudgetName(t *testing.T) {
	cases := []struct {
		input string
		want  string
	}{
		{"integration", "integration"},
		{"my-suite", "my_suite"},
		{"pkg/sub", "pkg_sub"},
		{"My.Service:v2", "my_service_v2"},
		{"a b c", "a_b_c"},
	}
	for _, c := range cases {
		got := resource.SanitizeBudgetName(c.input)
		if got != c.want {
			t.Errorf("SanitizeBudgetName(%q) = %q, want %q", c.input, got, c.want)
		}
	}
}

func TestAcquireResourceBudget_EmptyName(t *testing.T) {
	_, err := resource.AcquireResourceBudget(nil, "", 1, time.Second)
	if err == nil {
		t.Fatal("expected error for empty name")
	}
}

func TestAcquireResourceBudget_ZeroSlots(t *testing.T) {
	_, err := resource.AcquireResourceBudget(nil, "test", 0, time.Second)
	if err == nil {
		t.Fatal("expected error for zero slots")
	}
}

func TestAcquireResourceBudget_NegativeSlots(t *testing.T) {
	_, err := resource.AcquireResourceBudget(nil, "test", -1, time.Second)
	if err == nil {
		t.Fatal("expected error for negative slots")
	}
}

func TestAcquireResourceBudget_NilDB(t *testing.T) {
	_, err := resource.AcquireResourceBudget(nil, "test", 1, time.Second)
	if err == nil {
		t.Fatal("expected error for nil db")
	}
}

func TestAcquireResourceBudgetFromEnv_EnvOverride(t *testing.T) {
	// Set env vars for "mybudget" — slots=0 triggers validation error before DB is needed.
	t.Setenv("TEST_RESOURCE_BUDGET_MYBUDGET_SLOTS", "0")
	_, err := resource.AcquireResourceBudgetFromEnv(nil, "mybudget", 2, time.Minute)
	if err == nil {
		t.Fatal("expected error: env override set slots=0")
	}
}
