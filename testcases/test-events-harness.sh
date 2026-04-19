#!/usr/bin/env bash
# 快速测 n8n /bkd-events 路由、gate 正则，无需真 agent。
# 用 TEST-01 前缀避免污染真实 REQ-xx。

set -euo pipefail

N8N_EVENTS="http://n8n.43.239.84.24.nip.io/webhook/bkd-events"
BKD_API_BASE="https://bkd-launcher--admin-jbcnet--weifashi.coder.tbc.5ok.co/api"
BKD_API="$BKD_API_BASE/mcp"
WEBHOOK_ID="01KPFP700EAJK0RCTM29H85S71"
TOKEN="GRvtsFrbNV-7fX1P2rwfDRKwnmXsSYQEn"
PROJECT_ID="77k9z58j"
REQ_ID="${REQ_ID:-TEST-01}"

# 关 BKD session webhook，防止 n8n 下发创建的 issue 跑真 agent 后回流触发连锁
webhook_off() {
  curl -sS -X PATCH "$BKD_API_BASE/settings/webhooks/$WEBHOOK_ID" \
    -H "Coder-Session-Token: $TOKEN" -H "Content-Type: application/json" \
    -d '{"isActive":false}' >/dev/null
}
webhook_on() {
  curl -sS -X PATCH "$BKD_API_BASE/settings/webhooks/$WEBHOOK_ID" \
    -H "Coder-Session-Token: $TOKEN" -H "Content-Type: application/json" \
    -d '{"isActive":true}' >/dev/null
}

SID=""

