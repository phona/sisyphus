#!/usr/bin/env python3
"""Generates charts/n8n-workflows/v3.1/v3-events.template.json declaratively.

Each action is described as a short spec; the builder emits Cr/Id/Fu/St nodes
and wires them to Switch outputs. Keeps node count low while still giving
every action a visible execution trail in the n8n canvas.
"""
import json, os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO_ROOT, 'charts/n8n-workflows/v3.1/v3-events.template.json')

BKD_URL = 'https://bkd-launcher--admin-jbcnet--weifashi.coder.tbc.5ok.co/api/mcp'
TOKEN = 'GRvtsFrbNV-7fX1P2rwfDRKwnmXsSYQEn'

def bkd_headers(with_sid=True, sid_expr='={{ $node["[ENTRY] Ctx 提取"].json.sid }}'):
    hs = [
        {"name": "Accept", "value": "application/json, text/event-stream"},
        {"name": "Coder-Session-Token", "value": TOKEN},
    ]
    if with_sid:
        hs.append({"name": "Mcp-Session-Id", "value": sid_expr})
    return {"parameters": hs}

def http_node(node_id, name, x, y, json_body, rpc_id=1, timeout=30000, retry=False, on_error=None):
    params = {
        "method": "POST",
        "url": BKD_URL,
        "sendBody": True,
        "contentType": "json",
        "specifyBody": "json",
        "jsonBody": json_body,
        "sendHeaders": True,
        "headerParameters": bkd_headers(),
        "options": {
            "response": {"response": {"fullResponse": True, "responseFormat": "text"}},
            "timeout": timeout,
        },
    }
    if on_error:
        params["onError"] = on_error
    node = {
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.2,
        "position": [x, y],
        "parameters": params,
    }
    if retry:
        node["retryOnFail"] = True
        node["maxTries"] = 2
        node["waitBetweenTries"] = 2000
    return node

def code_node(node_id, name, x, y, js_code):
    return {
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [x, y],
        "parameters": {"language": "javaScript", "jsCode": js_code},
    }

def noop(node_id, name, x, y):
    return {"id": node_id, "name": name, "type": "n8n-nodes-base.noOp", "typeVersion": 1, "position": [x, y], "parameters": {}}

# ─── Entry ────────────────────────────────────────────────────────────────
nodes = []
conns = {}

nodes.append({
    "id": "wh", "name": "[ENTRY] Hook", "type": "n8n-nodes-base.webhook",
    "typeVersion": 2, "webhookId": "b02404e7-1027-4349-8cfa-705f7bcee7e7",
    "position": [200, 800],
    "parameters": {"httpMethod": "POST", "path": "bkd-events", "responseMode": "onReceived", "options": {}},
})

nodes.append(http_node(
    "init", "[ENTRY] Init MCP", 420, 800,
    '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"n8n-v31","version":"0.1"}}}',
    timeout=15000, retry=True,
))
# override headers — no Mcp-Session-Id on init
nodes[-1]["parameters"]["headerParameters"]["parameters"] = nodes[-1]["parameters"]["headerParameters"]["parameters"][:2]

nodes.append(http_node(
    "getissue", "[ENTRY] Get Issue", 620, 800,
    '={"jsonrpc":"2.0","id":50,"method":"tools/call","params":{"name":"get-issue","arguments":{"projectId":"{{ $node["[ENTRY] Hook"].json.body.projectId }}","issueId":"{{ $node["[ENTRY] Hook"].json.body.issueId }}"}}}',
    timeout=10000, on_error="continueRegularOutput",
))
# init sid comes from init response headers, not ctx yet:
nodes[-1]["parameters"]["headerParameters"]["parameters"][2]["value"] = '={{ $json.headers["mcp-session-id"] }}'

nodes.append(code_node(
    "ctx", "[ENTRY] Ctx 提取", 840, 800,
    """// Parse Init MCP + Get Issue responses into a flat ctx object consumed by Router.
const hookBody = $node['[ENTRY] Hook'].json.body || {};
const initHeaders = $node['[ENTRY] Init MCP'].json.headers || {};
const gi = $node['[ENTRY] Get Issue'].json;

const sid = initHeaders['mcp-session-id'] || '';

function parseSse(raw) {
  if (!raw) return null;
  const s = typeof raw === 'string' ? raw : String(raw.data || raw.body || raw);
  const m = s.match(/data:\\s*(\\{[\\s\\S]*?\\})\\s*$/m);
  if (!m) return null;
  try { return JSON.parse(m[1]); } catch { return null; }
}
function extractIssue(sseText) {
  const env = parseSse(sseText);
  if (!env || !env.result) return null;
  const c = Array.isArray(env.result.content) ? env.result.content[0] : null;
  if (!c || typeof c.text !== 'string') return env.result;
  try { return JSON.parse(c.text); } catch { return null; }
}

const issue = extractIssue(gi && (gi.data || gi.body || gi)) || {};
const tags = Array.isArray(issue.tags) ? issue.tags : (Array.isArray(hookBody.tags) ? hookBody.tags : []);
const priorStatusId = issue.statusId || hookBody.priorStatusId || null;
const title = hookBody.title || issue.title || '';
const event = hookBody.event || 'session.completed';
const issueId = hookBody.issueId || issue.id || '';

// Parse `## CI Result` block from issue description so Router can diagnose CI failures.
// ci-runner appends this block via follow-up-issue; BKD stores it on issue.description.
function parseCiResult(desc) {
  const s = typeof desc === 'string' ? desc : '';
  const m = s.match(/##\\s*CI Result\\s*([\\s\\S]+?)(?=\\n##\\s|\\n\\s*$|$)/);
  if (!m) return null;
  const body = m[1];
  const pick = (k) => (body.match(new RegExp('^\\\\s*' + k + '\\\\s*:\\\\s*(.*?)\\\\s*$', 'm')) || [])[1] || null;
  const failedTests = [];
  const ft = body.match(/failed_tests\\s*:\\s*\\n([\\s\\S]*?)(?=\\n[a-z_]+:|$)/);
  if (ft) {
    for (const line of ft[1].split('\\n')) {
      const mm = line.match(/^\\s*-\\s+(.+?)\\s*$/);
      if (mm) failedTests.push(mm[1]);
    }
  }
  const tail = body.match(/stderr_tail\\s*:\\s*\\|\\s*\\n([\\s\\S]+)$/);
  return {
    target: pick('target'),
    exitCode: pick('exit_code') === null ? null : parseInt(pick('exit_code'), 10),
    failedTests,
    stderrTail: tail ? tail[1].replace(/^\\s{0,4}/gm, '') : '',
  };
}
// Harness/backdoor: allow webhook body to inject _ciResult directly (tests).
const _ciResult = (hookBody && hookBody._ciResult && typeof hookBody._ciResult === 'object')
  ? hookBody._ciResult
  : parseCiResult(issue.description);

// Dedup by (timestamp + issueId + event [+ executionId]).
// 观察：BKD 的 executionId 不是 session-unique（同 issue 多次 session 复用同一 UUID），
// 只是 issue-level 标识。真正唯一的是 timestamp（毫秒精度）。同一事件 BKD 重投递会带
// 完全相同的 timestamp+executionId。合法重发（agent 再跑）timestamp 不同。
const eventId = (hookBody.timestamp || '') + '|' + issueId + '|' + event
              + (hookBody.executionId ? '|' + hookBody.executionId : '');
const staticData = $getWorkflowStaticData('global');
staticData.seen = staticData.seen || {};
const dedupSkip = !!staticData.seen[eventId];
if (!dedupSkip) {
  staticData.seen[eventId] = Date.now();
  // Cap at 5000 entries to bound memory; evict oldest 500 on overflow.
  const keys = Object.keys(staticData.seen);
  if (keys.length > 5000) {
    keys.sort((a, b) => staticData.seen[a] - staticData.seen[b])
        .slice(0, 500)
        .forEach(k => delete staticData.seen[k]);
  }
}

return [{ json: {
  sid,
  event,
  issueId,
  projectId: hookBody.projectId || issue.projectId || '',
  title,
  tags,
  priorStatusId,
  metadata: hookBody.metadata || {},
  _dedupSkip: !!dedupSkip,
  _dedupKey: eventId,
  _ciResult,
}}];
"""
))

# ─── Entry 2: intent:analyze hook ────────────────────────────────────────
nodes.append({
    "id": "wh_intent", "name": "[ENTRY intent] Hook",
    "type": "n8n-nodes-base.webhook", "typeVersion": 2,
    "webhookId": "c02404e7-1027-4349-8cfa-705f7bcee7e8",
    "position": [200, 1500],
    "parameters": {"httpMethod": "POST", "path": "bkd-issue-updated", "responseMode": "onReceived", "options": {}},
})

nodes.append(http_node(
    "init_intent", "[ENTRY intent] Init MCP", 420, 1500,
    '{"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"n8n-v31-intent","version":"0.1"}}}',
    timeout=15000, retry=True,
))
nodes[-1]["parameters"]["headerParameters"]["parameters"] = nodes[-1]["parameters"]["headerParameters"]["parameters"][:2]

nodes.append(code_node(
    "ctx_intent", "[ENTRY intent] Ctx", 640, 1500,
    """// Intent entry: BKD issue.updated webhook with changes.tag containing intent:analyze.
// Emits the same ctx shape as [ENTRY] Ctx 提取 so Router handles both uniformly.
const hookBody = $node['[ENTRY intent] Hook'].json.body || {};
const initHeaders = $node['[ENTRY intent] Init MCP'].json.headers || {};
const sid = initHeaders['mcp-session-id'] || '';

// changes.tag is a JSON-stringified array of the NEW tag list after update
let tags = [];
try {
  if (typeof hookBody?.changes?.tag === 'string') {
    tags = JSON.parse(hookBody.changes.tag);
  } else if (Array.isArray(hookBody?.changes?.tag)) {
    tags = hookBody.changes.tag;
  } else if (Array.isArray(hookBody.tags)) {
    tags = hookBody.tags;
  }
} catch {}

const issueId = hookBody.issueId || hookBody.id || '';
const projectId = hookBody.projectId || '';
const issueNumber = hookBody.issueNumber || hookBody.number || null;
// Strip previously-applied [REQ-xxx] [STAGE] prefixes to get the clean title.
// Prevents title stacking on retries (e.g. user removes `analyze` and re-adds `intent:analyze`).
const rawTitle = hookBody.title || '';
const strippedTitle = rawTitle.replace(/^(\\s*\\[REQ-[\\w-]+\\]\\s*\\[[^\\]]+\\]\\s*)+/, '').trim() || rawTitle;
// JSON-escape so downstream jsonBody interpolation stays valid even when title contains `"` or `\\`
const originalTitle = JSON.stringify(strippedTitle).slice(1, -1);
// reqId: use existing REQ-xxx tag or generate from issueNumber
const existingReq = tags.find(t => /^REQ-[\\w-]+$/.test(t));
const reqId = existingReq || (issueNumber ? `REQ-${issueNumber}` : null);

return [{ json: {
  sid,
  event: 'issue.updated',
  issueId,
  projectId,
  issueNumber,
  originalTitle,
  title: originalTitle,
  reqId,
  tags,
  priorStatusId: hookBody.priorStatusId || hookBody.statusId || null,
  metadata: hookBody.metadata || {}
}}];
"""
))

