#!/usr/bin/env bash
# sisyphus-clone-repos.sh — runner pod 内统一 clone source repo 的 helper
#
# sisyphus 提供机械工具，agent 调用，保证多仓 clone 行为一致：
#   - 统一 target 路径 /workspace/source/<repo-basename>/
#   - 统一 auth（$GH_TOKEN）
#   - shallow clone + 自动 unshallow（dev-agent 后续切分支需要 full history）
#   - 已存在 repo 不重 clone，只 fetch + reset --hard origin/<base_branch>
#
# 用法：
#   sisyphus-clone-repos.sh [--base <branch>] [--base-for <repo> <branch>] \
#     <owner1>/<repo1> [<owner2>/<repo2> ...]
#
# 参数：
#   --base <branch>           所有仓的默认 base branch（覆盖 origin/HEAD）
#   --base-for <repo> <branch> 指定 repo 的 base branch（覆盖 --base）
#
# 环境变量：
#   GH_TOKEN  GitHub token，用于 https x-access-token auth（必需）
#
# 退出码：0=全成功，非 0=任一仓 clone/update/校验 失败

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: sisyphus-clone-repos.sh [--base <branch>] [--base-for <repo> <branch>] \
  <owner1>/<repo1> [<owner2>/<repo2> ...]

Clones (or updates) source repos to /workspace/source/<repo-basename>/.
Requires $GH_TOKEN.

Options:
  --base <branch>            Default base branch for all repos
  --base-for <repo> <branch> Per-repo base branch override

Examples:
  sisyphus-clone-repos.sh phona/sisyphus
  sisyphus-clone-repos.sh --base develop phona/sisyphus
  sisyphus-clone-repos.sh --base develop --base-for ttpos-flutter feat/develop-hwt \
    phona/sisyphus phona/ttpos-flutter
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

# 解析 --base / --base-for 参数
DEFAULT_BASE=""
declare -A REPO_BASE_MAP

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)
      shift
      if [[ $# -eq 0 ]]; then usage; fi
      DEFAULT_BASE="$1"
      shift
      ;;
    --base-for)
      shift
      if [[ $# -lt 2 ]]; then usage; fi
      REPO_BASE_MAP["$1"]="$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "=== FAIL clone: unknown option $1 ===" >&2
      usage
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -eq 0 ]]; then
  usage
fi

# 辅助：给定 repo basename，返回应使用的 base branch
_resolve_base() {
  local basename="$1"
  if [[ -n "${REPO_BASE_MAP[$basename]:-}" ]]; then
    echo "${REPO_BASE_MAP[$basename]}"
  elif [[ -n "$DEFAULT_BASE" ]]; then
    echo "$DEFAULT_BASE"
  else
    echo ""
  fi
}

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
  base_branch=$(_resolve_base "$basename")

  # ── base branch 存在性校验（早 fail）──────────────────────────────────────
  if [[ -n "$base_branch" ]]; then
    echo "[sisyphus-clone-repos] validating base branch '$base_branch' for $repo_spec ..." >&2
    if ! git ls-remote --heads "$url" "$base_branch" | grep -q "refs/heads/$base_branch"; then
      echo "=== FAIL clone: base branch '$base_branch' not found on origin for $repo_spec ===" >&2
      FAILED+=("$repo_spec")
      continue
    fi
  fi

  needs_clone=0

  if [[ -d "$target/.git" ]]; then
    # 检查是否是有效 git repo（.git 目录可能损坏）
    if ! git -C "$target" rev-parse --git-dir >/dev/null 2>&1; then
      echo "[sisyphus-clone-repos] $target/.git exists but is corrupt, re-cloning" >&2
      rm -rf "$target"
      needs_clone=1
    elif [[ -n "$(git -C "$target" status --porcelain 2>/dev/null)" ]]; then
      # working tree 不干净（前一个 REQ 的残留修改 / 脏数据）
      echo "[sisyphus-clone-repos] $target has dirty working tree, re-cloning" >&2
      rm -rf "$target"
      needs_clone=1
    fi
  elif [[ -e "$target" ]]; then
    # 目录存在但不是 git repo（PVC 残留的非仓库数据）
    echo "[sisyphus-clone-repos] $target exists but is not a git repo, re-cloning" >&2
    rm -rf "$target"
    needs_clone=1
  fi

  if [[ "$needs_clone" -eq 0 && -d "$target/.git" ]]; then
    echo "[sisyphus-clone-repos] updating existing $repo_spec at $target" >&2
    # update 时：fetch all + checkout base_branch（有显式 base 用 base，否则 origin/HEAD）
    reset_ref="origin/${base_branch:-HEAD}"
    if ! ( cd "$target" \
           && git remote set-url origin "$url" \
           && git fetch --all --prune \
           && git checkout -B "${base_branch:-HEAD}" "$reset_ref" \
           && git reset --hard "$reset_ref" ); then
      echo "=== FAIL clone: $repo_spec (update failed) ===" >&2
      FAILED+=("$repo_spec")
      continue
    fi
  else
    echo "[sisyphus-clone-repos] cloning $repo_spec to $target" >&2
    clone_args="--depth=1"
    if [[ -n "$base_branch" ]]; then
      clone_args="$clone_args --branch $base_branch"
    fi
    # shellcheck disable=SC2086
    if ! git clone $clone_args "$url" "$target"; then
      echo "=== FAIL clone: $repo_spec (clone failed) ===" >&2
      FAILED+=("$repo_spec")
      continue
    fi
    # --depth=1 默认 refspec 只追默认分支（+refs/heads/master:refs/remotes/origin/master）。
    # 后续 spec_lint / dev_cross_check / staging_test checker 要 git checkout origin/feat/REQ-x，
    # 必须把 refspec 改宽 + fetch 全部，否则本地 refs/remotes/origin/feat/* 不会出现，checker
    # 必 fail（REQ-final2-1776948458 实证）。
    if ! ( cd "$target" \
           && git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*' \
           && git fetch --all --tags ); then
      echo "=== FAIL clone: $repo_spec (broaden refspec failed) ===" >&2
      FAILED+=("$repo_spec")
      continue
    fi
    # dev-agent 后续要 base 切 feat/REQ-x 分支，也要 full history
    if ! ( cd "$target" && git fetch --unshallow 2>/dev/null || true ); then
      echo "[sisyphus-clone-repos] WARN: unshallow failed for $repo_spec (continuing)" >&2
    fi
  fi

  # 验证关键项目文件存在（不阻断，仅 WARN——裸仓库或特殊项目允许通过）
  has_project_file=0
  for marker in Makefile pubspec.yaml package.json go.mod Cargo.toml pyproject.toml setup.py; do
    if [[ -f "$target/$marker" ]]; then
      has_project_file=1
      break
    fi
  done
  if [[ "$has_project_file" -eq 0 ]]; then
    echo "[sisyphus-clone-repos] WARN: $target missing expected project files (Makefile, pubspec.yaml, package.json, go.mod, Cargo.toml, pyproject.toml, setup.py)" >&2
  fi

  CLONED+=("$repo_spec")
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "=== FAIL clone: ${FAILED[*]} ===" >&2
  exit 1
fi

echo "=== CLONED: ${CLONED[*]} ==="
exit 0
