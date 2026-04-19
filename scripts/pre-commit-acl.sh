#!/usr/bin/env bash
# 按 AGENT_ROLE 强制 file-path 级 ACL
#
# 装为 .git/hooks/pre-commit 或被主 pre-commit 调用。
# AGENT_ROLE 来自 agent worktree 的环境变量（BKD 启 agent 时注入）。
# 未设置则 skip，便于本地人工编辑。
#
# 当前支持的角色及其禁写范围：
#   analyze-agent       : —                              （全权）
#   dev-spec-agent      : openspec/specs/**              contract.spec.yaml
#                         tests/**                       internal/**, cmd/**
#   contract-test-agent : openspec/specs/**              contract.spec.yaml
#                         tests/acceptance/**            tests/ui/**, tests/mobile/**
#                         internal/**, cmd/**            migrations/**
#   ui-test-agent       : openspec/specs/**              contract.spec.yaml
#                         tests/contract/**              tests/acceptance/**
#                         internal/**, cmd/**            migrations/**
#   migration-agent     : openspec/specs/**              contract.spec.yaml
#                         tests/**                       internal/**, cmd/**
#   accept-test-agent   : openspec/specs/**              contract.spec.yaml
#                         tests/contract/**              tests/ui/**, tests/mobile/**
#                         internal/**, cmd/**            migrations/**
#   dev-agent           : openspec/specs/**              openspec/changes/*/specs/**
#                         tests/contract/**              tests/acceptance/**
#                         tests/ui/**, tests/mobile/**   migrations/**
#                         openspec/changes/*/proposal.md
#                         openspec/changes/*/design.md
#                         openspec/changes/*/contract.spec.yaml
#                         reports/**
#   bugfix-agent        : 同 dev-agent（CODE BUG 阶段）
#   test-bugfix-agent   : 只许改 tests/**（contract / ui / acceptance / mobile）
#                         内部代码、migrations、openspec 都禁
#   verify-agent        : 所有文件（只读模式，不应 commit）
#   qa-agent            : tests/** internal/** cmd/** migrations/** openspec/specs/**
#                         openspec/changes/*/（除 reports/qa.md）

set -euo pipefail

AGENT_ROLE="${AGENT_ROLE:-}"

if [[ -z "$AGENT_ROLE" ]]; then
  echo "SKIP: AGENT_ROLE 未设置，本次不做 role ACL 校验"
  exit 0
fi

CHANGED=$(git diff --cached --name-only)
[[ -z "$CHANGED" ]] && { echo "OK: 无 staged 改动"; exit 0; }

FAILED=0

# 辅助：若 changed 里有匹配 pattern 的文件则 fail
forbid() {
  local role="$1" pattern="$2" reason="$3"
  if [[ "$AGENT_ROLE" == "$role" ]]; then
    local hit
    hit=$(echo "$CHANGED" | grep -E "$pattern" || true)
    if [[ -n "$hit" ]]; then
      echo "FAIL: [$role] 禁改 $pattern ($reason):"
      echo "$hit" | sed 's/^/  /'
      FAILED=1
    fi
  fi
}

# 辅助：若 AGENT_ROLE 不在空格分隔的 allowed_roles 列表里则 fail
forbid_others() {
  local allowed_roles="$1" pattern="$2" reason="$3"
  local matched=0 role
  for role in $allowed_roles; do
    if [[ "$AGENT_ROLE" == "$role" ]]; then
      matched=1
      break
    fi
  done
  if [[ $matched -eq 0 ]]; then
    local hit
    hit=$(echo "$CHANGED" | grep -E "$pattern" || true)
    if [[ -n "$hit" ]]; then
      echo "FAIL: [$AGENT_ROLE] 禁改 $pattern ($reason，仅 $allowed_roles 可)"
      echo "$hit" | sed 's/^/  /'
      FAILED=1
    fi
  fi
}

# ---- 权威 specs：仅 analyze / apply 流程可写 ----
forbid_others "analyze-agent" \
  '^openspec/specs/' \
  '长期权威 spec'

# ---- change 目录下的 spec-delta：仅 analyze 可写 ----
forbid_others "analyze-agent" \
  '^openspec/changes/[^/]+/specs/' \
  'spec-delta'

# ---- contract.spec.yaml：仅 analyze 可写 ----
forbid_others "analyze-agent" \
  '^openspec/changes/[^/]+/contract\.spec\.yaml$' \
  'API 契约'

# ---- proposal.md / design.md：仅 analyze 可写 ----
forbid_others "analyze-agent" \
  '^openspec/changes/[^/]+/(proposal|design)\.md$' \
  '需求 / 设计文档'

# ---- tests/contract/：仅 contract-test-agent 可写 ----
forbid_others "contract-test-agent" \
  '^tests/contract/' \
  '契约测试 LOCKED'

# ---- tests/acceptance/：accept-test-agent 或 test-bugfix-agent 可写 ----
forbid_others "accept-test-agent test-bugfix-agent" \
  '^tests/acceptance/' \
  '验收测试 LOCKED'

# ---- tests/ui/, tests/mobile/：ui-test-agent 或 test-bugfix-agent 可写 ----
forbid_others "ui-test-agent test-bugfix-agent" \
  '^tests/(ui|mobile)/' \
  'UI/移动端测试 LOCKED'

# ---- tests/contract/：contract-test-agent 或 test-bugfix-agent 可写（补之前遗漏）----
# （上面已有 contract-test-agent，这里不重复，但要让 test-bugfix-agent 也能写）
# 实际通过上面 ^tests/contract/ 的规则已处理

# ---- migrations/*.sql, *.md：仅 migration-agent 可写 ----
forbid_others "migration-agent" \
  '^migrations/' \
  'DB migration 脚本'

# ---- 业务代码：禁止 Test / Migration / Test Bug Fix 阶段修改 ----
forbid "contract-test-agent" \
  '^(internal|cmd)/' \
  '契约测试阶段只许写测试'

forbid "ui-test-agent" \
  '^(internal|cmd)/' \
  'UI 测试阶段只许写测试'

forbid "accept-test-agent" \
  '^(internal|cmd)/' \
  '验收测试阶段只许写测试'

forbid "migration-agent" \
  '^(internal|cmd)/' \
  'Migration 阶段只许写 SQL + plan'

forbid "test-bugfix-agent" \
  '^(internal|cmd)/' \
  'Test Bug Fix 阶段禁改业务代码（diagnosis 诊断为 TEST BUG 时才启用此 agent）'

forbid "test-bugfix-agent" \
  '^migrations/' \
  'Test Bug Fix 阶段禁改 migration'

# ---- reports/qa.md：仅 qa-agent 可写 ----
forbid_others "qa-agent" \
  '^openspec/changes/[^/]+/reports/qa\.md$' \
  '验收签收文档'

# ---- verify-agent 不该 commit 任何文件 ----
if [[ "$AGENT_ROLE" == "verify-agent" ]]; then
  echo "FAIL: [verify-agent] 不应 commit 任何文件（只跑测试写 title）"
  echo "$CHANGED" | sed 's/^/  /'
  FAILED=1
fi

if [[ $FAILED -eq 0 ]]; then
  echo "OK: [$AGENT_ROLE] ACL 通过"
  exit 0
else
  echo ""
  echo "如需修改这些文件，请确认：(1) 你的阶段职责是否正确；(2) 是否应由别的 agent 干"
  exit 1
fi
