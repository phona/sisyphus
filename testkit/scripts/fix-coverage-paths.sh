#!/bin/bash
# Fix Go coverage paths for SonarQube compatibility.
# Go coverage uses module name (e.g. mymodule/app/...) but SonarQube
# expects filesystem paths (e.g. main/app/...). This script reads the module
# name from go.mod and transforms coverage files accordingly.
#
# Usage: ./fix-coverage-paths.sh <coverage-file> [go-mod-path]

set -euo pipefail

COVERAGE_FILE="${1:?Usage: fix-coverage-paths.sh <coverage-file> [go-mod-path]}"
GO_MOD="${2:-main/go.mod}"

if [ ! -f "$COVERAGE_FILE" ]; then
  echo "Coverage file not found: $COVERAGE_FILE"
  exit 0
fi

if [ ! -f "$GO_MOD" ]; then
  echo "go.mod not found: $GO_MOD, skipping path fix"
  exit 0
fi

MODULE=$(head -1 "$GO_MOD" | awk '{print $2}')

# Get the directory name containing go.mod (e.g. "main")
TARGET_DIR=$(dirname "$GO_MOD")

echo "Fixing coverage paths: ${MODULE}/ -> ${TARGET_DIR}/"
sed -i "s|${MODULE}/|${TARGET_DIR}/|g" "$COVERAGE_FILE"

# Fix the entry point path (coverage binary writes /app/main.go)
sed -i "s|/app/main.go|${TARGET_DIR}/main.go|g" "$COVERAGE_FILE"

echo "Done: $COVERAGE_FILE"