nodes.append(code_node("router", "Router", 1060, 1100, "{{ROUTER_JS}}"))

# Switch with 13 outputs: 12 actions + fallback
action_order = [
    "skip",
    "start_analyze",
    "create_ci_runner",
    "comment_back",
    "create_bugfix",
    "create_test_fix",
    "create_reviewer",
    "create_accept",
    "open_github_issue",
    "done_archive",
    "fanout_specs",
    "mark_spec_reviewed",
    "escalate",
]
switch_rules = []
for act in action_order:
    switch_rules.append({
        "conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose"},
            "combinator": "and",
            "conditions": [{
                "operator": {"type": "string", "operation": "equals"},
                "leftValue": "={{ $json.action }}",
                "rightValue": act,
            }],
        },
        "outputKey": act,
    })
nodes.append({
    "id": "switch", "name": "Dispatch Action",
    "type": "n8n-nodes-base.switch", "typeVersion": 3.2,
    "position": [1280, 800],
    "parameters": {
        "mode": "rules",
        "rules": {"values": switch_rules},
        "options": {"fallbackOutput": "extra", "renameFallbackOutput": "other"},
    },
})

# ─── wire entries ─────────────────────────────────────────────────────────
# Entry A: session events
conns["[ENTRY] Hook"]             = {"main": [[{"node": "[ENTRY] Init MCP", "type": "main", "index": 0}]]}
conns["[ENTRY] Init MCP"]         = {"main": [[{"node": "[ENTRY] Get Issue", "type": "main", "index": 0}]]}
conns["[ENTRY] Get Issue"]        = {"main": [[{"node": "[ENTRY] Ctx 提取", "type": "main", "index": 0}]]}
conns["[ENTRY] Ctx 提取"]         = {"main": [[{"node": "Router", "type": "main", "index": 0}]]}
# Entry B: intent:analyze webhook
conns["[ENTRY intent] Hook"]      = {"main": [[{"node": "[ENTRY intent] Init MCP", "type": "main", "index": 0}]]}
conns["[ENTRY intent] Init MCP"]  = {"main": [[{"node": "[ENTRY intent] Ctx", "type": "main", "index": 0}]]}
conns["[ENTRY intent] Ctx"]       = {"main": [[{"node": "Router", "type": "main", "index": 0}]]}
conns["Router"]                   = {"main": [[{"node": "Dispatch Action", "type": "main", "index": 0}]]}

# ─── Action builders ────────────────────────────────────────────────────────
# Each action has its own column offset (y) and a triplet of Cr/Id/Fu/St nodes
# where applicable. Simple actions have only one or two nodes.

# id-extractor code (reused)
ID_EXTRACT = """const prev = $node['{PREV}'].json;
const raw = (prev && (prev.data || prev.body || prev)) || '';
const text = typeof raw === 'string' ? raw : String(raw);
const m = text.match(/data:\\s*(\\{[\\s\\S]*?\\})\\s*$/m);
let iid = '';
if (m) {
  try {
    const env = JSON.parse(m[1]);
    const c = env.result && Array.isArray(env.result.content) ? env.result.content[0] : null;
    if (c && typeof c.text === 'string') {
      const parsed = JSON.parse(c.text);
      iid = parsed.id || parsed.issueId || '';
    }
  } catch {}
}
// Carry forward params to downstream Fu/St. Router is the default source, but when the
// chain entered via [GH Bugfix Gate] (GH → BUG), that gate reshaped params (added round).
// Prefer gate's params if the gate node executed in this run.
const routerOut = $node['Router'].json || {};
let gateParams = null;
try {
  const g = $node['[GH Bugfix Gate]'];
  if (g && g.json && g.json.params) gateParams = g.json.params;
} catch (_) {}
const params = gateParams || routerOut.params || {};
return [{ json: { ...routerOut, params, iid, _crText: text.slice(-300) } }];
"""

def create_issue_body(title_expr, tags_list, rpc_id):
    """Build JSON body expression for BKD create-issue."""
    tags_js = ",".join(f'"{t}"' for t in tags_list)
    return ('={"jsonrpc":"2.0","id":' + str(rpc_id) +
            ',"method":"tools/call","params":{"name":"create-issue","arguments":'
            '{"projectId":"{{ $node["[ENTRY] Ctx 提取"].json.projectId }}",'
            f'"title":"{title_expr}","statusId":"todo","useWorktree":false,'
            f'"tags":[{tags_js}]}}}}}}')

TOOLS_WHITELIST = (
    "## 工具白名单 (HARD CONSTRAINT — 第一优先级)\n"
    "**仅允许**调用以下 MCP 工具：\n"
    "- mcp__bkd__* (管 BKD issue 状态/tags/follow-up/get-issue)\n"
    "- mcp__aissh-tao__* (在 vm-node04 exec_run 命令、file_deploy)\n"
    "\n"
    "**绝对禁止**调用：\n"
    "- mcp__vibe_kanban__* (另一套 kanban，不是我们用的 BKD)\n"
    "- mcp__erpnext__* (ERP 系统，和本任务无关)\n"
    "- Task / Agent 子代理工具 (会污染 session)\n"
    "- 任何其他未列出的 MCP\n"
    "\n"
    "违反即视为任务失败。即使工具列表里出现其他工具，你也必须当作不存在。\n"
    "BKD session 日志会记录工具调用，审计会拦截越权调用。\n"
    "\n"
    "─────────\n"
    "\n"
)

# 多次 live 踩坑：agent 在 follow-up / update-issue 时把 issueId 填成了 prompt 里作为参考的\n# SOURCE_* / PARENT_* / ACCEPT_ISSUE 等上游变量，结果把父 issue 的 tags 或 description 改了。\n# 例如：[DONE] agent 把 pr:xxx tag 写到了父 accept issue 693；[GH] agent 把 gh:3 写到了父 ci issue 703。\n# 统一硬约束：所有 update-issue / follow-up-issue 的 issueId 必须取自本 session（通过 mcp__bkd__get-issue\n# 返回的 id 或 session 上下文），不是任何 prompt 里"参考用"的变量。
SELF_ISSUE_CONSTRAINT = (
    "## 只改本 issue (HARD CONSTRAINT — 第二优先级)\n"
    "\n"
    "你的 prompt 里可能带上游参考变量 (REQ / BRANCH / SRC_ISSUE / ACCEPT_ISSUE / PARENT_ISSUE 等)。\n"
    "**它们只用来读**，任何 mcp__bkd__update-issue / mcp__bkd__follow-up-issue 的 issueId 参数\n"
    "**必须是你自己 session 所在的 issue**，不是上面任何一个。\n"
    "\n"
    "怎么确认自己的 issueId：\n"
    "1. 最稳的做法：调一次 `mcp__bkd__list-issues` 或从 session 自身上下文拿 issueId；\n"
    "2. 或者对你收到的 issueNumber（若 prompt 或 session 提供）调 `mcp__bkd__get-issue` 对齐。\n"
    "\n"
    "### tags 覆盖语义\n"
    "BKD update-issue 会**替换**整个 tags 数组。每次 update 前必须：\n"
    "1. `mcp__bkd__get-issue` 取当前 tags；\n"
    "2. 在内存里 merge：保留所有非本阶段管理的 tag + 追加本阶段要写入的 tag；\n"
    "3. 再 update-issue。\n"
    "\n"
    "**不要**只传新 tag 导致原 REQ-xxx / 阶段 tag 被抹掉，Router 会路由到 unknown。\n"
    "\n"
    "违反这条约束 = 任务失败。\n"
    "\n"
    "─────────\n"
    "\n"
)

def follow_up_body(issue_id_expr, prompt_text, rpc_id):
    # Prepend tool whitelist + self-issue constraint to every prompt, regardless of source.
    full_prompt = TOOLS_WHITELIST + SELF_ISSUE_CONSTRAINT + prompt_text
    # Escape sequence: backslash first, then double-quote, then newline.
    prompt_esc = full_prompt.replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34)).replace('\n', '\\n')
    # Use Router._input for projectId — works under both [ENTRY] Ctx 提取 and [ENTRY intent] Ctx
    return ('={"jsonrpc":"2.0","id":' + str(rpc_id) +
            ',"method":"tools/call","params":{"name":"follow-up-issue","arguments":'
            '{"projectId":"{{ $(\'Router\').first().json._input.projectId }}",'
            f'"issueId":"{issue_id_expr}","prompt":"{prompt_esc}"}}}}}}')

def update_issue_body(issue_id_expr, tags_list, rpc_id, status_id=None):
    tags_js = ",".join(f'"{t}"' for t in tags_list)
    extras = f',"statusId":"{status_id}"' if status_id else ''
    return ('={"jsonrpc":"2.0","id":' + str(rpc_id) +
            ',"method":"tools/call","params":{"name":"update-issue","arguments":'
            '{"projectId":"{{ $node["[ENTRY] Ctx 提取"].json.projectId }}",'
            f'"issueId":"{issue_id_expr}"{extras},"tags":[{tags_js}]}}}}}}')

# === Action: skip ============================================================
nodes.append(noop("a_skip", "[A] skip", 1580, 80))

# === Action: start_analyze ==================================================
# Called when user adds intent:analyze tag on a BKD issue.
# Router params: {issueId, reqId, originalTitle, repoUrl}
# Uses {{ $('Router').first().json._input.sid }} since [ENTRY intent] Ctx was parent
y = 180
nodes.append(http_node(
    "anz_upd", "[ANZ] Update title+tags", 1580, y,
    ('={"jsonrpc":"2.0","id":90,"method":"tools/call","params":{"name":"update-issue","arguments":'
     '{"projectId":"{{ $(\'Router\').first().json._input.projectId }}",'
     '"issueId":"{{ $json.params.issueId }}",'
     '"title":"[{{ $json.params.reqId }}] [ANALYZE] {{ $json.params.originalTitle }}",'
     '"tags":["analyze","{{ $json.params.reqId }}"]}}}'),
    rpc_id=90, timeout=15000, on_error="continueErrorOutput",
))
# override Mcp-Session-Id source
for hp in nodes[-1]["parameters"]["headerParameters"]["parameters"]:
    if hp["name"] == "Mcp-Session-Id":
        hp["value"] = '={{ $(\'Router\').first().json._input.sid }}'

