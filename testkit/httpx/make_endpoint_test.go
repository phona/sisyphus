package httpx_test

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/phona/sisyphus/testkit/httpx"
)

// TestNewServiceClientFromMake_BC2 verifies that NewServiceClientFromMake
// runs `make <target>` in the given dir, strips whitespace from stdout, and
// uses the result as the base URL for the returned HTTPClient.
func TestNewServiceClientFromMake_BC2(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"code":0,"message":"ok","data":null}`))
	}))
	defer srv.Close()

	dir := t.TempDir()
	makefile := filepath.Join(dir, "Makefile")
	content := "endpoint:\n\t@echo " + srv.URL + "\n"
	if err := os.WriteFile(makefile, []byte(content), 0o644); err != nil {
		t.Fatalf("write Makefile: %v", err)
	}

	client := httpx.NewServiceClientFromMake(t, "endpoint", dir)
	resp := client.Get(t, "/")
	resp.AssertOK(t)
}

// TestPostNoWait_BC3 verifies that PostNoWait sends the request, returns the
// status code, and does not block on the response body.
func TestPostNoWait_BC3(t *testing.T) {
	received := make(chan struct{}, 1)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		received <- struct{}{}
		w.WriteHeader(http.StatusAccepted)
		w.Write([]byte(`{"code":0,"message":"accepted","data":null}`))
	}))
	defer srv.Close()

	client := httpx.NewServiceClient(srv.URL)
	status := client.PostNoWait(t, "/trigger", map[string]string{"action": "bind"})

	if status != http.StatusAccepted {
		t.Fatalf("PostNoWait returned status %d, want %d", status, http.StatusAccepted)
	}
	select {
	case <-received:
	default:
		t.Fatal("server did not receive the POST request")
	}
}
