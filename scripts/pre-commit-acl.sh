#!/usr/bin/env bash
# 按 AGENT_ROLE 强制 file-path 级 ACL
#
# 装为 .git/hooks/pre-commit 或被主 pre-commit 调用。
# AGENT_ROLE 来自 agent worktree 的环境变量（BKD 启 agent 时注入）。
# 未设置则 skip，便于本地人工编辑。
#
# M14d：并行 dev agent 额外收 DEV_TASK_SCOPE（`:` 分隔的 glob 列表），
# 限制当前 dev task 只能改自己声明的 scope。例如：
#   DEV_TASK_SCOPE="internal/auth/*:tests/unit/auth_test.go"
# DEV_TASK_SCOPE 留空则不做 scope 限制（单 dev 模式 / 兼容老流程）。
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
#   ci-runner-agent     : 所有文件（只跑 make ci-*，绝不 commit）
#   qa-agent            : 只允许 reports/qa.md，其它一律禁
#
# 配套校验：tasks.md section 归属由 check-tasks-section-ownership.sh 单独跑。

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

# ---- tests/contract/：contract-test-agent 或 test-bugfix-agent 可写 ----
forbid_others "contract-test-agent test-bugfix-agent" \
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

# ---- migrations/*.sql, *.md：仅 migration-agent 可写 ----
forbid_others "migration-agent" \
  '^migrations/' \
  'DB migration 脚本'

# ---- 业务代码：仅 dev-agent / bugfix-agent 可写（unit test 跟 prod code 同 dir 也允许）----
forbid_others "dev-agent bugfix-agent" \
  '^(internal|cmd)/' \
  '业务代码 + unit test 仅 dev / bugfix 阶段可写'

# ---- reports/qa.md：仅 qa-agent 可写 ----
forbid_others "qa-agent" \
  '^openspec/changes/[^/]+/reports/qa\.md$' \
  '验收签收文档'

# ---- reports/bugfix-*.md：bugfix-agent / test-bugfix-agent 可写（diagnosis 报告）----
forbid_others "bugfix-agent test-bugfix-agent" \
  '^openspec/changes/[^/]+/reports/bugfix-' \
  'Bug Fix diagnosis 报告'

# ---- reports/ 兜底：禁止其他 agent 在 reports/ 下随意写 ----
forbid_others "qa-agent bugfix-agent test-bugfix-agent analyze-agent" \
  '^openspec/changes/[^/]+/reports/' \
  'reports/ 目录受控'

# ---- verify-agent 不该 commit 任何文件 ----
if [[ "$AGENT_ROLE" == "verify-agent" ]]; then
  echo "FAIL: [verify-agent] 不应 commit 任何文件（只跑测试写 title）"
  echo "$CHANGED" | sed 's/^/  /'
  FAILED=1
fi

# ---- ci-runner-agent 不该 commit 任何文件（只跑 make ci-* + 写 issue） ----
if [[ "$AGENT_ROLE" == "ci-runner-agent" ]]; then
  echo "FAIL: [ci-runner-agent] 不应 commit 任何文件（只跑 make ci-*，结果写进 BKD issue 不写 repo）"
  echo "$CHANGED" | sed 's/^/  /'
  FAILED=1
fi

# ---- M14d: DEV_TASK_SCOPE 任务维度 scope（dev-agent 并行 fanout 专用） ----
# DEV_TASK_SCOPE 非空时，dev-agent / bugfix-agent 只许改匹配 scope 的文件。
# scope 间用 `:` 分隔；每项是 shell glob（fnmatch 风格）。
if [[ -n "${DEV_TASK_SCOPE:-}" ]]; then
  if [[ "$AGENT_ROLE" == "dev-agent" || "$AGENT_ROLE" == "bugfix-agent" ]]; then
    # 用 awk 做 scope 匹配：任一 pattern 命中即 allow
    IFS=':' read -ra SCOPE_PATTERNS <<< "$DEV_TASK_SCOPE"
    out_of_scope=""
    while IFS= read -r f; do
      [[ -z "$f" ]] && continue
      matched=0
      for pat in "${SCOPE_PATTERNS[@]}"; do
        # bash extglob + 按需支持 `foo/**` → 简化成前缀或 fnmatch
        if [[ "$pat" == */ ]]; then
          # 目录前缀
          if [[ "$f" == "$pat"* ]]; then matched=1; break; fi
        elif [[ "$f" == $pat ]]; then   # bash glob，未 quote 触发匹配
          matched=1; break
        elif [[ "$f" == "$pat" ]]; then
          matched=1; break
        elif [[ "$f" == "$pat"/* ]]; then
          matched=1; break
        fi
      done
      if [[ $matched -eq 0 ]]; then
        out_of_scope+="  - $f"$'\n'
      fi
    done <<< "$CHANGED"

    if [[ -n "$out_of_scope" ]]; then
      echo "FAIL: [$AGENT_ROLE] 越出 DEV_TASK_SCOPE=$DEV_TASK_SCOPE："
      echo -n "$out_of_scope"
      FAILED=1
    fi
  fi
fi

if [[ $FAILED -eq 0 ]]; then
  echo "OK: [$AGENT_ROLE] ACL 通过"
  exit 0
else
  echo ""
  echo "如需修改这些文件，请确认：(1) 你的阶段职责是否正确；(2) 是否应由别的 agent 干"
  exit 1
fi
