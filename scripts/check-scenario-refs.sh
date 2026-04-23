#!/usr/bin/env bash
# Scenario ID 完整性 linter
#
# 扫所有 openspec/changes/*/tasks.md 和 docs/req/*/reports/*.md 里的 [XXX-S<N>] 引用，
# 必须能在 specs/*.md 的 ## Scenario: XXX-S<N> heading 找到。
#
# 用法：
#   ./scripts/check-scenario-refs.sh [--specs-search-path <path>]... [repo_root]
#
# 参数：
#   repo_root              当前 repo 根（默认 .）
#   --specs-search-path P  额外的 specs 搜索路径，可重复。
#                          会把 P/*/openspec/specs 加进 scenario ID 定义集合。
#                          用于跨仓 scenario 引用（consumer 仓 task 引用 producer 仓
#                          定义的 scenario）。
#
# 退出码 0=全通过，1=有未解析的 scenario ID。

set -euo pipefail

SEARCH_PATHS=()
ROOT="."

while [[ $# -gt 0 ]]; do
  case "$1" in
    --specs-search-path)
      if [[ $# -lt 2 ]]; then
        echo "FAIL: --specs-search-path requires an argument" >&2
        exit 2
      fi
      SEARCH_PATHS+=("$2")
      shift 2
      ;;
    --specs-search-path=*)
      SEARCH_PATHS+=("${1#--specs-search-path=}")
      shift
      ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    --)
      shift
      ROOT="${1:-.}"
      shift || true
      ;;
    -*)
      echo "FAIL: unknown flag $1" >&2
      exit 2
      ;;
    *)
      ROOT="$1"
      shift
      ;;
  esac
done

cd "$ROOT"

REF_PATTERN='\[([A-Z][A-Z0-9-]+-S[0-9]+)\]'
# Bash [[ =~ ]] uses POSIX ERE — no \s, no \b. Use [[:space:]] and an explicit boundary.
HEADING_PATTERN='^##+ Scenario:[[:space:]]+([A-Z][A-Z0-9-]+-S[0-9]+)([[:space:]]|$|[^A-Z0-9-])'

# 收集所有 specs 里定义的 scenario ID（当前 repo + 所有 search-path 下的 sibling repo）
# NB: 必须显式初始化为空 ()，否则 set -u + 空 ${#DEFINED[@]} 会报 unbound variable（bash 5.2）
declare -A DEFINED=()

scan_spec_file() {
  local file="$1"
  local line
  while IFS= read -r line; do
    if [[ "$line" =~ $HEADING_PATTERN ]]; then
      DEFINED["${BASH_REMATCH[1]}"]="$file"
    fi
  done < "$file"
}

while IFS= read -r file; do
  scan_spec_file "$file"
done < <(find openspec/specs openspec/changes/*/specs 2>/dev/null -name '*.md' 2>/dev/null || true)

for sp in "${SEARCH_PATHS[@]}"; do
  # P/*/openspec/specs/*.md  — 同时匹配每个 sibling repo 的 in-progress changes
  while IFS= read -r file; do
    scan_spec_file "$file"
  done < <(find "$sp"/*/openspec/specs "$sp"/*/openspec/changes/*/specs 2>/dev/null -name '*.md' 2>/dev/null || true)
done

if [[ ${#DEFINED[@]} -eq 0 ]]; then
  echo "WARN: 未在 openspec/specs 或 openspec/changes/*/specs 下找到任何 Scenario 定义"
fi

# 扫所有引用位置，查找每一个 [XXX-S<N>]
FAILED=0
while IFS= read -r file; do
  # grep 出本文件所有 scenario 引用
  while IFS=: read -r lineno content; do
    # 提取所有 [XXX-S<N>] 匹配
    while read -r sid; do
      # NB: guard on DEFINED being populated — bash 4.x + set -u throws on subscript
      # into an empty associative array.
      if [[ ${#DEFINED[@]} -eq 0 || -z "${DEFINED[$sid]:-}" ]]; then
        echo "FAIL: $file:$lineno references undefined scenario [$sid]"
        FAILED=1
      fi
    done < <(echo "$content" | grep -oE "$REF_PATTERN" | grep -oE '[A-Z][A-Z0-9-]+-S[0-9]+')
  done < <(grep -n -E "$REF_PATTERN" "$file" || true)
done < <(find openspec/changes docs/req 2>/dev/null -name '*.md' 2>/dev/null || true)

if [[ $FAILED -eq 0 ]]; then
  echo "OK: 所有 scenario 引用都在 specs 中找到（共 ${#DEFINED[@]} 个定义）"
  exit 0
else
  echo "FAIL: 有未解析的 scenario 引用，请修正后重试"
  exit 1
fi
