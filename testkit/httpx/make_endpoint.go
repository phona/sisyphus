package httpx

import (
	"os/exec"
	"strings"
	"testing"
)

// NewServiceClientFromMake runs `make <makeTarget>` in dir (defaults to "." when
// empty), trims stdout, and returns an HTTPClient whose base URL is the output.
// Calls tb.Fatalf if make exits non-zero or produces empty output.
func NewServiceClientFromMake(tb testing.TB, makeTarget, dir string, opts ...Option) *HTTPClient {
	tb.Helper()
	if dir == "" {
		dir = "."
	}
	cmd := exec.Command("make", makeTarget)
	cmd.Dir = dir
	out, err := cmd.Output()
	if err != nil {
		tb.Fatalf("make %s in %s failed: %v", makeTarget, dir, err)
	}
	url := strings.TrimSpace(string(out))
	if url == "" {
		tb.Fatalf("make %s in %s produced empty output", makeTarget, dir)
	}
	return NewServiceClient(url, opts...)
}