nodes.append(http_node(
    "anz_fu", "[ANZ] Send analyze prompt", 1800, y,
    follow_up_body(
        "{{ $('Router').first().json.params.issueId }}",
        "## 需求分析 (ANALYZE)\n"
        "AGENT_ROLE=analyze-agent\n"
        "REQ={{ $('Router').first().json.params.reqId }}\n"
        "REPO_URL={{ $('Router').first().json.params.repoUrl }}\n"
        "\n"
        "## 职责：需求拆解 + 立框架\n"
        "- 看清需求要改什么 / 怎么改 / 联动哪些仓库\n"
        "- 定高层方案（选型、取舍、风险）\n"
        "- **立 tasks.md 骨架**（三个 Stage section，section 下只列 TODO checkbox，不填细节）\n"
        "\n"
        "## 产出（严格对齐 openspec 规范，不自创文件）\n"
        "1. `openspec/changes/$REQ/proposal.md`\n"
        "   - 一句话需求 + 用户价值\n"
        "   - **影响范围**：改哪些模块 / 文件 / 仓库（写清楚）\n"
        "   - 支持性判定：支持 / 不支持 / 需澄清\n"
        "   - frontmatter 写 `repos: [ubox-crosser]`（单仓）或 `[repoA, repoB]`（多仓）\n"
        "\n"
        "2. `openspec/changes/$REQ/design.md`（可选但建议）\n"
        "   - 高层方案、选型、依赖、风险\n"
        "   - **不**写具体 OpenAPI / SQL / UI 代码\n"
        "\n"
        "3. `openspec/changes/$REQ/tasks.md` —— 只立骨架，三个 Stage section:\n"
        "```markdown\n"
        "## Stage: contract-tests (owner: contract-test-agent)\n"
        "- [ ] TODO: 列出要覆盖的 API/DB/UI 契约点\n"
        "\n"
        "## Stage: acceptance-tests (owner: accept-test-agent)\n"
        "- [ ] TODO: 列出要验的用户行为\n"
        "\n"
        "## Stage: implementation (owner: dev-agent)\n"
        "- [ ] TODO: 列出要实现的模块\n"
        "```\n"
        "每条 checkbox 只写粗粒度 TODO，**不**填细节。下游对应 agent 会替换为具体任务。\n"
        "\n"
        "## 禁止越权（pre-commit ACL 会拦）\n"
        "- 不要写 `contract.spec.yaml`（contract-test-agent 的活）\n"
        "- 不要写 `openspec/changes/$REQ/specs/**`（下游 agent 的活）\n"
        "- 不要写 `tests/**`（下游 test-agent 或 dev-agent 的活）\n"
        "- 不要填 tasks.md 里具体任务内容（只立 section + TODO 框）\n"
        "\n"
        "## 完成时\n"
        "- 3 个文件 commit + push 到 feat/$REQ\n"
        "- update-issue tags（保留 analyze + $REQ）追加：\n"
        "  - `repo:<name>`（proposal.md 里每个 repo 一个 tag）\n"
        "  - 不支持：`decision:unsupported`；需澄清：`decision:needs-clarify`\n"
        "- move review\n"
        "\n"
        "n8n 收到 session.completed 后 fan-out 2 个 test-agent：contract-test + accept-test。",
        91),
    timeout=30000, on_error="continueRegularOutput",
))
for hp in nodes[-1]["parameters"]["headerParameters"]["parameters"]:
    if hp["name"] == "Mcp-Session-Id":
        hp["value"] = '={{ $(\'Router\').first().json._input.sid }}'

nodes.append(http_node(
    "anz_trg", "[ANZ] Trigger agent", 2020, y,
    ('={"jsonrpc":"2.0","id":92,"method":"tools/call","params":{"name":"update-issue","arguments":'
     '{"projectId":"{{ $(\'Router\').first().json._input.projectId }}",'
     '"issueId":"{{ $(\'Router\').first().json.params.issueId }}",'
     '"statusId":"working"}}}'),
    rpc_id=92, timeout=10000, on_error="continueRegularOutput",
))
for hp in nodes[-1]["parameters"]["headerParameters"]["parameters"]:
    if hp["name"] == "Mcp-Session-Id":
        hp["value"] = '={{ $(\'Router\').first().json._input.sid }}'

conns["[ANZ] Update title+tags"] = {"main": [[{"node": "[ANZ] Send analyze prompt", "type": "main", "index": 0}]]}
conns["[ANZ] Send analyze prompt"] = {"main": [[{"node": "[ANZ] Trigger agent", "type": "main", "index": 0}]]}

# === Action: create_ci_runner ================================================
# Uses Router params: reqId, target, branch, workdir, repoUrl, parentStage, parentIssueId
y = 260
nodes.append(http_node(
    "ci_cr", "[CI] Cr", 1580, y,
    create_issue_body(
        '[{{ $json.params.reqId }}] [CI {{ $json.params.target }}] self-check {{ $json.params.parentStage }}',
        [
            'ci',
            '{{ $json.params.reqId }}',
            'target:{{ $json.params.target }}',
            'parent:{{ $json.params.parentStage }}',
            'parent-id:{{ $json.params.parentIssueId }}',
        ],
        10),
    rpc_id=10, retry=True, on_error="continueErrorOutput",
))
nodes.append(code_node("ci_id", "[CI] Id", 1800, y, ID_EXTRACT.replace("{PREV}", "[CI] Cr")))
nodes.append(http_node(
    "ci_fu", "[CI] Fu", 2020, y,
    follow_up_body(
        '{{ $json.iid }}',
        "## CI 核验 (CI-RUNNER)\n"
        "AGENT_ROLE=ci-runner-agent\n"
        "REQ={{ $json.params.reqId }}\n"
        "TARGET={{ $json.params.target }}\n"
        "BRANCH={{ $json.params.branch }}\n"
        "WORKDIR={{ $json.params.workdir }}\n"
        "REPO_URL={{ $json.params.repoUrl }}\n"
        "PARENT_ISSUE={{ $json.params.parentIssueId }}  # 只用于参考,禁止对它 update\n"
        "PARENT_STAGE={{ $json.params.parentStage }}\n"
        "\n"
        "## 硬约束\n"
        "1. 所有命令只能通过 mcp__aissh-tao__exec_run 在 vm-node04 上执行。禁止本地 Bash。\n"
        "2. 每条 exec_run 命令必须以 cd $WORKDIR 开头。禁止 cd 到 $WORKDIR 之外。\n"
        "3. 禁改 repo 任何文件 (pre-commit ACL 会拦)。\n"
        "4. **update-issue 时 issueId 必须是本 session 所在 issue (即你自己),绝不是 PARENT_ISSUE**。PARENT_ISSUE 只是上下文参考值。\n"
        "5. tags 覆盖语义:update-issue 会替换 tags 整个数组。必须完整列出所有要保留的 tag (含 parent-id)。\n"
        "6. stderr_tail 原样贴 make output 最后 50 行,不总结不翻译。\n"
        "7. 失败不分析原因 —— 你是报告员不是诊断师。\n"
        "\n"
        "## 步骤\n"
        "Step 1 bootstrap (一条 exec_run, bash -c 包起来):\n"
        "  首次: git clone --branch $BRANCH $REPO_URL $WORKDIR\n"
        "  已存在: cd $WORKDIR && git fetch origin && git reset --hard origin/$BRANCH\n"
        "  两种情况幂等处理。\n"
        "\n"
        "Step 2 跑测试 (一条 exec_run):\n"
        "  cd $WORKDIR && time BASE_REV=origin/master make ci-$TARGET 2>&1\n"
        "  **BASE_REV=origin/master 让 make ci-lint 只检查本分支相对 master 新增的 lint 问题**,不被 baseline 污染。\n"
        "  记录 exit_code / duration_ms / stderr 最后 50 行 / 失败测试名列表。\n"
        "\n"
        "Step 3 写结果 (对**本 issue**操作,不是 PARENT_ISSUE):\n"
        "  A. mcp__bkd__follow-up-issue 把下面 block 追加到本 issue 正文:\n"
        "\n"
        "## CI Result\n"
        "target: $TARGET\n"
        "branch: $BRANCH\n"
        "workdir: $WORKDIR\n"
        "commit: <cd $WORKDIR && git rev-parse --short HEAD>\n"
        "exit_code: <0 或非 0>\n"
        "duration_ms: <ms>\n"
        "coverage: <% 或空>\n"
        "failed_tests:\n"
        "  - <name>\n"
        "stderr_tail: |\n"
        "  <原样最后 50 行>\n"
        "\n"
        "  B. mcp__bkd__update-issue 改**本 issue**的 tags (不是 PARENT_ISSUE)。tags 必须完整列:\n"
        "     tags=[ci, {{ $json.params.reqId }}, target:{{ $json.params.target }}, parent:{{ $json.params.parentStage }}, parent-id:{{ $json.params.parentIssueId }}, <ci:pass 或 ci:fail>]\n"
        "  C. mcp__bkd__update-issue 改**本 issue**状态: statusId=review",
        11),
    rpc_id=11, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "ci_st", "[CI] St", 2240, y,
    update_issue_body(
        '{{ $node["[CI] Id"].json.iid }}',
        [
            'ci',
            '{{ $node["[CI] Id"].json.params.reqId }}',
            'target:{{ $node["[CI] Id"].json.params.target }}',
            'parent:{{ $node["[CI] Id"].json.params.parentStage }}',
            'parent-id:{{ $node["[CI] Id"].json.params.parentIssueId }}',
        ],
        12, status_id='working'),
    rpc_id=12, timeout=10000, on_error="continueRegularOutput",
))
conns["[CI] Cr"] = {"main": [[{"node": "[CI] Id", "type": "main", "index": 0}]]}
conns["[CI] Id"] = {"main": [[{"node": "[CI] Fu", "type": "main", "index": 0}]]}
conns["[CI] Fu"] = {"main": [[{"node": "[CI] St", "type": "main", "index": 0}]]}

