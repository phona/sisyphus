# 工作流 Prompt 大全

每个阶段的完整 prompt。所有 agent 都可以通过 aissh MCP 访问调试环境（vm-node04）。

## 阶段一：需求分析

```
## 需求分析

Requirement: {title}
Description: {description}

Use /opsx:propose to decompose this requirement.

产出 openspec/changes/{reqId}/：
- proposal.md：做什么、为什么
- specs/：capability 规格（后续验收测试依据）
- design.md：关键设计决策
**不要产出 tasks.md**（实现任务拆解归开发Spec阶段）。

另出 contract.spec.yaml（OpenAPI 3.0+）：
- 所有 endpoint 路径、HTTP 方法
- Request/response schema，含类型与约束
- 状态码与错误响应
- 示例 request/response

这份 openspec 文档是**所有后续 agent（Spec / Dev / Bug Fix）的唯一真相源**。
所有人都靠它理解做什么、为什么。写得清楚、完备、无歧义。
后续任何 Bug Fix 也要回头读这份文档，防止改出奇奇怪怪的东西。

Do NOT write any code or tests.
If requirements are ambiguous, STOP and ask via this issue.
When done, commit all files and move this issue to review.
```

## 阶段二-A：开发 Spec

```
## 开发 Spec

Read openspec artifacts and contract.spec.yaml from the repo.

Write dev.spec.md — a detailed implementation guide including:
- File structure: where to add new files
- Function signatures and their responsibilities
- Dependencies needed
- Error handling strategy
- Edge cases to handle
- Database/storage requirements if any

Write it so the Dev agent does not need to guess anything.
Do NOT write implementation code or test code.
When done, commit dev.spec.md and move to review.
```

## 阶段二-B：契约测试 Spec

```
## 契约测试 Spec

Read contract.spec.yaml from the repo.

Write contract_test.* — executable test code that validates the API against the contract:
- Request format validation (correct schema, required fields, types)
- Response format validation (correct schema, status codes)
- Error response validation (400, 404, 500 etc.)
- Content-Type headers
- Boundary values and edge cases

Use aissh MCP to verify that your test code compiles:
- aissh exec_run: cd to project dir, run compile/lint

Tests should fail when run (no implementation yet) — that is correct.
Do NOT write any business/implementation code.
When done, commit and move to review.
These test files are LOCKED after this stage — no one can modify them.
```

## 阶段二-C：验收测试 Spec

```
## 验收测试 Spec

Read openspec specs/ from the repo.

Write acceptance_test.* — executable end-to-end test code:
- Given/When/Then scenarios
- Happy path: normal usage flows
- Error path: invalid input, not found, etc.
- Edge cases: empty data, large payloads, concurrent access

Use aissh MCP to verify that your test code compiles:
- aissh exec_run: cd to project dir, run compile/lint

Tests should fail when run (no implementation yet) — that is correct.
Do NOT write any business/implementation code.
When done, commit and move to review.
These test files are LOCKED after this stage — no one can modify them.
```

## 阶段三：开发

```
## 开发

Read dev.spec.md for implementation details.
Read contract_test.* and acceptance_test.* to understand what tests you must pass.

Workflow:
1. Read dev.spec.md thoroughly
2. Read all test files to understand what's expected
3. Write the full implementation
4. Write unit tests
5. Use aissh MCP to verify on debug environment (vm-node04):
   - aissh exec_run: L0 — lint/compile
   - aissh exec_run: L1 — unit tests
   - aissh exec_run: L2 — contract tests (contract_test.*)
   - aissh exec_run: L3 — acceptance tests (acceptance_test.*)
6. If any test fails, fix and re-run (iterate locally, don't commit yet)
7. ALL L0-L3 pass → commit once → push → move to review

RULES:
- Do NOT modify contract_test.* or acceptance_test.* (LOCKED)
- Do NOT modify contract.spec.yaml
- Only commit when ALL tests pass
- One clean commit, not many small ones
- Move to review after push
```

## 阶段四：测试验证

```
## 测试验证

You are an independent verifier. You did NOT write any of this code.

Use aissh MCP to verify on the debug environment (vm-node04):

1. aissh exec_run: git pull latest code
2. aissh exec_run: L0 — lint and compile
3. aissh exec_run: L1 — run unit tests
4. aissh exec_run: L2 — run contract tests (contract_test.*)
5. aissh exec_run: L3 — run acceptance tests (acceptance_test.*)

Do NOT modify any code or tests. Only run and report.

Report format — include in your final message:
- L0: PASS/FAIL + output
- L1: PASS/FAIL + output
- L2: PASS/FAIL + output
- L3: PASS/FAIL + output
- Overall: PASS (all green) or FAIL (which layer failed)

If overall PASS, include "PASS" in the issue title before moving to review.
If overall FAIL, include "FAIL" in the issue title before moving to review.

Move to review when done.
```

## Bug Fix

```
## Bug Fix

Previous test verification failed. Failure details:
{fail_info}

**动手前必读**：
- openspec/changes/{reqId}/（proposal.md / specs/ / design.md）—— 本需求的唯一真相源
- dev.spec.md —— 实现指南
搞清楚原本要做什么、设计约束是什么，避免改出奇奇怪怪的东西偏离初衷。

Fix the code to make failing tests pass.

TDD approach — use aissh MCP on debug environment:
1. aissh exec_run: reproduce the failure
2. Read the failing test to understand what's expected
3. Fix the implementation code (遵循 openspec 和 dev.spec.md)
4. aissh exec_run: re-run the failing test
5. Red? Fix more. Green? Run all tests.
6. aissh exec_run: L0-L3 all pass

RULES:
- Do NOT modify contract_test.* or acceptance_test.* (LOCKED)
- Only fix implementation code
- 决策不明时回头看 openspec，不要凭空发挥
- Commit and push when done
- Move to review
```

## 阶段五：验收

```
## 验收

You are an independent acceptance tester with NO context from the development process.

Use aissh MCP to set up and test on debug environment (vm-node04):

1. aissh exec_run: git pull latest code
2. aissh exec_run: build and deploy the service
3. aissh exec_run: run acceptance_test.*
4. Record evidence: actual status codes, response bodies, error messages
5. Generate acceptance report

Do NOT modify any code or tests.

Report format:
- Each test scenario: PASS/FAIL + evidence
- Overall: PASS or FAIL
- If PASS, include "PASS" in issue title
- If FAIL, include "FAIL" in issue title

Move to review when done.
```

## 变量说明

| 变量 | 来源 | 说明 |
|------|------|------|
| {title} | webhook 入参 | 需求标题 |
| {description} | webhook 入参 | 需求描述 |
| {fail_info} | 上一轮测试验证的结果 | Bug Fix 时用 |

## aissh MCP 使用方式

所有 agent 通过 `.mcp.json` 配置自动获得 aissh 工具。可用操作：

```
aissh exec_run:
  server_id: "5b25f0cd-4fef-4a1f-a4c0-14ecf1395d84"  (vm-node04)
  command: "cd /path/to/project && go test ./..."
  reason: "Run L1 unit tests"
```
