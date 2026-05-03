// Package httpx provides an HTTP test client with fluent assertions.
//
// Merged from two call sites:
//   - WithToken / WithHeaders builder pattern
//   - NewServiceClient factory for multi-service setups
package httpx

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

// Option configures an HTTPClient at construction time.
type Option func(*HTTPClient)

// WithToken sets the Bearer authorization token.
func WithToken(token string) Option {
	return func(c *HTTPClient) { c.token = token }
}

// WithHeaders sets persistent headers applied to every request.
func WithHeaders(headers map[string]string) Option {
	return func(c *HTTPClient) { c.headers = headers }
}

// HTTPClient wraps http.Client with convenience methods for integration testing.
type HTTPClient struct {
	client  *http.Client
	baseURL string
	token   string
	headers map[string]string
}

// NewServiceClient creates an HTTPClient for the given service URL.
// Business repos wrap this to create named clients:
//
//	func NewErpClient() *httpx.HTTPClient { return httpx.NewServiceClient(env.GetEnv("ERP_URL", "http://localhost:14021")) }
func NewServiceClient(serviceURL string, opts ...Option) *HTTPClient {
	c := &HTTPClient{
		client:  &http.Client{Timeout: 30 * time.Second},
		baseURL: serviceURL,
	}
	for _, o := range opts {
		o(c)
	}
	return c
}

// WithToken sets the Bearer token for subsequent requests (builder form).
func (c *HTTPClient) WithToken(token string) *HTTPClient {
	c.token = token
	return c
}

// WithHeaders sets persistent headers for subsequent requests (builder form).
func (c *HTTPClient) WithHeaders(headers map[string]string) *HTTPClient {
	c.headers = headers
	return c
}

// Get performs a GET request. Per-request headers may be passed as optional maps.
func (c *HTTPClient) Get(tb testing.TB, path string, headers ...map[string]string) *HTTPResponse {
	tb.Helper()
	req, err := http.NewRequest(http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		tb.Fatalf("failed to create GET request: %v", err)
	}
	return c.doRequest(tb, req, headers...)
}