# === Action: comment_back ====================================================
y = 400
nodes.append(http_node(
    "cmt_fu", "[CMT] Fu", 1580, y,
    follow_up_body(
        '{{ $json.params.targetIssueId }}',
        '🔴 CI 自检未过\n\nREQ={{ $json.params.reqId }}\n原因: {{ $json.params.reason }}\nCI issue: {{ $json.params.ciIssueId }}\n\n请查看 CI issue 的 `## CI Result` block，修复后 move review 重新触发。此为轻量反馈，不计入 bugfix round。',
        20),
    rpc_id=20, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "cmt_st", "[CMT] St (回 in_progress)", 1800, y,
    update_issue_body(
        '{{ $node["Router"].json.params.targetIssueId }}',
        [],  # empty tags means we keep existing (but update-issue replaces; use empty OK)
        21, status_id="working",
    ),
    rpc_id=21, timeout=10000, on_error="continueRegularOutput",
))
# Tags list empty would replace tags. Instead just update status. Rebuild body without tags:
nodes[-1]["parameters"]["jsonBody"] = ('={"jsonrpc":"2.0","id":21,"method":"tools/call","params":{"name":"update-issue","arguments":'
    '{"projectId":"{{ $node["[ENTRY] Ctx 提取"].json.projectId }}",'
    '"issueId":"{{ $node["Router"].json.params.targetIssueId }}","statusId":"working"}}}')
conns["[CMT] Fu"] = {"main": [[{"node": "[CMT] St (回 in_progress)", "type": "main", "index": 0}]]}

# === Action: create_bugfix ===================================================
y = 540
nodes.append(http_node(
    "bug_cr", "[BUG] Cr", 1580, y,
    create_issue_body(
        '[{{ $json.params.reqId }}] [BUGFIX round-{{ $json.params.round }}] {{ $json.params.reason }}',
        ['bugfix', '{{ $json.params.reqId }}', 'round-{{ $json.params.round }}'],
        30),
    rpc_id=30, retry=True, on_error="continueErrorOutput",
))
nodes.append(code_node("bug_id", "[BUG] Id", 1800, y, ID_EXTRACT.replace("{PREV}", "[BUG] Cr")))
nodes.append(http_node(
    "bug_fu", "[BUG] Fu", 2020, y,
    follow_up_body(
        '{{ $json.iid }}',
        '## BUG FIX (DEV-FIX)\nAGENT_ROLE=dev-fix-agent\nREQ={{ $json.params.reqId }}\nROUND={{ $json.params.round }}\nREASON={{ $json.params.reason }}\nSOURCE_ISSUE={{ $json.params.sourceIssueId }}\nBRANCH_BASE={{ $json.params.branch }}\nBRANCH_WORK=stage/bugfix-dev-{{ $json.params.reqId }}-round-{{ $json.params.round }}\n\n## 职责\n- 诊断 BUG 类型 (CODE / TEST / SPEC)\n- 只能改业务代码 (internal/ cmd/ main/)，禁改 tests/, openspec/\n- 诊断到 TEST BUG: 加 tag diagnosis:test-bug 直接 move review (交 test-fix-agent)\n- 诊断到 SPEC BUG: 加 tag diagnosis:spec-bug 直接 move review (走 Escalate)\n- CODE BUG: 从 $BRANCH_BASE 拉 $BRANCH_WORK 子分支, 改代码, commit, push, 不 merge 到 feat\n\n## 硬规则\n- 禁 commit 到 feat/* (reviewer 才能 merge)\n- 禁加 result:* tag\n- pre-commit 会拦 tests/ 和 openspec/ 的改动',
        31),
    rpc_id=31, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "bug_st", "[BUG] St", 2240, y,
    update_issue_body(
        '{{ $node["[BUG] Id"].json.iid }}',
        ['bugfix', '{{ $node["[BUG] Id"].json.params.reqId }}', 'round-{{ $node["[BUG] Id"].json.params.round }}'],
        32, status_id='working'),
    rpc_id=32, timeout=10000, on_error="continueRegularOutput",
))
conns["[BUG] Cr"] = {"main": [[{"node": "[BUG] Id", "type": "main", "index": 0}]]}
conns["[BUG] Id"] = {"main": [[{"node": "[BUG] Fu", "type": "main", "index": 0}]]}
conns["[BUG] Fu"] = {"main": [[{"node": "[BUG] St", "type": "main", "index": 0}]]}

# === Action: create_test_fix =================================================
y = 680
nodes.append(http_node(
    "tfix_cr", "[TFIX] Cr", 1580, y,
    create_issue_body(
        '[{{ $json.params.reqId }}] [TEST-FIX round-{{ $json.params.round }}] adversarial test review',
        ['test-fix', '{{ $json.params.reqId }}', 'round-{{ $json.params.round }}'],
        40),
    rpc_id=40, retry=True, on_error="continueErrorOutput",
))
nodes.append(code_node("tfix_id", "[TFIX] Id", 1800, y, ID_EXTRACT.replace("{PREV}", "[TFIX] Cr")))
nodes.append(http_node(
    "tfix_fu", "[TFIX] Fu", 2020, y,
    follow_up_body(
        '{{ $json.iid }}',
        '## TEST FIX\nAGENT_ROLE=test-fix-agent\nREQ={{ $json.params.reqId }}\nROUND={{ $json.params.round }}\nPREV_BUGFIX={{ $json.params.sourceIssueId }}\nBRANCH_WORK=stage/bugfix-test-{{ $json.params.reqId }}-round-{{ $json.params.round }}\n\n## 职责\n从 feat/$REQ 拉 $BRANCH_WORK 子分支, 以 test 视角审视测试, 改 tests/ 下文件。即使 dev-fix 判了 CODE BUG, 你也要独立审一次, 对抗验证。\n\n## 权限\n- 可写: tests/contract/ tests/acceptance/ tests/ui/ tests/mobile/\n- 禁写: internal/ cmd/ openspec/ migrations/\n\n## 读 dev-fix 的 diagnosis\n看 sourceIssueId 的 tags:\n- diagnosis:test-bug → 重点修 test\n- 无 → 审一下 test 有没有问题, 无改动就 no-op commit + move review\n\n## 硬规则\n- 禁 merge 到 feat/*\n- 禁加 result:* tag',
        41),
    rpc_id=41, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "tfix_st", "[TFIX] St", 2240, y,
    update_issue_body(
        '{{ $node["[TFIX] Id"].json.iid }}',
        ['test-fix', '{{ $node["[TFIX] Id"].json.params.reqId }}', 'round-{{ $node["[TFIX] Id"].json.params.round }}'],
        42, status_id='working'),
    rpc_id=42, timeout=10000, on_error="continueRegularOutput",
))
conns["[TFIX] Cr"] = {"main": [[{"node": "[TFIX] Id", "type": "main", "index": 0}]]}
conns["[TFIX] Id"] = {"main": [[{"node": "[TFIX] Fu", "type": "main", "index": 0}]]}
conns["[TFIX] Fu"] = {"main": [[{"node": "[TFIX] St", "type": "main", "index": 0}]]}

# === Action: create_reviewer =================================================
y = 820
nodes.append(http_node(
    "rvw_cr", "[RVW] Cr", 1580, y,
    create_issue_body(
        '[{{ $json.params.reqId }}] [REVIEWER round-{{ $json.params.round }}] pick winner',
        ['reviewer', '{{ $json.params.reqId }}', 'round-{{ $json.params.round }}'],
        50),
    rpc_id=50, retry=True, on_error="continueErrorOutput",
))
nodes.append(code_node("rvw_id", "[RVW] Id", 1800, y, ID_EXTRACT.replace("{PREV}", "[RVW] Cr")))
nodes.append(http_node(
    "rvw_fu", "[RVW] Fu", 2020, y,
    follow_up_body(
        '{{ $json.iid }}',
        '## REVIEWER\nAGENT_ROLE=reviewer-agent\nREQ={{ $json.params.reqId }}\nROUND={{ $json.params.round }}\n\n## 职责\n比较两条分支 diff 选胜者 merge 到 feat/$REQ:\n- stage/bugfix-dev-$REQ-round-$ROUND (dev-fix 改的 code)\n- stage/bugfix-test-$REQ-round-$ROUND (test-fix 改的 test)\n\n选边标准:\n1. spec 和 test 对齐 → test 对, 合 test 分支\n2. spec 和 code 对齐 → code 对, 合 dev 分支\n3. spec 本身模糊 → 两边都不合, 加 result:fail + 报告 → Escalate\n4. 看能不能都合 (不冲突): 都合\n\n## 完成时\n- merge 胜者 到 feat/$REQ → push\n- tags 追加 result:pass (合了) 或 result:fail (都没合)\n- move review',
        51),
    rpc_id=51, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "rvw_st", "[RVW] St", 2240, y,
    update_issue_body(
        '{{ $node["[RVW] Id"].json.iid }}',
        ['reviewer', '{{ $node["[RVW] Id"].json.params.reqId }}', 'round-{{ $node["[RVW] Id"].json.params.round }}'],
        52, status_id='working'),
    rpc_id=52, timeout=10000, on_error="continueRegularOutput",
))
conns["[RVW] Cr"] = {"main": [[{"node": "[RVW] Id", "type": "main", "index": 0}]]}
conns["[RVW] Id"] = {"main": [[{"node": "[RVW] Fu", "type": "main", "index": 0}]]}
conns["[RVW] Fu"] = {"main": [[{"node": "[RVW] St", "type": "main", "index": 0}]]}

