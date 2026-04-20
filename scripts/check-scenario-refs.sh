#!/usr/bin/env bash
# Scenario ID 完整性 linter
#
# 扫所有 openspec/changes/*/tasks.md 和 docs/req/*/reports/*.md 里的 [XXX-S<N>] 引用，
# 必须能在对应 specs/*.md 的 ## Scenario: XXX-S<N> heading 找到。
#
# 用法：
#   ./scripts/check-scenario-refs.sh [repo_root]
# 默认扫当前目录。
# 退出码 0=全通过，1=有未解析的 scenario ID。

set -euo pipefail

ROOT="${1:-.}"
cd "$ROOT"

REF_PATTERN='\[([A-Z][A-Z0-9-]+-S[0-9]+)\]'
# Bash [[ =~ ]] uses POSIX ERE — no \s, no \b. Use [[:space:]] and an explicit boundary.
HEADING_PATTERN='^##+ Scenario:[[:space:]]+([A-Z][A-Z0-9-]+-S[0-9]+)([[:space:]]|$|[^A-Z0-9-])'

# 收集所有 specs 里定义的 scenario ID
declare -A DEFINED
while IFS= read -r file; do
  while IFS= read -r line; do
    if [[ "$line" =~ $HEADING_PATTERN ]]; then
      DEFINED["${BASH_REMATCH[1]}"]="$file"
    fi
  done < "$file"
done < <(find openspec/specs openspec/changes/*/specs 2>/dev/null -name '*.md' 2>/dev/null || true)

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
      if [[ -z "${DEFINED[$sid]:-}" ]]; then
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
