#!/usr/bin/env bash
# sisyphus-clone-repos.sh — runner pod 内统一 clone source repo 的 helper
#
# sisyphus 提供机械工具，agent 调用，保证多仓 clone 行为一致：
#   - 统一 target 路径 /workspace/source/<repo-basename>/
#   - 统一 auth（$GH_TOKEN）
#   - shallow clone + 自动 unshallow（dev-agent 后续切分支需要 full history）
#   - 已存在 repo 不重 clone，只 fetch + reset --hard origin/main
#
# 用法：
#   sisyphus-clone-repos.sh <owner1>/<repo1> [<owner2>/<repo2> ...]
#
# 环境变量：
#   GH_TOKEN  GitHub token，用于 https x-access-token auth（必需）
#
# 退出码：0=全成功，非 0=任一仓 clone/update 失败

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: sisyphus-clone-repos.sh <owner1>/<repo1> [<owner2>/<repo2> ...]

Clones (or updates) source repos to /workspace/source/<repo-basename>/.
Requires $GH_TOKEN.

Examples:
  sisyphus-clone-repos.sh phona/sisyphus
  sisyphus-clone-repos.sh phona/sisyphus phona/ubox-crosser
EOF
  exit 1
}

if [[ $# -eq 0 ]]; then
  usage
fi

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "=== FAIL clone: \$GH_TOKEN not set ===" >&2
  exit 2
fi

WORKSPACE_ROOT="${SISYPHUS_SOURCE_ROOT:-/workspace/source}"
mkdir -p "$WORKSPACE_ROOT"

CLONED=()
FAILED=()

for repo_spec in "$@"; do
  if [[ "$repo_spec" != */* ]]; then
    echo "=== FAIL clone: $repo_spec (expected <owner>/<repo> form) ===" >&2
    FAILED+=("$repo_spec")
    continue
  fi

  basename="${repo_spec##*/}"
  basename="${basename%.git}"
  target="$WORKSPACE_ROOT/$basename"
  url="https://x-access-token:${GH_TOKEN}@github.com/${repo_spec}.git"

  if [[ -d "$target/.git" ]]; then
    echo "[sisyphus-clone-repos] updating existing $repo_spec at $target" >&2
    if ! ( cd "$target" \
           && git remote set-url origin "$url" \
           && git fetch --all --prune \
           && git checkout main \
           && git reset --hard origin/main ); then
      echo "=== FAIL clone: $repo_spec (update failed) ===" >&2
      FAILED+=("$repo_spec")
      continue
    fi
  else
    echo "[sisyphus-clone-repos] cloning $repo_spec to $target" >&2
    if ! git clone --depth=1 "$url" "$target"; then
      echo "=== FAIL clone: $repo_spec (clone failed) ===" >&2
      FAILED+=("$repo_spec")
      continue
    fi
    # dev-agent 后续要 base 切 feat/REQ-x 分支，需要 full history
    if ! ( cd "$target" && git fetch --unshallow 2>/dev/null || true ); then
      echo "[sisyphus-clone-repos] WARN: unshallow failed for $repo_spec (continuing)" >&2
    fi
  fi

  CLONED+=("$repo_spec")
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "=== FAIL clone: ${FAILED[*]} ===" >&2
  exit 1
fi

echo "=== CLONED: ${CLONED[*]} ==="
exit 0