# === Action: create_accept (AI-QA) ===========================================
# Router params: reqId, sourceIssueId, branch, workdir, repoUrl
y = 880
nodes.append(http_node(
    "acc_cr", "[ACC] Cr", 1580, y,
    create_issue_body(
        '[{{ $json.params.reqId }}] [ACCEPT] AI-QA',
        ['accept', '{{ $json.params.reqId }}'],
        60),
    rpc_id=60, retry=True, on_error="continueErrorOutput",
))
nodes.append(code_node("acc_id", "[ACC] Id", 1800, y, ID_EXTRACT.replace("{PREV}", "[ACC] Cr")))
nodes.append(http_node(
    "acc_fu", "[ACC] Fu", 2020, y,
    follow_up_body(
        "{{ $json.iid }}",
        "## 工具白名单 (HARD CONSTRAINT — 第一优先级)\n"
        "仅允许: mcp__bkd__* / mcp__aissh-tao__*\n"
        "绝对禁止: mcp__vibe_kanban__* / mcp__erpnext__* / Task / Agent / 其他未列出 MCP\n"
        "\n─────────\n"
        "## 验收 (ACCEPT / AI-QA)\n"
        "AGENT_ROLE=accept-agent\n"
        "REQ={{ $json.params.reqId }}\n"
        "BRANCH={{ $json.params.branch }}\n"
        "WORKDIR={{ $json.params.workdir }}\n"
        "REPO_URL={{ $json.params.repoUrl }}\n"
        "\n"
        "## 职责\n"
        "你是 AI-QA。读 openspec/changes/$REQ/specs/*/spec.md 里标 FEATURE-A* 的 Acceptance Scenario，\n"
        "在调试环境跑一遍产品验证场景（非测试代码，而是用户视角的行为），给出 pass/fail 判定。\n"
        "\n"
        "## 硬约束\n"
        "1. 所有命令只能通过 mcp__aissh-tao__exec_run 在 vm-node04 上执行。\n"
        "2. 每条 exec_run 以 cd $WORKDIR 开头。\n"
        "3. 禁改 repo 任何文件（只做读 + 部署 + 调用）。\n"
        "4. update-issue issueId 必须是本 issue（不是 ci issue 或父 issue）。\n"
        "\n"
        "## 步骤\n"
        "Step 1 bootstrap（首次 clone, 已存在 reset）:\n"
        "  git clone --branch $BRANCH $REPO_URL $WORKDIR\n"
        "  或 cd $WORKDIR && git fetch origin && git reset --hard origin/$BRANCH\n"
        "\n"
        "Step 2 构建 + 本地部署（AI-QA 侧，不是线上）:\n"
        "  cd $WORKDIR && make ci-build 2>&1\n"
        "  选一种方式启后台: ./bin/<main> & 或 make run & (看 Makefile 约定)\n"
        "\n"
        "Step 3 跑所有 FEATURE-A* Acceptance Scenario:\n"
        "  对每个场景用 curl / CLI / 真实 IO 走一遍 Given/When/Then。\n"
        "  记录每个场景: name / pass-or-fail / evidence (curl 输出 + 状态码 / 文件内容 / ...)\n"
        "\n"
        "Step 4 stop 后台服务 (kill / docker stop)。\n"
        "\n"
        "Step 5 写结果到**本 issue**:\n"
        "  A. follow-up-issue 追加 `## Accept Result` block: 每场景 name/result/evidence\n"
        "  B. update-issue tags: [accept, $REQ, <result:pass 或 result:fail>]\n"
        "  C. update-issue statusId=review",
        61),
    rpc_id=61, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "acc_st", "[ACC] St", 2240, y,
    update_issue_body(
        '{{ $node["[ACC] Id"].json.iid }}',
        ['accept', '{{ $node["[ACC] Id"].json.params.reqId }}'],
        62, status_id='working'),
    rpc_id=62, timeout=10000, on_error="continueRegularOutput",
))
conns["[ACC] Cr"] = {"main": [[{"node": "[ACC] Id", "type": "main", "index": 0}]]}
conns["[ACC] Id"] = {"main": [[{"node": "[ACC] Fu", "type": "main", "index": 0}]]}
conns["[ACC] Fu"] = {"main": [[{"node": "[ACC] St", "type": "main", "index": 0}]]}

# === Action: open_github_issue ===============================================
# ci-integration fail (spec/test/unknown diag) 和 accept fail 都走这里。
# 让一个 agent 用 gh CLI 在 repo 开 issue，把 BKD 里的 CI/Accept Result 转贴过去，
# 让 repo owner 评审决定 spec 改动还是 code 改动，不再 auto-bugfix。
y = 1040
nodes.append(http_node(
    "gh_cr", "[GH] Cr", 1580, y,
    create_issue_body(
        '[{{ $json.params.reqId }}] [GH-ISSUE] {{ $json.params.kind }}',
        ['github-incident', '{{ $json.params.reqId }}', 'kind:{{ $json.params.kind }}'],
        80),
    rpc_id=80, retry=True, on_error="continueErrorOutput",
))
nodes.append(code_node("gh_id", "[GH] Id", 1800, y, ID_EXTRACT.replace("{PREV}", "[GH] Cr")))
nodes.append(http_node(
    "gh_fu", "[GH] Fu", 2020, y,
    follow_up_body(
        "{{ $json.iid }}",
        "## GitHub 工单 (GH-ESCALATOR)\n"
        "AGENT_ROLE=gh-escalator-agent\n"
        "REQ={{ $json.params.reqId }}\n"
        "KIND={{ $json.params.kind }}         # ci-integration-fail | accept-fail\n"
        "DIAGNOSIS={{ $json.params.diagnosis }}   # code-bug | test-bug | spec-bug | unknown (hint)\n"
        "SRC_ISSUE={{ $json.params.sourceIssueId }}\n"
        "BRANCH={{ $json.params.branch }}\n"
        "WORKDIR={{ $json.params.workdir }}\n"
        "REPO_URL={{ $json.params.repoUrl }}\n"
        "INCIDENT_KEY={{ $json.params.incidentKey }}   # 例: REQ-686:ci-integration-fail\n"
        "\n"
        "## 职责\n"
        "契约测试/验收测试是 LOCKED 边界，fail 一律需要人工判定。你的活：\n"
        "1. 若该 REQ+KIND 已有**开着的** GitHub issue → **comment** 新上下文到它；\n"
        "2. 否则 → **create** 新 issue。\n"
        "去重靠 label `sisyphus/incident:$INCIDENT_KEY`。新 issue 必须加这个 label。\n"
        "\n"
        "## 步骤\n"
        "Step 1 读 SRC_ISSUE (mcp__bkd__get-issue $SRC_ISSUE)，提取 `## CI Result` 或 `## Accept Result` 块。\n"
        "\n"
        "Step 2 确定 repo slug（仅一次）：\n"
        "  cd $WORKDIR && SLUG=$(git remote get-url origin | sed -E 's#.*[:/]([^/]+/[^/.]+)(\\\\.git)?$#\\\\1#')\n"
        "\n"
        "Step 3 搜重复（必须做，不做就违反 user policy）：\n"
        "  gh issue list --repo $SLUG --state open --label \"sisyphus/incident:$INCIDENT_KEY\" --json number,title,url --limit 1\n"
        "  → 若有匹配记录，记 EXISTING_URL / EXISTING_NUMBER，跳到 Step 4b；\n"
        "  → 否则跳 Step 4a。\n"
        "\n"
        "Step 4a **create**（首次）：\n"
        "  gh label create \"sisyphus/incident:$INCIDENT_KEY\" --color ededed --description \"sisyphus auto-filed\" --repo $SLUG --force\n"
        "  gh issue create --repo $SLUG \\\n"
        "    --title \"$REQ: $KIND needs human review\" \\\n"
        "    --label \"sisyphus/incident:$INCIDENT_KEY\" \\\n"
        "    --body \"$(cat <<EOF\n"
        "$REQ 在 sisyphus 无人值守链路中卡在 **$KIND**。契约/验收测试是 LOCKED 边界，AI 不会自改。\n"
        "请人工判定：\n"
        "- 代码错 → 合 fix 到 $BRANCH，关闭本 issue 即可让 sisyphus 重跑。\n"
        "- spec/test 本身错 → 改 openspec/changes/$REQ/specs/ 或对应 test，重新跑 sisyphus。\n"
        "\n"
        "### 上下文\n"
        "- BKD incident issue: $SRC_ISSUE\n"
        "- 分支: $BRANCH\n"
        "- 机械诊断 hint: $DIAGNOSIS\n"
        "- dedup key (label): sisyphus/incident:$INCIDENT_KEY\n"
        "\n"
        "### 首次失败详情\n"
        "<贴 ## CI Result 或 ## Accept Result>\n"
        "EOF\n"
        ")\"\n"
        "  记录 URL + number。\n"
        "\n"
        "Step 4b **comment**（重发）：\n"
        "  gh issue comment $EXISTING_NUMBER --repo $SLUG --body \"$(cat <<EOF\n"
        "同一 incident 再次触发（$(date -Iseconds)）。\n"
        "- BKD incident issue: $SRC_ISSUE\n"
        "- 机械诊断 hint: $DIAGNOSIS\n"
        "\n"
        "### 本次失败详情\n"
        "<贴本次 ## CI Result 或 ## Accept Result>\n"
        "EOF\n"
        ")\"\n"
        "\n"
        "Step 5 回写到**本 BKD issue**（issueId 是你自己，不是 SRC_ISSUE）：\n"
        "  A. follow-up-issue 追加 `## GH Issue`: action=create|comment / url / number / timestamp\n"
        "  B. update-issue tags: 先 get-issue 取当前 tags，merge: [github-incident, $REQ, kind:$KIND, gh:<number>, action:<create|comment>]\n"
        "  C. update-issue statusId=review",
        81),
    rpc_id=81, timeout=60000, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "gh_st", "[GH] St", 2240, y,
    update_issue_body(
        '{{ $node["[GH] Id"].json.iid }}',
        ['github-incident', '{{ $node["[GH] Id"].json.params.reqId }}', 'kind:{{ $node["[GH] Id"].json.params.kind }}'],
        82, status_id='working'),
    rpc_id=82, timeout=10000, on_error="continueRegularOutput",
))
conns["[GH] Cr"] = {"main": [[{"node": "[GH] Id", "type": "main", "index": 0}]]}
conns["[GH] Id"] = {"main": [[{"node": "[GH] Fu", "type": "main", "index": 0}]]}
conns["[GH] Fu"] = {"main": [[{"node": "[GH] St", "type": "main", "index": 0}]]}
conns["[GH] St"] = {"main": [[{"node": "[GH Bugfix List]", "type": "main", "index": 0}]]}

