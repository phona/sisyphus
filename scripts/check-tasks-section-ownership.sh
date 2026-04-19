#!/usr/bin/env bash
# tasks.md section ownership linter
#
# 解析 tasks.md 里的 "## Stage: <name> (owner: <agent-role>)" section heading，
# 对本次 commit 里 tasks.md 的 diff 行校验：
#   改动的 checkbox 行必须落在 owner 与 $AGENT_ROLE 一致的 section 内。
#
# 用法：
#   AGENT_ROLE=dev-agent ./scripts/check-tasks-section-ownership.sh openspec/changes/REQ-06/tasks.md
#
# 为便于本地试跑，若 AGENT_ROLE 未设置则 skip 校验（pre-commit 在 agent worktree 里会自动带环境变量）。

set -euo pipefail

FILE="${1:?需传入 tasks.md 路径}"
AGENT_ROLE="${AGENT_ROLE:-}"

if [[ -z "$AGENT_ROLE" ]]; then
  echo "SKIP: AGENT_ROLE 未设置，本次不做 section ownership 校验"
  exit 0
fi

if [[ ! -f "$FILE" ]]; then
  echo "FAIL: $FILE 不存在"
  exit 1
fi

# 拿改动的行号（git diff --cached，staged 的 diff）
CHANGED_LINES=$(git diff --cached --unified=0 -- "$FILE" \
  | awk '/^@@/{
      # @@ -a,b +c,d @@
      split($3, hunk, ",");
      start = substr(hunk[1], 2);
      len = (length(hunk) > 1) ? hunk[2] : 1;
      for (i=0; i<len; i++) print start + i
    }' || true)

if [[ -z "$CHANGED_LINES" ]]; then
  echo "OK: $FILE 本次无 staged 改动"
  exit 0
fi

# 扫全文件，建立 "行号 -> section owner" 映射
declare -A LINE_OWNER
current_owner=""
lineno=0
while IFS= read -r line; do
  lineno=$((lineno + 1))
  if [[ "$line" =~ ^##+\ Stage:.*\(owner:\ *([a-z0-9_-]+)\ *\) ]]; then
    current_owner="${BASH_REMATCH[1]}"
  fi
  LINE_OWNER[$lineno]="$current_owner"
done < "$FILE"

FAILED=0
for ln in $CHANGED_LINES; do
  # 只校验 checkbox 改动行（`- [ ]` 或 `- [x]`）
  content=$(sed -n "${ln}p" "$FILE" 2>/dev/null || true)
  if [[ -n "$content" ]] && [[ "$content" =~ ^-\ \[[\ xX]\] ]]; then
    owner="${LINE_OWNER[$ln]:-<none>}"
    if [[ "$owner" != "$AGENT_ROLE" ]]; then
      echo "FAIL: $FILE:$ln 属于 section owner=$owner，但当前 AGENT_ROLE=$AGENT_ROLE"
      echo "  content: $content"
      FAILED=1
    fi
  fi
done

if [[ $FAILED -eq 0 ]]; then
  echo "OK: $FILE 所有 checkbox 改动都在 $AGENT_ROLE 的 section 内"
  exit 0
else
  exit 1
fi