mcp_init() {
  local headers
  headers=$(curl -sS -D - -X POST "$BKD_API" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Coder-Session-Token: $TOKEN" \
    -d '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"harness","version":"1.0"}}}' \
    -o /dev/null)
  SID=$(echo "$headers" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r\n')
  [[ -n "$SID" ]] || { echo "MCP init failed. headers: $headers"; exit 1; }
}

mcp_call() {
  local payload="$1"
  curl -sS -X POST "$BKD_API" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -H "Coder-Session-Token: $TOKEN" \
    -H "Mcp-Session-Id: $SID" \
    -d "$payload"
}

bkd_create() {
  # 先 todo 创建，再 update 到目标 status，防止 working 触发真 agent
  local title="$1" tags="$2" status="$3"
  local resp iid
  resp=$(mcp_call "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"create-issue\",\"arguments\":{\"projectId\":\"workflowtest\",\"title\":\"${title}\",\"statusId\":\"todo\",\"useWorktree\":false,\"tags\":${tags}}}}")
  iid=$(echo "$resp" | grep -oE 'id[^a-z]*[a-z0-9]{8}' | head -1 | grep -oE '[a-z0-9]{8}$')
  if [[ -n "$iid" && "$status" != "todo" ]]; then
    mcp_call "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"update-issue\",\"arguments\":{\"projectId\":\"workflowtest\",\"issueId\":\"${iid}\",\"statusId\":\"${status}\"}}}" >/dev/null
  fi
  echo "$iid"
}

bkd_delete() {
  mcp_call "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"delete-issue\",\"arguments\":{\"projectId\":\"workflowtest\",\"issueId\":\"$1\"}}}" >/dev/null
}

bkd_cancel() {
  mcp_call "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"cancel-issue\",\"arguments\":{\"projectId\":\"workflowtest\",\"issueId\":\"$1\"}}}" >/dev/null 2>&1 || true
}

bkd_list() {
  mcp_call '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list-issues","arguments":{"projectId":"workflowtest","limit":200}}}'
}

bkd_list_test_ids() {
  # 只取 TEST- 前缀的 issue id
  bkd_list | python3 -c '
import sys, re, json
raw = sys.stdin.read()
# SSE data lines
for line in raw.splitlines():
    if not line.startswith("data: "): continue
    try:
        obj = json.loads(line[6:])
        text = obj.get("result",{}).get("content",[{}])[0].get("text","")
        arr = json.loads(text)
        for it in arr:
            if it.get("title","").startswith("['"$REQ_ID"']"):
                print(it["id"], it["statusId"], it["title"])
    except Exception as e:
        pass
'
}

post_webhook() {
  local title="$1" issue_id="$2" tags="$3"
  local body
  body=$(cat <<EOF
{"event":"session.completed","title":"${title}","issueId":"${issue_id}","projectId":"${PROJECT_ID}","tags":${tags}}
EOF
)
  curl -sS -X POST "$N8N_EVENTS" -H "Content-Type: application/json" -d "$body" -w "\n http=%{http_code}\n"
}

cmd_clean() {
  mcp_init
  echo "清理 ${REQ_ID}* issue..."
  ids=$(bkd_list_test_ids | awk '{print $1}')
  if [[ -z "$ids" ]]; then echo "  无"; return; fi
  for iid in $ids; do
    echo "  del $iid"
    bkd_cancel "$iid"
    bkd_delete "$iid"
  done
}

cmd_list() {
  mcp_init
  bkd_list_test_ids
}

# 真实标题（n8n IF 判断依赖中文标题，tags 里用英文）
SEED_DEV_SPEC_TITLE="开发Spec"
SEED_CONTRACT_SPEC_TITLE="契约测试Spec"
SEED_ACCEPT_SPEC_TITLE="验收测试Spec"

# 用例：3 个 Spec 都 review → 应触发开发
case_gate_pass() {
  cmd_clean
  mcp_init
  echo "=== GATE_PASS: 3 Spec review 时触发 Spec 完成 webhook，应该创建开发 ==="
  iid1=$(bkd_create "[${REQ_ID}] ${SEED_DEV_SPEC_TITLE}" "[\"dev-spec\",\"${REQ_ID}\"]" "review")
  iid2=$(bkd_create "[${REQ_ID}] ${SEED_CONTRACT_SPEC_TITLE}" "[\"contract-spec\",\"${REQ_ID}\"]" "review")
  iid3=$(bkd_create "[${REQ_ID}] ${SEED_ACCEPT_SPEC_TITLE}" "[\"accept-spec\",\"${REQ_ID}\"]" "review")
  echo "  seed $iid1 $iid2 $iid3"
  echo "--- POST webhook (契约测试Spec 完成) ---"
  post_webhook "[${REQ_ID}] ${SEED_CONTRACT_SPEC_TITLE}" "fake-con" "[\"contract-spec\",\"${REQ_ID}\"]"
  sleep 3
  echo "--- 检查结果 ---"
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE '开发$'; then
    echo "✅ PASS: 开发 issue 已创建"
  else
    echo "❌ FAIL: 没创建开发 issue"
  fi
}

# 用例：只有 2 个 Spec review，不该触发开发
case_gate_block() {
  cmd_clean
  mcp_init
  echo "=== GATE_BLOCK: 2 review + 1 working 时 Spec 完成 webhook，不该创建开发 ==="
  iid1=$(bkd_create "[${REQ_ID}] ${SEED_DEV_SPEC_TITLE}" "[\"dev-spec\",\"${REQ_ID}\"]" "review")
  iid2=$(bkd_create "[${REQ_ID}] ${SEED_CONTRACT_SPEC_TITLE}" "[\"contract-spec\",\"${REQ_ID}\"]" "review")
  iid3=$(bkd_create "[${REQ_ID}] ${SEED_ACCEPT_SPEC_TITLE}" "[\"accept-spec\",\"${REQ_ID}\"]" "working")
  echo "  seed $iid1 $iid2 $iid3"
  echo "--- POST webhook (契约测试Spec 完成) ---"
  post_webhook "[${REQ_ID}] ${SEED_CONTRACT_SPEC_TITLE}" "fake-con" "[\"contract-spec\",\"${REQ_ID}\"]"
  sleep 3
  echo "--- 检查结果 ---"
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE '开发$'; then
    echo "❌ FAIL: 提前创建了开发 issue"
  else
    echo "✅ PASS: 没创建开发 issue"
  fi
}

# 用例：开发Spec 完成不应走"Is 开发?"分支（测 endsWith vs contains）
case_dev_spec_not_dev() {
  cmd_clean
  mcp_init
  echo "=== DEV_SPEC_NOT_DEV: 开发Spec 完成不应被当作开发完成 ==="
  iid=$(bkd_create "[${REQ_ID}] ${SEED_DEV_SPEC_TITLE}" "[\"dev-spec\",\"${REQ_ID}\"]" "review")
  echo "  seed $iid"
  echo "--- POST webhook (开发Spec 完成) ---"
  post_webhook "[${REQ_ID}] ${SEED_DEV_SPEC_TITLE}" "fake-ds" "[\"dev-spec\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE '测试验证'; then
    echo "❌ FAIL: 开发Spec 误匹配，创建了测试验证"
  else
    echo "✅ PASS"
  fi
}

# 用例：真正"开发"完成 → 应创建测试验证
case_dev_ends_correctly() {
  cmd_clean
  mcp_init
  echo "=== DEV_ENDS: 开发 完成 → 应创建测试验证 ==="
  post_webhook "[${REQ_ID}] 开发" "fake-dev" "[\"dev\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE '测试验证$'; then
    echo "✅ PASS"
  else
    echo "❌ FAIL: 没创建测试验证"
  fi
}

# ===== 验收 / Verify / Bug Fix / 熔断 / 需求分析 / JSON 转义 =====

# Is 验收? endsWith 验收 不应误匹配"验收测试Spec"
case_accept_spec_not_accept() {
  cmd_clean
  mcp_init
  echo "=== ACCEPT_SPEC_NOT_ACCEPT: 验收测试Spec 不应被当作 验收 完成 ==="
  post_webhook "[${REQ_ID}] ${SEED_ACCEPT_SPEC_TITLE}" "fake-as" "[\"accept-spec\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  # 验收 完成会走 Done 节点（只发 follow-up，不创 issue）；误匹配会把它当验收完成但没下游错误
  # 用 Is Spec? 分支行不行判断：应该进到 Is Spec? → Query
  # 断言：不应走 Done 分支（用其他可观测副作用判定，此处仅检查没有额外 issue 创建）
  n=$(bkd_list_test_ids | wc -l)
  if [[ "$n" -le 1 ]]; then
    echo "✅ PASS: 没创建额外 issue（仅 seed 本身）"
  else
    echo "❌ FAIL: 创建了额外 issue（$n 行）"
  fi
}

# 测试验证 PASS → 创建 验收
case_verify_pass() {
  cmd_clean
  mcp_init
  echo "=== VERIFY_PASS: PASS [X] 测试验证 → 创建 验收 ==="
  post_webhook "PASS [${REQ_ID}] 测试验证" "fake-vf" "[\"verify\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE '验收$'; then
    echo "✅ PASS"
  else
    echo "❌ FAIL: 没创建验收"
  fi
}

# 测试验证 FAIL 且 bugfix 计数 <3 → 创建 Bug Fix
case_verify_fail_creates_bugfix() {
  cmd_clean
  mcp_init
  echo "=== VERIFY_FAIL: FAIL [X] 测试验证 + 无 bugfix 历史 → 创 Bug Fix ==="
  post_webhook "FAIL [${REQ_ID}] 测试验证 L2" "fake-vf" "[\"verify\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE 'Bug Fix'; then
    echo "✅ PASS"
  else
    echo "❌ FAIL: 没创建 Bug Fix"
  fi
}

# Bug Fix 完成 → 创建新一轮 测试验证
case_bugfix_complete() {
  cmd_clean
  mcp_init
  echo "=== BUGFIX_COMPLETE: Bug Fix 完成 → 创 测试验证 ==="
  post_webhook "[${REQ_ID}] Bug Fix" "fake-bf" "[\"bugfix\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE '测试验证$'; then
    echo "✅ PASS"
  else
    echo "❌ FAIL: 没创建测试验证"
  fi
}

# 熔断：3 个 Bug Fix 已 review，再 FAIL → 不该再创 Bug Fix，应 escalate
case_circuit_breaker() {
  cmd_clean
  mcp_init
  echo "=== CIRCUIT_BREAKER: 已有 3 个 bugfix → FAIL 应熔断 ==="
  for i in 1 2 3; do
    iid=$(bkd_create "[${REQ_ID}] Bug Fix round-$i" "[\"bugfix\",\"${REQ_ID}\"]" "review")
    echo "  seed bugfix $i: $iid"
  done
  post_webhook "FAIL [${REQ_ID}] 测试验证 L1" "fake-vf" "[\"verify\",\"${REQ_ID}\"]"
  sleep 3
  bfcount=$(bkd_list_test_ids | grep -c "Bug Fix" || true)
  echo "当前 Bug Fix issue 数：$bfcount"
  if [[ "$bfcount" -eq 3 ]]; then
    echo "✅ PASS: 熔断触发，没有新 Bug Fix"
  else
    echo "❌ FAIL: 创建了第 $bfcount 个 Bug Fix（应熔断）"
  fi
}

# 验收 PASS → Done（不创新 issue）
case_accept_pass() {
  cmd_clean
  mcp_init
  echo "=== ACCEPT_PASS: PASS [X] 验收 → Done（不创新 issue） ==="
  post_webhook "PASS [${REQ_ID}] 验收" "fake-ac" "[\"accept\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  n=$(bkd_list_test_ids | wc -l)
  if [[ "$n" -eq 0 ]]; then
    echo "✅ PASS: 没创建新 issue"
  else
    echo "❌ FAIL: 意外创建了 $n 个 issue"
  fi
}

# 验收 FAIL → 应该创建 Bug Fix（当前 workflow 可能走 Done 而漏掉）
case_accept_fail() {
  cmd_clean
  mcp_init
  echo "=== ACCEPT_FAIL: FAIL [X] 验收 → 应创 Bug Fix（检测 workflow 缺陷） ==="
  post_webhook "FAIL [${REQ_ID}] 验收" "fake-ac" "[\"accept\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE 'Bug Fix'; then
    echo "✅ PASS: 创建了 Bug Fix"
  else
    echo "❌ FAIL (workflow bug): 验收 FAIL 没有 fan-out 到 Bug Fix，可能误入 Done 分支"
  fi
}

# 需求分析完成 → 并行创 3 个 Spec
case_analyze_complete() {
  cmd_clean
  mcp_init
  echo "=== ANALYZE_COMPLETE: 需求分析 完成 → 同时创 3 个 Spec ==="
  post_webhook "[${REQ_ID}] 需求分析" "fake-an" "[\"analyze\",\"${REQ_ID}\"]"
  sleep 4
  bkd_list_test_ids
  cnt_ds=$(bkd_list_test_ids | grep -cE '开发Spec$' || true)
  cnt_cs=$(bkd_list_test_ids | grep -cE '契约测试Spec$' || true)
  cnt_as=$(bkd_list_test_ids | grep -cE '验收测试Spec$' || true)
  echo "  开发Spec=$cnt_ds, 契约测试Spec=$cnt_cs, 验收测试Spec=$cnt_as"
  if [[ "$cnt_ds" -eq 1 && "$cnt_cs" -eq 1 && "$cnt_as" -eq 1 ]]; then
    echo "✅ PASS"
  else
    echo "❌ FAIL: 3 个 Spec 数量不对"
  fi
}

# --- 新架构用例 ---

# UNSUPPORTED: 需求分析 title prefix "UNSUPPORTED" → 应进 escalate 不 fan-out Spec
case_analyze_unsupported() {
  cmd_clean
  mcp_init
  echo "=== ANALYZE_UNSUPPORTED: title 'UNSUPPORTED [X] 需求分析' 不应 fan-out 3 Specs ==="
  post_webhook "UNSUPPORTED [${REQ_ID}] 需求分析" "fake-an" "[\"analyze\",\"${REQ_ID}\"]"
  sleep 4
  bkd_list_test_ids
  specs=$(bkd_list_test_ids | grep -cE 'Spec$' || true)
  if [[ "$specs" -eq 0 ]]; then
    echo "✅ PASS: UNSUPPORTED 走 escalate，没创建 Spec"
  else
    echo "❌ FAIL: 创建了 $specs 个 Spec（应该 0）"
  fi
}

# NEEDS-CLARIFY: 同 UNSUPPORTED，走 escalate 路径
case_analyze_needs_clarify() {
  cmd_clean
  mcp_init
  echo "=== ANALYZE_NEEDS_CLARIFY: 需要澄清的需求不应 fan-out ==="
  post_webhook "NEEDS-CLARIFY [${REQ_ID}] 需求分析" "fake-an" "[\"analyze\",\"${REQ_ID}\"]"
  sleep 4
  bkd_list_test_ids
  specs=$(bkd_list_test_ids | grep -cE 'Spec$' || true)
  if [[ "$specs" -eq 0 ]]; then
    echo "✅ PASS: NEEDS-CLARIFY 不 fan-out"
  else
    echo "❌ FAIL: 创建了 $specs 个 Spec"
  fi
}

# TEST-BUG diagnosis: Bug Fix title prefix "TEST-BUG" → 启动 Test Bug Fix agent
case_bugfix_test_bug() {
  cmd_clean
  mcp_init
  echo "=== BUGFIX_TEST_BUG: 'TEST-BUG [X] Bug Fix' → 创 Test Bug Fix ==="
  post_webhook "TEST-BUG [${REQ_ID}] Bug Fix Round 1" "fake-bf" "[\"bugfix\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE 'Test Bug Fix'; then
    echo "✅ PASS: 创建了 Test Bug Fix issue"
  else
    echo "❌ FAIL: 没创建 Test Bug Fix"
  fi
}

# SPEC-BUG diagnosis: Bug Fix title prefix "SPEC-BUG" → escalate, 不创建新 issue
case_bugfix_spec_bug() {
  cmd_clean
  mcp_init
  echo "=== BUGFIX_SPEC_BUG: 'SPEC-BUG [X] Bug Fix' → escalate 不创 issue ==="
  post_webhook "SPEC-BUG [${REQ_ID}] Bug Fix Round 1" "fake-bf" "[\"bugfix\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE '(测试验证|Test Bug Fix)'; then
    echo "❌ FAIL: 误创建了测试验证或 Test Bug Fix"
  else
    echo "✅ PASS: 走 escalate，没新 issue"
  fi
}

# Test Bug Fix 完成 → 回流应创建测试验证
case_test_bugfix_complete() {
  cmd_clean
  mcp_init
  echo "=== TEST_BUGFIX_COMPLETE: '[X] Test Bug Fix' 完成 → 创测试验证 ==="
  post_webhook "[${REQ_ID}] Test Bug Fix" "fake-tbf" "[\"test-bugfix\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE '测试验证$'; then
    echo "✅ PASS: Test Bug Fix 完成后重跑 verify"
  else
    echo "❌ FAIL: 没创建测试验证"
  fi
}

# 常规 Bug Fix (无 prefix) 完成 → 当作 CODE BUG 修完，重跑 verify（原行为）
case_bugfix_code_bug() {
  cmd_clean
  mcp_init
  echo "=== BUGFIX_CODE_BUG: 常规 '[X] Bug Fix' 完成 → 创测试验证 ==="
  post_webhook "[${REQ_ID}] Bug Fix" "fake-bf" "[\"bugfix\",\"${REQ_ID}\"]"
  sleep 3
  bkd_list_test_ids
  if bkd_list_test_ids | grep -qE '测试验证$'; then
    echo "✅ PASS"
  else
    echo "❌ FAIL: 没创建测试验证"
  fi
}

# JSON 转义：Ctx 的 ttl 字段遇到带 " 和 \n 的 title 应安全
case_json_escape() {
  cmd_clean
  mcp_init
  echo "=== JSON_ESCAPE: title 含 \" 和 \\n，Ctx 的 JSON.stringify 应处理 ==="
  # 伪造带双引号的 title（严格来说 BKD webhook payload 里 title 不会带原始 "，但测 n8n 的稳健性）
  local evil_title='[TEST-01] 开发 with "quote" and\nnewline'
  curl -sS -X POST "$N8N_EVENTS" -H "Content-Type: application/json" \
    -d "$(python3 -c "import json; print(json.dumps({'event':'session.completed','title':'${evil_title}','issueId':'fake-ev','projectId':'${PROJECT_ID}','tags':['dev','${REQ_ID}']}))")" \
    -w "\nhttp=%{http_code}\n"
  sleep 3
  bkd_list_test_ids
  # Is 开发? endsWith "开发" — evil_title 不以"开发"结尾（有后缀），所以应落入 Is Spec? or 需求分析?
  # 主要验证 n8n 没崩（http=200 即通过 Init/Ctx 阶段）
  echo "  (Ctx 没崩 => JSON 转义 OK)"
}

cmd_all() {
  webhook_off
  echo "[BKD webhook disabled for test run]"
  case_gate_block
  case_gate_pass
  case_dev_spec_not_dev
  case_dev_ends_correctly
  case_accept_spec_not_accept
  case_verify_pass
  case_verify_fail_creates_bugfix
  case_bugfix_complete
  case_circuit_breaker
  case_accept_pass
  case_accept_fail
  case_analyze_complete
  case_json_escape
  # 新架构用例
  case_analyze_unsupported
  case_analyze_needs_clarify
  case_bugfix_test_bug
  case_bugfix_spec_bug
  case_test_bugfix_complete
  case_bugfix_code_bug
  cmd_clean
  echo "[BKD webhook 仍然 disabled — 若要恢复实跑流程: ./test-events-harness.sh webhook_on]"
}

case "${1:-all}" in
  init) mcp_init; echo "SID=$SID" ;;
  clean) cmd_clean ;;
  list) cmd_list ;;
  case) webhook_off; "case_${2}" ;;
  all) cmd_all ;;
  webhook_off) webhook_off; echo "BKD webhook OFF" ;;
  webhook_on) webhook_on; echo "BKD webhook ON" ;;
  *) echo "Unknown: $1"; exit 1 ;;
esac