# ─── [GH] → BKD bugfix gate ──────────────────────────────────────────────
# After GH issue created/commented, decide whether to also trigger BKD bugfix chain.
# Policy (user-confirmed):
#   - every ci-integration/accept fail → GH issue (done above)
#   - additionally BKD bugfix unless round > CB_THRESHOLD (熔断，只留 GH)
#   - round count = existing BUGFIX+REQ issues + 1
# Independent of Router Dispatch; runs as a post-step after [GH] St.
y = y + 140  # below [GH] row
nodes.append(http_node(
    "gh_bug_list", "[GH Bugfix List]", 1580, y,
    ('={"jsonrpc":"2.0","id":85,"method":"tools/call","params":{"name":"list-issues","arguments":'
     '{"projectId":"{{ $node["[ENTRY] Ctx 提取"].json.projectId }}","limit":200}}}'),
    rpc_id=85, timeout=10000, on_error="continueRegularOutput",
))
nodes.append(code_node(
    "gh_bug_gate", "[GH Bugfix Gate]", 1800, y,
    """// Count existing BUGFIX issues for this REQ to decide next round + 熔断.
const raw = $node['[GH Bugfix List]'].json;
const src = raw && (raw.data || raw.body || raw);
const text = typeof src === 'string' ? src : String(src);
const m = text.match(/data:\\s*(\\{[\\s\\S]*?\\})\\s*$/m);
let issues = [];
if (m) {
  try {
    const env = JSON.parse(m[1]);
    const c = env.result && Array.isArray(env.result.content) ? env.result.content[0] : null;
    if (c && typeof c.text === 'string') issues = JSON.parse(c.text);
  } catch {}
}
const routerParams = $node['Router'].json.params;
const reqId = routerParams.reqId;
const existing = (Array.isArray(issues) ? issues : []).filter(i =>
  Array.isArray(i.tags) && i.tags.includes('bugfix') && i.tags.includes(reqId));
const round = existing.length + 1;
const CB = 3; // circuit-breaker threshold; matches router.js CB_THRESHOLD
const shouldBugfix = round <= CB;
// Carry context needed by [BUG] Cr downstream, shaped like Router's create_bugfix params.
return [{ json: {
  shouldBugfix,
  round,
  cbThreshold: CB,
  existingCount: existing.length,
  // Expose under params.* so [BUG] Cr's existing expressions ($json.params.*) still work.
  params: {
    reqId,
    round,
    sourceIssueId: routerParams.sourceIssueId || routerParams.issueId,
    reason: (routerParams.kind || 'gh-escalated') + ' round-' + round,
    branch: routerParams.branch || ('feat/' + reqId),
  },
}}];
"""
))
nodes.append({
    "id": "gh_bug_if", "name": "[GH Bugfix If]",
    "type": "n8n-nodes-base.if", "typeVersion": 2.2,
    "position": [2020, y],
    "parameters": {
        "conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose"},
            "combinator": "and",
            "conditions": [{
                "operator": {"type": "boolean", "operation": "true"},
                "leftValue": "={{ $json.shouldBugfix }}",
                "rightValue": True,
            }],
        },
    },
})
conns["[GH Bugfix List]"] = {"main": [[{"node": "[GH Bugfix Gate]", "type": "main", "index": 0}]]}
conns["[GH Bugfix Gate]"] = {"main": [[{"node": "[GH Bugfix If]", "type": "main", "index": 0}]]}
# If branch: true → start BUGFIX chain; false → terminal (circuit-breaker, GH issue alone)
conns["[GH Bugfix If]"] = {
    "main": [
        [{"node": "[BUG] Cr", "type": "main", "index": 0}],
        [{"node": "[A] skip", "type": "main", "index": 0}],
    ],
}

# === Action: done_archive ====================================================
# Accept pass → openspec apply + gh pr create + mark analyze parent done
y = 960
nodes.append(http_node(
    "done_cr", "[DONE] Cr", 1580, y,
    create_issue_body(
        '[{{ $json.params.reqId }}] [DONE] archive & PR',
        ['done-archive', '{{ $json.params.reqId }}'],
        70),
    rpc_id=70, retry=True, on_error="continueErrorOutput",
))
nodes.append(code_node("done_id", "[DONE] Id", 1800, y, ID_EXTRACT.replace("{PREV}", "[DONE] Cr")))
nodes.append(http_node(
    "done_fu", "[DONE] Fu", 2020, y,
    follow_up_body(
        "{{ $json.iid }}",
        "## 工具白名单 (HARD CONSTRAINT — 第一优先级)\n"
        "仅允许: mcp__bkd__* / mcp__aissh-tao__*\n"
        "绝对禁止: mcp__vibe_kanban__* / mcp__erpnext__* / Task / Agent / 其他未列出 MCP\n"
        "\n─────────\n"
        "## 归档 (DONE ARCHIVE)\n"
        "AGENT_ROLE=done-archive-agent\n"
        "REQ={{ $json.params.reqId }}\n"
        "BRANCH={{ $json.params.branch }}\n"
        "WORKDIR={{ $json.params.workdir }}\n"
        "REPO_URL={{ $json.params.repoUrl }}\n"
        "ACCEPT_ISSUE={{ $json.params.acceptIssueId }}\n"
        "\n"
        "## 职责\n"
        "验收通过后，把 $REQ 的变更正式固化：openspec apply + 创建 PR。\n"
        "\n"
        "## 硬约束\n"
        "- **update-issue / follow-up-issue 的 issueId 只能是本 issue (即你自己 session 所在的 issue)，\n"
        "  绝对不能是 ACCEPT_ISSUE**。ACCEPT_ISSUE 只是上下文参考。\n"
        "- 所有 shell 命令通过 mcp__aissh-tao__exec_run 在 vm-node04 上执行。\n"
        "\n"
        "## 步骤\n"
        "Step 1 bootstrap:\n"
        "  cd $WORKDIR && git fetch origin && git reset --hard origin/$BRANCH\n"
        "\n"
        "Step 2 openspec apply:\n"
        "  cd $WORKDIR && openspec apply $REQ\n"
        "  这会把 changes/$REQ/specs/* 里的 ADDED 块合并进 openspec/specs/*，\n"
        "  删除 changes/$REQ 目录。produces a new commit。\n"
        "  失败就贴 error 到本 issue description，加 tag archive-fail 并 move review。\n"
        "\n"
        "Step 3 push:\n"
        "  cd $WORKDIR && git push origin $BRANCH\n"
        "\n"
        "Step 4 创 PR:\n"
        "  cd $WORKDIR && gh pr create --base master --head $BRANCH \\\n"
        "    --title \"$REQ: <一句话概括>\" --body \"$(cat openspec/changes/$REQ/proposal.md 2>/dev/null || echo done via sisyphus)\"\n"
        "  记录 PR URL。\n"
        "\n"
        "Step 5 写结果到**本 issue**:\n"
        "  A. follow-up-issue 追加 `## Archive Result`: pr_url / commit_sha\n"
        "  B. update-issue tags: [done-archive, $REQ, result:pass, pr:<url>]\n"
        "  C. update-issue statusId=done  # 归档完成直接 done，不 review\n",
        71),
    rpc_id=71, timeout=60000, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "done_st", "[DONE] St", 2240, y,
    update_issue_body(
        '{{ $node["[DONE] Id"].json.iid }}',
        ['done-archive', '{{ $node["[DONE] Id"].json.params.reqId }}'],
        72, status_id='working'),
    rpc_id=72, timeout=10000, on_error="continueRegularOutput",
))
conns["[DONE] Cr"] = {"main": [[{"node": "[DONE] Id", "type": "main", "index": 0}]]}
conns["[DONE] Id"] = {"main": [[{"node": "[DONE] Fu", "type": "main", "index": 0}]]}
conns["[DONE] Fu"] = {"main": [[{"node": "[DONE] St", "type": "main", "index": 0}]]}

