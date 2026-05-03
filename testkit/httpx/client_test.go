package httpx_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/phona/sisyphus/testkit/httpx"
)

func newEchoServer() *httptest.Server {
	mux := http.NewServeMux()

	mux.HandleFunc("/ok", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"code":0,"message":"ok","data":null}`))
	})

	mux.HandleFunc("/headers", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{
			"authorization": r.Header.Get("Authorization"),
			"x-custom":      r.Header.Get("X-Custom"),
		})
	})

	mux.HandleFunc("/echo", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusCreated)
		io := r.Body
		defer io.Close()
		var body any
		json.NewDecoder(io).Decode(&body)
		json.NewEncoder(w).Encode(body)
	})

	mux.HandleFunc("/fail", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusBadRequest)
		w.Write([]byte(`{"code":400,"message":"bad","data":null}`))
	})

	return httptest.NewServer(mux)
}

func TestNewServiceClient_Get(t *testing.T) {
	srv := newEchoServer()
	defer srv.Close()

	client := httpx.NewServiceClient(srv.URL)
	resp := client.Get(t, "/ok")
	resp.AssertOK(t).AssertSuccess(t)
}

func TestNewServiceClient_WithTokenOption(t *testing.T) {
	srv := newEchoServer()
	defer srv.Close()

	client := httpx.NewServiceClient(srv.URL, httpx.WithToken("mytoken"))
	resp := client.Get(t, "/headers")
	resp.AssertOK(t)

	var body map[string]string
	resp.JSON(t, &body)
	if body["authorization"] != "Bearer mytoken" {
		t.Fatalf("expected Bearer mytoken, got %q", body["authorization"])
	}
}

func TestHTTPClient_WithTokenBuilder(t *testing.T) {
	srv := newEchoServer()
	defer srv.Close()

	client := httpx.NewServiceClient(srv.URL).WithToken("buildertoken")
	resp := client.Get(t, "/headers")
	resp.AssertOK(t)

	var body map[string]string
	resp.JSON(t, &body)
	if body["authorization"] != "Bearer buildertoken" {
		t.Fatalf("expected Bearer buildertoken, got %q", body["authorization"])
	}
}

func TestHTTPClient_WithHeadersBuilder(t *testing.T) {
	srv := newEchoServer()
	defer srv.Close()

	client := httpx.NewServiceClient(srv.URL).WithHeaders(map[string]string{"X-Custom": "val"})
	resp := client.Get(t, "/headers")
	resp.AssertOK(t)

	var body map[string]string
	resp.JSON(t, &body)
	if body["x-custom"] != "val" {
		t.Fatalf("expected val, got %q", body["x-custom"])
	}
}

func TestHTTPClient_Post(t *testing.T) {
	srv := newEchoServer()
	defer srv.Close()

	client := httpx.NewServiceClient(srv.URL)
	resp := client.Post(t, "/echo", map[string]string{"hello": "world"})
	resp.AssertCreated(t)
	resp.AssertBodyContains(t, "hello")
}

func TestHTTPClient_AssertBadRequest(t *testing.T) {
	srv := newEchoServer()
	defer srv.Close()

	client := httpx.NewServiceClient(srv.URL)
	resp := client.Get(t, "/fail")
	resp.AssertBadRequest(t)
	resp.AssertErrorCode(t, 400)
}

func TestHTTPClient_DoRequest(t *testing.T) {
	srv := newEchoServer()
	defer srv.Close()

	client := httpx.NewServiceClient(srv.URL)
	resp := client.DoRequest(t, http.MethodGet, "/ok", nil, nil)
	resp.AssertOK(t)
}

func TestHTTPResponse_String(t *testing.T) {
	srv := newEchoServer()
	defer srv.Close()

	client := httpx.NewServiceClient(srv.URL)
	resp := client.Get(t, "/ok")
	if resp.String() == "" {
		t.Fatal("expected non-empty body string")
	}
}
