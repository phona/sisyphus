// Package env provides typed environment variable helpers for test fixtures.
package env

import (
	"os"
	"strconv"
)

// GetEnv returns the value of the environment variable key, or defaultValue if not set or empty.
func GetEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}

// GetEnvInt returns the integer value of the environment variable key,
// or defaultValue if not set or not a valid integer.
func GetEnvInt(key string, defaultValue int) int {
	value := os.Getenv(key)
	if value == "" {
		return defaultValue
	}
	parsed, err := strconv.Atoi(value)
	if err != nil {
		return defaultValue
	}
	return parsed
}