# === Action: fanout_specs ====================================================
# Uses n8n Split Out: expands params.specs[] into N items, each going through Cr/Fu/St
y = 960
nodes.append({
    "id": "fan_split", "name": "[FAN] Split specs",
    "type": "n8n-nodes-base.splitOut", "typeVersion": 1,
    "position": [1580, y],
    "parameters": {"fieldToSplitOut": "params.specs", "options": {"destinationFieldName": "specStage"}},
})
# After Split Out, $node[X] fails auto-pairing; use $(X).first() absolute ref
nodes.append(http_node(
    "fan_cr", "[FAN] Cr", 1800, y,
    create_issue_body(
        '[{{ $(\'Router\').first().json.params.reqId }}] [{{ $json.specStage }}]',
        ['{{ $json.specStage }}', '{{ $(\'Router\').first().json.params.reqId }}'],
        60),
    rpc_id=60, retry=True, on_error="continueErrorOutput",
))
# Also override the projectId expression for the FAN Cr/Fu/St to use .first()
# (done inline below by post-fixing the expressions)
nodes.append(code_node(
    "fan_id", "[FAN] Id", 2020, y,
    """// After [FAN] Cr: N items (one per spec). Extract iid from each; recover specStage from Split Out by index.
const crItems = $input.all();
const splitItems = $('[FAN] Split specs').all();
const reqId = $('Router').first().json.params.reqId;
return crItems.map((item, idx) => {
  const raw = item.json;
  const text = String((raw && (raw.data || raw.body)) || raw || '');
  const m = text.match(/data:\\s*(\\{[\\s\\S]*?\\})\\s*$/m);
  let iid = '';
  if (m) {
    try {
      const env = JSON.parse(m[1]);
      const c = env.result && Array.isArray(env.result.content) ? env.result.content[0] : null;
      if (c && typeof c.text === 'string') iid = (JSON.parse(c.text).id) || '';
    } catch {}
  }
  const srcItem = splitItems[idx];
  const specStage = (srcItem && srcItem.json && srcItem.json.specStage) || '';
  return { json: { specStage, iid, reqId } };
});
"""
))
nodes.append(http_node(
    "fan_fu", "[FAN] Fu", 2240, y,
    follow_up_body(
        '{{ $json.iid }}',
        "## SPEC ({{ $json.specStage }})\n"
        "AGENT_ROLE={{ $json.specStage }}-agent\n"
        "REQ={{ $json.reqId }}\n"
        "\n"
        "## 你的任务（按 specStage 对号入座）\n"
        "\n"
        "### 若 specStage == contract-test\n"
        "你是 **contract-test-agent**，负责所有跨部件 LOCKED 约定的测试层。\n"
        "\n"
        "**读**：\n"
        "- openspec/changes/$REQ/proposal.md（看影响范围）\n"
        "- openspec/changes/$REQ/design.md（看接口形态）\n"
        "- openspec/changes/$REQ/tasks.md 的 `## Stage: contract-tests` section（骨架 TODO）\n"
        "\n"
        "**根据 proposal 涉及的形态，挑一种或多种写**：\n"
        "- 若涉及 **REST API**：产 `openspec/changes/$REQ/contract.spec.yaml`（OpenAPI）+ `tests/contract/*.go`\n"
        "- 若涉及 **UI 组件契约**：产 `tests/ui/*`（props 快照 / 可达性 断言）\n"
        "- 若涉及 **DB schema**：产 `migrations/*.sql` + 验证 SQL\n"
        "- 以上都涉及就都写（可以同时）\n"
        "- 都不涉及（罕见，纯重构）：在 tasks.md section 里写 `- [x] no contract changes` + ci-passed\n"
        "\n"
        "**写 specs（LOCKED 契约描述）**：\n"
        "- openspec/changes/$REQ/specs/<capability>/spec.md 的 ADDED 块，以 `## Scenario: <REQ>-S<N>` 命名\n"
        "- 这是 Router/lint 工具认的格式（scripts/check-scenario-refs.sh 会校验）\n"
        "\n"
        "**填 tasks.md 的 Stage: contract-tests section**（替换 TODO 为实际任务清单），\n"
        "每条 checkbox 可以 `[XXX-S<N>]` 引用 scenario ID（check-scenario-refs.sh 会校验）。\n"
        "\n"
        "**性质**：LOCKED。dev-agent 不得改 contract.spec.yaml / tests/contract/** / migrations/**。\n"
        "pre-commit ACL 已配好 `contract-test-agent` 的可写范围。\n"
        "\n"
        "### 若 specStage == accept-test\n"
        "你是 **accept-test-agent**，负责用户视角端到端验收。\n"
        "\n"
        "**读**：proposal.md + design.md + tasks.md 的 `## Stage: acceptance-tests` section\n"
        "\n"
        "**产**：\n"
        "- openspec/changes/$REQ/specs/<capability>/spec.md 的 ADDED 块，Scenario 以 `FEATURE-A<N>` 命名\n"
        "  每条 Given/When/Then 格式，**用户视角**（不关心代码层）\n"
        "- 可选：`tests/acceptance/*`（若有 E2E 自动化工具）\n"
        "\n"
        "**填 tasks.md 的 Stage: acceptance-tests section**（替换 TODO 为真实场景清单）\n"
        "\n"
        "**性质**：LOCKED。accept-agent 在验收阶段按这个跑。\n"
        "\n"
        "## 硬规则（所有 specStage 通用）\n"
        "- **只改自己 Stage section 对应的 tasks.md checkbox**，check-tasks-section-ownership.sh 会校验 diff\n"
        "- **不改** proposal.md / design.md / 其他 agent 的 Stage section\n"
        "- **不写** openspec/specs/**（那是 openspec apply 归档后的位置，归 analyze+done 阶段）\n"
        "- pre-commit ACL 会拦越权；违反即任务失败\n"
        "\n"
        "## 完成时\n"
        "- commit + push 到 feat/$REQ（子分支可选，直接 push feat/$REQ 也行）\n"
        "- update-issue tags 保留 {{ $json.specStage }} + $REQ\n"
        "- move review —— **不加** result:* tag；由下游 CI-lint gate 通过后 n8n 自动推进。",
        61),
    rpc_id=61, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "fan_st", "[FAN] St", 2460, y,
    update_issue_body(
        # Fu returned HTTP response (no iid) — reach back to [FAN] Id via pairedItem
        "{{ $('[FAN] Id').item.json.iid }}",
        ["{{ $('[FAN] Id').item.json.specStage }}", "{{ $('[FAN] Id').item.json.reqId }}"],
        62, status_id='working'),
    rpc_id=62, timeout=10000, on_error="continueRegularOutput",
))
# Mark the analyze parent issue as done so subsequent webhooks get skipped
# by Router's `priorStatusId === 'done'` gate (prevents repeated fanout).
nodes.append(http_node(
    "fan_done", "[FAN] Mark analyze done", 2700, y,
    ('={"jsonrpc":"2.0","id":63,"method":"tools/call","params":{"name":"update-issue","arguments":'
     '{"projectId":"{{ $(\'[ENTRY] Ctx 提取\').first().json.projectId }}",'
     '"issueId":"{{ $(\'[ENTRY] Ctx 提取\').first().json.issueId }}",'
     '"statusId":"done"}}}'),
    rpc_id=63, timeout=10000, on_error="continueRegularOutput",
))
conns["[FAN] Split specs"] = {"main": [[{"node": "[FAN] Cr", "type": "main", "index": 0}]]}
conns["[FAN] Cr"] = {"main": [[{"node": "[FAN] Id", "type": "main", "index": 0}]]}
conns["[FAN] Id"] = {"main": [[{"node": "[FAN] Fu", "type": "main", "index": 0}]]}
conns["[FAN] Fu"] = {"main": [[{"node": "[FAN] St", "type": "main", "index": 0}]]}
conns["[FAN] St"] = {"main": [[{"node": "[FAN] Mark analyze done", "type": "main", "index": 0}]]}

# Patch FAN nodes: $node["[ENTRY] Ctx 提取"] -> $('[ENTRY] Ctx 提取').first()
# (Split Out breaks auto-pairing for $node refs on both body and headers)
for n in nodes:
    if n["name"].startswith("[FAN]") and n["type"] == "n8n-nodes-base.httpRequest":
        body = n["parameters"].get("jsonBody", "")
        body = body.replace(
            '$node["[ENTRY] Ctx 提取"]',
            '$(\'[ENTRY] Ctx 提取\').first()',
        )
        n["parameters"]["jsonBody"] = body
        for hp in n["parameters"].get("headerParameters", {}).get("parameters", []):
            hp["value"] = hp["value"].replace(
                '$node["[ENTRY] Ctx 提取"]',
                '$(\'[ENTRY] Ctx 提取\').first()',
            )

# === Action: mark_spec_reviewed ==============================================
y = 1100
nodes.append(http_node(
    "spg_mark", "[SPG] Mark spec-reviewed", 1580, y,
    update_issue_body(
        '{{ $json.params.parentIssueId }}',
        ['{{ $json.params.specStage }}', '{{ $json.params.reqId }}', 'ci-passed'],
        70),
    rpc_id=70, timeout=10000, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "spg_list", "[SPG] List REQ specs", 1800, y,
    ('={"jsonrpc":"2.0","id":71,"method":"tools/call","params":{"name":"list-issues","arguments":'
     '{"projectId":"{{ $node["[ENTRY] Ctx 提取"].json.projectId }}","limit":200}}}'),
    rpc_id=71, timeout=10000, on_error="continueRegularOutput",
))
nodes.append(code_node(
    "spg_gate", "[SPG] Gate check", 2020, y,
    """// Check whether all expected specs for this REQ have `ci-passed` tag.
// If yes, emit {allReady: true, reqId, repoUrl}. Else skip.
const raw = $node['[SPG] List REQ specs'].json;
const src = raw && (raw.data || raw.body || raw);
const text = typeof src === 'string' ? src : String(src);
const m = text.match(/data:\\s*(\\{[\\s\\S]*?\\})\\s*$/m);
if (!m) return [{ json: { allReady: false, reason: 'bad list SSE' } }];
let issues = [];
try {
  const env = JSON.parse(m[1]);
  const c = env.result && Array.isArray(env.result.content) ? env.result.content[0] : null;
  if (c && typeof c.text === 'string') issues = JSON.parse(c.text);
} catch {}
if (!Array.isArray(issues)) return [{ json: { allReady: false, reason: 'list parse err' } }];

const routerParams = $node['Router'].json.params;
const reqId = routerParams.reqId;
// Infer expected specs from analyze layers — but we don't have layers here.
// MVP: look at current ci-passed specs for this REQ and check we have all known spec kinds.
// Simple heuristic: if >=2 spec issues ci-passed (dev-spec + accept-spec at minimum), gate open.
const reqSpecs = issues.filter(i => Array.isArray(i.tags) && i.tags.includes(reqId));
const passed = reqSpecs.filter(i => i.tags.includes('ci-passed'));
const expectedCount = 2; // fan-out 两路: contract-test + accept-test
// Idempotency gate: if a DEV issue already exists for this REQ, don't open again.
// Without this, every spec re-entry (mark_spec_reviewed replays) re-triggers dev creation.
const reqDevs = issues.filter(i => Array.isArray(i.tags) && i.tags.includes('dev') && i.tags.includes(reqId));
const allReady = passed.length >= expectedCount && reqDevs.length === 0;
return [{ json: {
  allReady,
  reqId,
  passedCount: passed.length,
  expectedCount,
  params: { reqId, branch: 'stage/' + reqId + '-dev', workdir: '/var/sisyphus-ci/stage-' + reqId + '-dev', repoUrl: routerParams.repoUrl || null }
}}];
"""
))
# IF branch: allReady == true → create dev issue (via inline Cr/Fu/St)
nodes.append({
    "id": "spg_if", "name": "[SPG] If gate open",
    "type": "n8n-nodes-base.if", "typeVersion": 2.2,
    "position": [2240, y],
    "parameters": {
        "conditions": {
            "options": {"caseSensitive": True, "typeValidation": "loose"},
            "combinator": "and",
            "conditions": [{
                "operator": {"type": "boolean", "operation": "true"},
                "leftValue": "={{ $json.allReady }}",
                "rightValue": True,
            }],
        },
    },
})
nodes.append(http_node(
    "spg_dev_cr", "[SPG] Dev Cr", 2460, y-60,
    create_issue_body(
        '[{{ $json.reqId }}] [DEV]',
        ['dev', '{{ $json.reqId }}'],
        72),
    rpc_id=72, retry=True, on_error="continueErrorOutput",
))
nodes.append(code_node("spg_dev_id", "[SPG] Dev Id", 2680, y-60, ID_EXTRACT.replace("{PREV}", "[SPG] Dev Cr").replace("$node['Router']", "$node['[SPG] Gate check']")))
nodes.append(http_node(
    "spg_dev_fu", "[SPG] Dev Fu", 2900, y-60,
    follow_up_body(
        "{{ $json.iid }}",
        "## 开发 (DEV)\n"
        "AGENT_ROLE=dev-agent\n"
        "REQ={{ $json.reqId }}\n"
        "\n"
        "前置：contract-test-agent 和 accept-test-agent 已产出契约/验收 spec + 测试骨架。\n"
        "你负责实现业务代码 + unit test，让上游契约和验收测试全部跑绿。\n"
        "\n"
        "## 步骤\n"
        "1. 读 `openspec/changes/$REQ/`：\n"
        "   - proposal.md + design.md（理解需求和方案）\n"
        "   - contract.spec.yaml（若有，是 API/IO LOCKED 定义）\n"
        "   - specs/<capability>/spec.md（Scenario 列表，LOCKED）\n"
        "   - tasks.md 的 `## Stage: implementation` section（待你填细节）\n"
        "2. 从 feat/$REQ 拉 stage/$REQ-dev 子分支\n"
        "3. **填 tasks.md 的 Stage: implementation section**：把 analyze 留的 TODO 换成具体任务清单\n"
        "4. 实现业务代码（internal/ cmd/）+ 同目录 unit test\n"
        "5. 通过 mcp__aissh-tao__exec_run 在 vm-node04 跑 `go vet` / `go build` / `make ci-unit-test` 验证\n"
        "6. ONE 干净 commit + push 到 stage/$REQ-dev\n"
        "7. move review\n"
        "\n"
        "## 禁改（pre-commit ACL 会拦）\n"
        "- tests/contract/** / tests/acceptance/** / tests/ui/**（契约/验收测试 LOCKED）\n"
        "- openspec/changes/$REQ/specs/**（contract/accept Scenario LOCKED）\n"
        "- openspec/changes/$REQ/contract.spec.yaml（LOCKED）\n"
        "- tasks.md 里其他 Stage section（check-tasks-section-ownership.sh 会校验 diff）\n"
        "- openspec/specs/**（是 openspec apply 归档后的长期知识库，归 [DONE] 阶段写）",
        73),
    rpc_id=73, on_error="continueRegularOutput",
))
nodes.append(http_node(
    "spg_dev_st", "[SPG] Dev St", 3120, y-60,
    update_issue_body(
        '{{ $node["[SPG] Dev Id"].json.iid }}',
        ['dev', '{{ $node["[SPG] Dev Id"].json.reqId }}'],
        74, status_id='working'),
    rpc_id=74, timeout=10000, on_error="continueRegularOutput",
))
nodes.append(noop("spg_wait", "[SPG] Gate not yet ready", 2460, y+60))