// Post performs a POST request with an optional JSON body.
func (c *HTTPClient) Post(tb testing.TB, path string, body any, headers ...map[string]string) *HTTPResponse {
	tb.Helper()
	req, err := http.NewRequest(http.MethodPost, c.baseURL+path, jsonReader(tb, body))
	if err != nil {
		tb.Fatalf("failed to create POST request: %v", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	return c.doRequest(tb, req, headers...)
}

// Delete performs a DELETE request with an optional JSON body.
func (c *HTTPClient) Delete(tb testing.TB, path string, body any, headers ...map[string]string) *HTTPResponse {
	tb.Helper()
	req, err := http.NewRequest(http.MethodDelete, c.baseURL+path, jsonReader(tb, body))
	if err != nil {
		tb.Fatalf("failed to create DELETE request: %v", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	return c.doRequest(tb, req, headers...)
}

// DoRequest performs a request with explicit method, path, optional JSON body, and extra headers.
func (c *HTTPClient) DoRequest(tb testing.TB, method, path string, body any, headers map[string]string) *HTTPResponse {
	tb.Helper()
	req, err := http.NewRequest(method, c.baseURL+path, jsonReader(tb, body))
	if err != nil {
		tb.Fatalf("failed to create %s request: %v", method, err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	return c.doRequest(tb, req, headers)
}

func (c *HTTPClient) doRequest(tb testing.TB, req *http.Request, extraHeaders ...map[string]string) *HTTPResponse {
	tb.Helper()

	if c.token != "" {
		req.Header.Set("Authorization", fmt.Sprintf("Bearer %s", c.token))
	}
	for key, value := range c.headers {
		req.Header.Set(key, value)
	}
	for _, h := range extraHeaders {
		for key, value := range h {
			req.Header.Set(key, value)
		}
	}

	resp, err := c.client.Do(req)
	if err != nil {
		tb.Fatalf("request failed: %v", err)
	}
	body, err := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err != nil {
		tb.Fatalf("failed to read response body: %v", err)
	}
	return &HTTPResponse{
		StatusCode: resp.StatusCode,
		Headers:    resp.Header,
		Body:       body,
	}
}

// jsonReader marshals v to JSON and returns a reader, or nil if v is nil.
func jsonReader(tb testing.TB, v any) io.Reader {
	if v == nil {
		return nil
	}
	b, err := json.Marshal(v)
	if err != nil {
		tb.Fatalf("failed to marshal request body: %v", err)
	}
	return bytes.NewReader(b)
}

// HTTPResponse wraps an HTTP response for testing.
type HTTPResponse struct {
	StatusCode int
	Headers    http.Header
	Body       []byte
}

// String returns the response body as a string.
func (r *HTTPResponse) String() string { return string(r.Body) }

// JSON unmarshals the response body into v.
func (r *HTTPResponse) JSON(tb testing.TB, v any) {
	tb.Helper()
	if err := json.Unmarshal(r.Body, v); err != nil {
		tb.Fatalf("failed to unmarshal response body: %v\nbody: %s", err, r.String())
	}
}

// AssertStatus asserts the expected HTTP status code.
func (r *HTTPResponse) AssertStatus(tb testing.TB, expected int) *HTTPResponse {
	tb.Helper()
	if r.StatusCode != expected {
		tb.Fatalf("expected status %d but got %d: %s", expected, r.StatusCode, r.String())
	}
	return r
}

// AssertOK asserts 200 OK.
func (r *HTTPResponse) AssertOK(tb testing.TB) *HTTPResponse {
	tb.Helper()
	return r.AssertStatus(tb, http.StatusOK)
}

// AssertCreated asserts 201 Created.
func (r *HTTPResponse) AssertCreated(tb testing.TB) *HTTPResponse {
	tb.Helper()
	return r.AssertStatus(tb, http.StatusCreated)
}

// AssertBadRequest asserts 400 Bad Request.
func (r *HTTPResponse) AssertBadRequest(tb testing.TB) *HTTPResponse {
	tb.Helper()
	return r.AssertStatus(tb, http.StatusBadRequest)
}

// AssertUnauthorized asserts 401 Unauthorized.
func (r *HTTPResponse) AssertUnauthorized(tb testing.TB) *HTTPResponse {
	tb.Helper()
	return r.AssertStatus(tb, http.StatusUnauthorized)
}

// AssertNotFound asserts 404 Not Found.
func (r *HTTPResponse) AssertNotFound(tb testing.TB) *HTTPResponse {
	tb.Helper()
	return r.AssertStatus(tb, http.StatusNotFound)
}

// AssertInternalError asserts 500 Internal Server Error.
func (r *HTTPResponse) AssertInternalError(tb testing.TB) *HTTPResponse {
	tb.Helper()
	return r.AssertStatus(tb, http.StatusInternalServerError)
}

// AssertBodyContains asserts the response body contains the given substring.
func (r *HTTPResponse) AssertBodyContains(tb testing.TB, sub string) *HTTPResponse {
	tb.Helper()
	if !strings.Contains(r.String(), sub) {
		tb.Fatalf("expected body to contain %q, got: %s", sub, r.String())
	}
	return r
}

// APIResponse is the standard API envelope: { "code": 0, "message": "...", "data": ... }.
type APIResponse struct {
	Code    int             `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

// ParseAPIResponse unmarshals the body as an APIResponse.
func (r *HTTPResponse) ParseAPIResponse(tb testing.TB) *APIResponse {
	tb.Helper()
	var resp APIResponse
	r.JSON(tb, &resp)
	return &resp
}

// AssertSuccess asserts the API envelope has code == 0.
func (r *HTTPResponse) AssertSuccess(tb testing.TB) *HTTPResponse {
	tb.Helper()
	if resp := r.ParseAPIResponse(tb); resp.Code != 0 {
		tb.Fatalf("expected success code 0 but got %d: %s", resp.Code, resp.Message)
	}
	return r
}

// AssertErrorCode asserts the API envelope has the expected non-zero code.
func (r *HTTPResponse) AssertErrorCode(tb testing.TB, expectedCode int) *HTTPResponse {
	tb.Helper()
	if resp := r.ParseAPIResponse(tb); resp.Code != expectedCode {
		tb.Fatalf("expected error code %d but got %d: %s", expectedCode, resp.Code, resp.Message)
	}
	return r
}
