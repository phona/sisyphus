#!/usr/bin/env bash
# Smoke test for sisyphus-clone-repos.sh
#
# 不真去 GitHub clone（CI 没 token）。只验证：
#   - 无参数 → exit 1，stderr 含 Usage
#   - $GH_TOKEN 缺失 → exit 2，stderr 含 GH_TOKEN
#   - bad format → exit 1，stderr 含 expected <owner>/<repo>
#   - basename 推导：phona/foo → /workspace/source/foo（用 SISYPHUS_SOURCE_ROOT 覆写）
#   - basename 去 .git：phona/foo.git → .../foo
#
# 用法：bash scripts/test_clone_repos.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HELPER="$SCRIPT_DIR/sisyphus-clone-repos.sh"

PASS=0
FAIL=0

assert() {
  local label="$1"
  local actual_exit="$2"
  local expected_exit="$3"
  local actual_stderr="$4"
  local expected_substr="$5"

  if [[ "$actual_exit" -ne "$expected_exit" ]]; then
    echo "FAIL [$label]: exit=$actual_exit expected=$expected_exit"
    echo "  stderr: $actual_stderr"
    FAIL=$((FAIL + 1))
    return
  fi
  if [[ -n "$expected_substr" && "$actual_stderr" != *"$expected_substr"* ]]; then
    echo "FAIL [$label]: stderr missing '$expected_substr'"
    echo "  stderr: $actual_stderr"
    FAIL=$((FAIL + 1))
    return
  fi
  echo "OK [$label]"
  PASS=$((PASS + 1))
}

# 1. no args → usage + exit 1
out=$(bash "$HELPER" 2>&1 >/dev/null) && rc=0 || rc=$?
assert "no args" "$rc" 1 "$out" "Usage:"

# 2. missing GH_TOKEN
out=$(env -u GH_TOKEN bash "$HELPER" phona/foo 2>&1 >/dev/null) && rc=0 || rc=$?
assert "missing token" "$rc" 2 "$out" "GH_TOKEN"

# 3. bad format
TMP_ROOT=$(mktemp -d)
out=$(GH_TOKEN=dummy SISYPHUS_SOURCE_ROOT="$TMP_ROOT" bash "$HELPER" badformat 2>&1 >/dev/null) && rc=0 || rc=$?
assert "bad format" "$rc" 1 "$out" "expected <owner>/<repo>"
rm -rf "$TMP_ROOT"

# 4. basename derivation — clone will fail (dummy token), but we can verify the
#    target dir naming via the diagnostic line on stderr.
TMP_ROOT=$(mktemp -d)
out=$(GH_TOKEN=dummy SISYPHUS_SOURCE_ROOT="$TMP_ROOT" bash "$HELPER" phona/foo 2>&1 >/dev/null) && rc=0 || rc=$?
expected_target="$TMP_ROOT/foo"
if [[ "$out" == *"cloning phona/foo to $expected_target"* ]]; then
  echo "OK [basename phona/foo → foo]"
  PASS=$((PASS + 1))
else
  echo "FAIL [basename phona/foo → foo]: stderr did not mention $expected_target"
  echo "  stderr: $out"
  FAIL=$((FAIL + 1))
fi
rm -rf "$TMP_ROOT"

# 5. basename with .git suffix
TMP_ROOT=$(mktemp -d)
out=$(GH_TOKEN=dummy SISYPHUS_SOURCE_ROOT="$TMP_ROOT" bash "$HELPER" phona/foo.git 2>&1 >/dev/null) && rc=0 || rc=$?
expected_target="$TMP_ROOT/foo"
if [[ "$out" == *"cloning phona/foo.git to $expected_target"* ]]; then
  echo "OK [basename phona/foo.git → foo]"
  PASS=$((PASS + 1))
else
  echo "FAIL [basename phona/foo.git → foo]: stderr did not mention $expected_target"
  echo "  stderr: $out"
  FAIL=$((FAIL + 1))
fi
rm -rf "$TMP_ROOT"

# 6. --base-for accepts <owner>/<repo> form and normalizes to basename
#    Verifies the bug fix: helm `env.default_base_branches: {phona/sisyphus: main}`
#    was passed verbatim as --base-for phona/sisyphus main; script stored under
#    "phona/sisyphus" key but looked up "sisyphus" basename → miss → fell back
#    to --base develop → fail. After fix the slug key is normalized to basename
#    on storage so per-repo override hits.
TMP_ROOT=$(mktemp -d)
out=$(GH_TOKEN=dummy SISYPHUS_SOURCE_ROOT="$TMP_ROOT" bash "$HELPER" \
  --base develop --base-for phona/sisyphus main phona/sisyphus 2>&1 >/dev/null) && rc=0 || rc=$?
# clone will fail (dummy token); we assert the validating diagnostic mentions
# 'main', not 'develop' — proving --base-for owner/repo overrode --base.
if [[ "$out" == *"validating base branch 'main' for phona/sisyphus"* ]]; then
  echo "OK [--base-for owner/repo normalizes to basename]"
  PASS=$((PASS + 1))
else
  echo "FAIL [--base-for owner/repo normalizes to basename]: expected validating 'main', got:"
  echo "  stderr: $out"
  FAIL=$((FAIL + 1))
fi
rm -rf "$TMP_ROOT"

# 7. --base-for basename form still works (backward compat)
TMP_ROOT=$(mktemp -d)
out=$(GH_TOKEN=dummy SISYPHUS_SOURCE_ROOT="$TMP_ROOT" bash "$HELPER" \
  --base develop --base-for sisyphus main phona/sisyphus 2>&1 >/dev/null) && rc=0 || rc=$?
if [[ "$out" == *"validating base branch 'main' for phona/sisyphus"* ]]; then
  echo "OK [--base-for basename form still works]"
  PASS=$((PASS + 1))
else
  echo "FAIL [--base-for basename form still works]: expected validating 'main', got:"
  echo "  stderr: $out"
  FAIL=$((FAIL + 1))
fi
rm -rf "$TMP_ROOT"

echo "---"
echo "PASS=$PASS FAIL=$FAIL"
[[ $FAIL -eq 0 ]]
