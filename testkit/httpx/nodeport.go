package httpx

import "fmt"

// NodePortURL constructs an HTTP URL for a Kubernetes NodePort service.
// Use when the runner must reach a service exposed via NodePort from outside
// the cluster (or from a node that differs from the service's pod node).
func NodePortURL(nodeIP string, nodePort int) string {
	return fmt.Sprintf("http://%s:%d", nodeIP, nodePort)
}