conns["[SPG] Mark spec-reviewed"] = {"main": [[{"node": "[SPG] List REQ specs", "type": "main", "index": 0}]]}
conns["[SPG] List REQ specs"] = {"main": [[{"node": "[SPG] Gate check", "type": "main", "index": 0}]]}
conns["[SPG] Gate check"] = {"main": [[{"node": "[SPG] If gate open", "type": "main", "index": 0}]]}
conns["[SPG] If gate open"] = {"main": [
    [{"node": "[SPG] Dev Cr", "type": "main", "index": 0}],
    [{"node": "[SPG] Gate not yet ready", "type": "main", "index": 0}],
]}
conns["[SPG] Dev Cr"] = {"main": [[{"node": "[SPG] Dev Id", "type": "main", "index": 0}]]}
conns["[SPG] Dev Id"] = {"main": [[{"node": "[SPG] Dev Fu", "type": "main", "index": 0}]]}
conns["[SPG] Dev Fu"] = {"main": [[{"node": "[SPG] Dev St", "type": "main", "index": 0}]]}

# === Action: escalate ========================================================
y = 1240
nodes.append(http_node(
    "esc_st", "[ESC] St (add escalate tag)", 1580, y,
    update_issue_body(
        '{{ $json.params.issueId || $json.params.reqId }}',
        ['escalated', 'reason:{{ $json.reason }}'],
        80),
    rpc_id=80, timeout=10000, on_error="continueRegularOutput",
))
# Note: update-issue tags replaces the tag list. For MVP we accept overwrite.

# === Fallback: unhandled ========================================================
nodes.append(noop("a_other", "[A] unhandled", 1580, 1340))

# ─── Dispatch Action output connections ────────────────────────────────────
# Switch output order: skip, create_ci_runner, comment_back, create_bugfix,
# create_test_fix, create_reviewer, fanout_specs, mark_spec_reviewed, escalate,
# then fallback
conns["Dispatch Action"] = {"main": [
    [{"node": "[A] skip", "type": "main", "index": 0}],
    [{"node": "[ANZ] Update title+tags", "type": "main", "index": 0}],
    [{"node": "[CI] Cr", "type": "main", "index": 0}],
    [{"node": "[CMT] Fu", "type": "main", "index": 0}],
    [{"node": "[BUG] Cr", "type": "main", "index": 0}],
    [{"node": "[TFIX] Cr", "type": "main", "index": 0}],
    [{"node": "[RVW] Cr", "type": "main", "index": 0}],
    [{"node": "[ACC] Cr", "type": "main", "index": 0}],
    [{"node": "[GH] Cr", "type": "main", "index": 0}],
    [{"node": "[DONE] Cr", "type": "main", "index": 0}],
    [{"node": "[FAN] Split specs", "type": "main", "index": 0}],
    [{"node": "[SPG] Mark spec-reviewed", "type": "main", "index": 0}],
    [{"node": "[ESC] St (add escalate tag)", "type": "main", "index": 0}],
    [{"node": "[A] unhandled", "type": "main", "index": 0}],
]}

# ─── Re-layout: semantic grid ────────────────────────────────────────────
# x grid (columns): entry → router → switch → action steps
COL = {
    'hook':     200,
    'init':     440,
    'get':      680,
    'ctx':      920,
    'router':  1160,
    'switch':  1400,
    'a1':      1680,   # action step 1 (usually Cr or single HTTP)
    'a2':      1920,   # Id / next
    'a3':      2160,   # Fu
    'a4':      2400,   # St
    'a5':      2640,   # extras (SPG has Dev Cr/Id/Fu/St after If)
    'a6':      2880,
    'a7':      3120,
}

# y grid: entries top, actions stacked below Switch
LAYOUT = {
    # Entries (upper band)
    '[ENTRY] Hook':                 (COL['hook'],     200),
    '[ENTRY] Init MCP':             (COL['init'],     200),
    '[ENTRY] Get Issue':            (COL['get'],      200),
    '[ENTRY] Ctx 提取':             (COL['ctx'],      200),
    '[ENTRY intent] Hook':          (COL['hook'],     440),
    '[ENTRY intent] Init MCP':      (COL['init'],     440),
    '[ENTRY intent] Ctx':           (COL['ctx'],      440),

    # Center
    'Router':                       (COL['router'],   320),
    'Dispatch Action':              (COL['switch'],   320),
}

# Action branches: each row = one action, step-nodes fill x=a1..a4
# Row order follows the REQ lifecycle execution sequence (top = earliest stage):
#   ANZ → FAN → SPG → CI → CMT → BUG → TFIX → RVW → ESC (anomaly) → skip/unhandled (noops)
ACTION_ROWS = [
    ('start_analyze',    [('[ANZ] Update title+tags','a1'), ('[ANZ] Send analyze prompt','a2'), ('[ANZ] Trigger agent','a3')]),
    ('fanout_specs',     [('[FAN] Split specs','a1'), ('[FAN] Cr','a2'), ('[FAN] Id','a3'), ('[FAN] Fu','a4'), ('[FAN] St','a5'), ('[FAN] Mark analyze done','a6')]),
    ('mark_spec_reviewed', [
        ('[SPG] Mark spec-reviewed','a1'),
        ('[SPG] List REQ specs','a2'),
        ('[SPG] Gate check','a3'),
        ('[SPG] If gate open','a4'),
        # If-true chain (gate open → create dev)
        ('[SPG] Dev Cr','a5'),
        ('[SPG] Dev Id','a6'),
        ('[SPG] Dev Fu','a7'),
    ]),
    ('create_ci_runner', [('[CI] Cr','a1'), ('[CI] Id','a2'), ('[CI] Fu','a3'), ('[CI] St','a4')]),
    ('comment_back',     [('[CMT] Fu','a1'), ('[CMT] St (回 in_progress)','a2')]),
    ('create_bugfix',    [('[BUG] Cr','a1'), ('[BUG] Id','a2'), ('[BUG] Fu','a3'), ('[BUG] St','a4')]),
    ('create_test_fix',  [('[TFIX] Cr','a1'), ('[TFIX] Id','a2'), ('[TFIX] Fu','a3'), ('[TFIX] St','a4')]),
    ('create_reviewer',  [('[RVW] Cr','a1'), ('[RVW] Id','a2'), ('[RVW] Fu','a3'), ('[RVW] St','a4')]),
    ('create_accept',    [('[ACC] Cr','a1'), ('[ACC] Id','a2'), ('[ACC] Fu','a3'), ('[ACC] St','a4')]),
    ('open_github_issue', [('[GH] Cr','a1'), ('[GH] Id','a2'), ('[GH] Fu','a3'), ('[GH] St','a4')]),
    ('done_archive',     [('[DONE] Cr','a1'), ('[DONE] Id','a2'), ('[DONE] Fu','a3'), ('[DONE] St','a4')]),
    ('escalate',         [('[ESC] St (add escalate tag)','a1')]),
    ('skip',             [('[A] skip',              'a1')]),
    ('fallback',         [('[A] unhandled','a1')]),
]

ACTION_ROW_Y_START = 620      # first action row y (below entries + Router/Switch)
ACTION_ROW_SPACING = 180      # vertical gap between action rows

for idx, (name, steps) in enumerate(ACTION_ROWS):
    y = ACTION_ROW_Y_START + idx * ACTION_ROW_SPACING
    for node_name, col_key in steps:
        LAYOUT[node_name] = (COL[col_key], y)

# SPG special case: Dev St extends past a7; If-false (gate not ready) is below
_spg_row_idx = next(i for i, (name, _) in enumerate(ACTION_ROWS) if name == 'mark_spec_reviewed')
_spg_y = ACTION_ROW_Y_START + _spg_row_idx * ACTION_ROW_SPACING
LAYOUT['[SPG] Dev St']             = (COL['a7'] + 240, _spg_y)       # end of If-true chain
LAYOUT['[SPG] Gate not yet ready'] = (COL['a5'],       _spg_y + 80)  # below Dev Cr (If-false branch)

# [GH Bugfix] gate row: below [GH] main row (open_github_issue action)
_gh_row_idx = next(i for i, (name, _) in enumerate(ACTION_ROWS) if name == 'open_github_issue')
_gh_y = ACTION_ROW_Y_START + _gh_row_idx * ACTION_ROW_SPACING
LAYOUT['[GH Bugfix List]'] = (COL['a1'], _gh_y + 100)
LAYOUT['[GH Bugfix Gate]'] = (COL['a2'], _gh_y + 100)
LAYOUT['[GH Bugfix If]']   = (COL['a3'], _gh_y + 100)

# Apply layout
missing = []
for n in nodes:
    pos = LAYOUT.get(n['name'])
    if pos is None:
        missing.append(n['name'])
        continue
    n['position'] = [pos[0], pos[1]]
if missing:
    print(f'⚠ no layout for: {missing}')

# ─── Emit ─────────────────────────────────────────────────────────────────
workflow = {
    "name": "V3.1 BKD Events (Code-node router)",
    "nodes": nodes,
    "connections": conns,
    "settings": {},
    "active": False,
}
open(OUT, 'w').write(json.dumps(workflow, ensure_ascii=False, indent=2))
print(f"✓ wrote {OUT}")
print(f"  nodes: {len(nodes)}")
