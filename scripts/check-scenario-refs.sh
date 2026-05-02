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
# --current-change <REQ>：把空壳 scenario 检测**只限**到 openspec/changes/<REQ>/specs/。
# 不传时保持原行为（全仓所有 changes/*/specs/* 都做空壳检测）。
# 引用解析（DEFINED 集合）始终全量收集，保跨 REQ scenario 引用能解析。
CURRENT_CHANGE=""

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
    --current-change)
      if [[ $# -lt 2 ]]; then
        echo "FAIL: --current-change requires an argument" >&2
        exit 2
      fi
      CURRENT_CHANGE="$2"
      shift 2
      ;;
    --current-change=*)
      CURRENT_CHANGE="${1#--current-change=}"
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

# 全局 FAIL 标志，必须在 scan_spec_file 调用前设为 0（覆盖引用扫描段的旧值）
FAILED=0

# Scenario heading 匹配（用于空壳检测，精确匹配 #### Scenario:）
SCENARIO_HEADING_PATTERN='^#{4}[[:space:]]+Scenario:[[:space:]]+([A-Z][A-Z0-9-]+-S[0-9]+)([[:space:]]|$|[^A-Z0-9-])'
# Step 检测正则（匹配 gherkin 和 bullet 两种格式，大小写不敏感）
_STEP_PATTERN='^[[:space:]]*(-[[:space:]]*\*\*)?[[:space:]]*([Gg][Ii][Vv][Ee][Nn]|[Ww][Hh][Ee][Nn]|[Tt][Hh][Ee][Nn]|[Aa][Nn][Dd]|[Bb][Uu][Tt])([^[:alnum:]_]|$)'

scan_spec_file() {
  local file="$1"
  local check_empty="${2:-1}"   # 0 = 只 collect DEFINED；1 = 同时检测空壳（默认，向后兼容）
  local line
  local in_scenario=0
  local scen_id=""
  local scen_has_step=0

  while IFS= read -r line; do
    # 检测新的 scenario heading
    if [[ "$line" =~ $SCENARIO_HEADING_PATTERN ]]; then
      # 检查上一个 scenario 是否有 step
      if [[ $check_empty -eq 1 && $in_scenario -eq 1 && $scen_has_step -eq 0 ]]; then
        echo "FAIL: $file scenario [$scen_id] has no GIVEN/WHEN/THEN steps"
        FAILED=1
      fi
      # 开始新 scenario
      in_scenario=1
      scen_id="${BASH_REMATCH[1]}"
      scen_has_step=0
      # 同时记录到 DEFINED（原有行为）
      DEFINED["$scen_id"]="$file"
      continue
    fi

    # 在 scenario 范围内检测 step 和结束条件
    if [[ $in_scenario -eq 1 ]]; then
      # 检测 step 行
      if [[ "$line" =~ $_STEP_PATTERN ]]; then
        scen_has_step=1
      fi

      # 检测是否遇到结束 scenario 的 heading
      # 结束条件：1-3个#号（更高级/同级Requirement）或4个#号但不是Scenario（同级非Scenario）
      local end_scenario=0
      if [[ "$line" =~ ^#{1,3}[[:space:]] ]]; then
        end_scenario=1
      elif [[ "$line" =~ ^#{4}[[:space:]] && ! "$line" =~ $SCENARIO_HEADING_PATTERN ]]; then
        end_scenario=1
      fi

      if [[ $end_scenario -eq 1 ]]; then
        if [[ $check_empty -eq 1 && $scen_has_step -eq 0 ]]; then
          echo "FAIL: $file scenario [$scen_id] has no GIVEN/WHEN/THEN steps"
          FAILED=1
        fi
        in_scenario=0
        scen_id=""
        scen_has_step=0
      fi
    fi
  done < "$file"

  # 文件结尾检查最后一个 scenario
  if [[ $check_empty -eq 1 && $in_scenario -eq 1 && $scen_has_step -eq 0 ]]; then
    echo "FAIL: $file scenario [$scen_id] has no GIVEN/WHEN/THEN steps"
    FAILED=1
  fi
}

# helper: 文件是否属于「当前 REQ」的 specs（用于决定是否做空壳检测）
file_in_current_change() {
  local file="$1"
  if [[ -z "$CURRENT_CHANGE" ]]; then
    # 没传 --current-change → 所有 changes 都算「当前」（向后兼容：全量空壳检测）
    return 0
  fi
  # 路径含 openspec/changes/<CURRENT_CHANGE>/  或  openspec/specs/（baseline 永远扫）
  case "$file" in
    *"openspec/specs/"*) return 0 ;;
    *"openspec/changes/$CURRENT_CHANGE/"*) return 0 ;;
    *) return 1 ;;
  esac
}

while IFS= read -r file; do
  # 当前仓 specs：只对「当前 REQ 的 specs」+ baseline 做空壳检测；
  # 别的 REQ（含已合 / 历史遗留）的空壳 scenario 不应卡当前 REQ 的 spec_lint。
  if file_in_current_change "$file"; then
    scan_spec_file "$file" 1
  else
    scan_spec_file "$file" 0
  fi
done < <(find openspec/specs openspec/changes/*/specs 2>/dev/null -name '*.md' 2>/dev/null || true)

for sp in "${SEARCH_PATHS[@]}"; do
  # P/*/openspec/specs/*.md  — 同时匹配每个 sibling repo 的 in-progress changes
  while IFS= read -r file; do
    if file_in_current_change "$file"; then
      scan_spec_file "$file" 1
    else
      scan_spec_file "$file" 0
    fi
  done < <(find "$sp"/*/openspec/specs "$sp"/*/openspec/changes/*/specs 2>/dev/null -name '*.md' 2>/dev/null || true)
done

if [[ ${#DEFINED[@]} -eq 0 ]]; then
  echo "WARN: 未在 openspec/specs 或 openspec/changes/*/specs 下找到任何 Scenario 定义"
fi

# 扫所有引用位置，查找每一个 [XXX-S<N>]
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
