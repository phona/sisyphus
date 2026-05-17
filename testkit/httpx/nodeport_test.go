package httpx_test

import (
	"fmt"
	"testing"

	"github.com/phona/sisyphus/testkit/httpx"
)

func TestNodePortURL_BC1(t *testing.T) {
	cases := []struct {
		nodeIP   string
		nodePort int
	}{
		{"10.0.0.1", 30080},
		{"192.168.1.100", 31000},
		{"127.0.0.1", 30000},
	}
	for _, tc := range cases {
		want := fmt.Sprintf("http://%s:%d", tc.nodeIP, tc.nodePort)
		got := httpx.NodePortURL(tc.nodeIP, tc.nodePort)
		if got != want {
			t.Errorf("NodePortURL(%q, %d) = %q, want %q", tc.nodeIP, tc.nodePort, got, want)
		}
	}
}
