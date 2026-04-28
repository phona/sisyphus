## Stage: implementation

- [x] HTTP driver 实现 (thanatos/src/thanatos/drivers/http.py)
  - preflight / observe / act / assert_ / capture_evidence
  - 支持 POST/GET/PUT/PATCH/DELETE + body JSON
  - 支持 response code / body JSONPath 断言
- [x] Playwright driver 实现 (thanatos/src/thanatos/drivers/playwright.py)
  - preflight (chromium launch + navigate)
  - observe (a11y snapshot)
  - act (click / type)
  - assert_ (title / visible / contains)
  - capture_evidence (screenshot + DOM)
- [x] runner.py 补全 execute flow
  - async run_scenario / run_all
  - given → when → then 执行链
  - 失败时自动 capture_evidence
- [x] create_accept.py 恢复 thanatos MCP
  - env-up → 解析 thanatos block → 派 accept-agent
  - 无 thanatos block 时 fallback v0.3-lite
- [x] accept.md.j2 恢复 thanatos MCP 调用指引
- [x] 测试恢复
  - test_http_driver.py (mock httpx)
  - test_runner.py (mock driver flow)
  - 更新 test_contract_thanatos.py THAN-S5/THAN-S5b
  - 更新 orchestrator accept 测试适配 thanatos MCP + fallback

## Stage: PR

- [x] git push feat/REQ-thanatos-m1-impl-1777389456
- [x] gh pr create with sisyphus label
