# AI 自动化测试快速开始

## 环境准备

```bash
# 确保平台已启动
make status

# 检查 Gitea 运行状态
kubectl get pods -n sisyphus | grep gitea

# 检查 n8n 运行状态  
kubectl get pods -n sisyphus | grep n8n
```

## 完整流程示例

### 前置阶段：接口契约设计

```bash
# 创建需求目录
mkdir -p specs/REQ-16-ai-inventory

# 编写原始需求（PRD）
cat > specs/REQ-16-ai-inventory/prd.md << 'EOF'
# REQ-16: AI 智能库存预警

## 背景
系统需要根据销售数据自动预测库存需求，提前预警缺货风险。

## 功能需求
1. 基于历史销售数据的库存预测
2. 缺货风险等级评估（高/中/低）
3. 自动推荐采购数量
4. 预警通知推送
EOF

# 设计接口契约（后端主导，测试+前端评审）
cat > specs/REQ-16-ai-inventory/contract.spec.yaml << 'EOF'
api_version: "v1"
req_id: "REQ-16"

endpoints:
  - id: "API-01"
    path: "/api/v1/inventory/predict"
    method: "POST"
    description: "库存预测"
    
    request:
      content_type: "application/json"
      schema:
        type: object
        properties:
          product_id:
            type: string
            required: true
          days:
            type: integer
            minimum: 1
            maximum: 90
    
    responses:
      "200":
        description: "预测成功"
        schema:
          type: object
          properties:
            product_id:
              type: string
            predict_qty:
              type: number
            risk_level:
              type: string
              enum: ["high", "medium", "low"]
            suggest_qty:
              type: number
EOF

# 评审通过后，标记契约设计完成
make contract-done REQ_ID=REQ-16
```

### 阶段 1：OpenSpec 拆分

```bash
# 运行 OpenSpec 拆分（AI Agent 执行）
make spec-split REQ_ID=REQ-16
```

**输出文件：**
```
specs/REQ-16-ai-inventory/
├── prd.md                    # 原始需求
├── contract.spec.yaml        # 契约规格（已评审）
├── dev.spec.md               # 开发规格
└── ac.spec.yaml              # 验收规格
```

### 阶段 2：三方并行开发

```bash
# 创建 feature 分支
git checkout -b feature/REQ-16-ai-inventory

# 创建三个并行分支
git checkout -b dev/REQ-16
git checkout -b test/REQ-16
git checkout -b ac/REQ-16
```

#### 2.1 开发分支：单元测试 + 实现（开发负责）

```bash
git checkout dev/REQ-16

# ====== 先写单元测试（TDD）======
mkdir -p tests/unit/inventory
cat > tests/unit/inventory/predictor_test.go << 'EOF'
package inventory

import (
    "testing"
    "src/inventory/ai"
)

// 测试正常预测
func TestPredict_NormalCase(t *testing.T) {
    req := ai.PredictRequest{
        ProductID: "P001",
        Days:      7,
    }
    
    resp, err := ai.Predict(req)
    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }
    
    if resp.ProductID != "P001" {
        t.Errorf("expected product_id P001, got %s", resp.ProductID)
    }
    
    if resp.PredictQty <= 0 {
        t.Error("predict_qty should be positive")
    }
    
    // 验证风险等级在有效范围内
    validRisks := map[string]bool{"high": true, "medium": true, "low": true}
    if !validRisks[resp.RiskLevel] {
        t.Errorf("invalid risk_level: %s", resp.RiskLevel)
    }
}

// 测试无效参数
func TestPredict_InvalidDays(t *testing.T) {
    req := ai.PredictRequest{
        ProductID: "P001",
        Days:      0,  // 无效
    }
    
    _, err := ai.Predict(req)
    if err == nil {
        t.Error("expected error for invalid days")
    }
}

// 测试高风险场景
func TestPredict_HighRisk(t *testing.T) {
    // 库存低，销量高的场景
    req := ai.PredictRequest{
        ProductID: "P002_LOW_STOCK",
        Days:      7,
    }
    
    resp, _ := ai.Predict(req)
    
    if resp.RiskLevel != "high" {
        t.Errorf("expected high risk, got %s", resp.RiskLevel)
    }
    
    // 高风险时建议采购量应大于预测量
    if resp.SuggestQty <= resp.PredictQty {
        t.Error("suggest_qty should be greater than predict_qty for high risk")
    }
}
EOF

# ====== 再写实现代码 ======
mkdir -p src/inventory/ai
cat > src/inventory/ai/predictor.go << 'EOF'
package ai

import "errors"

type PredictRequest struct {
    ProductID string `json:"product_id"`
    Days      int    `json:"days"`
}

type PredictResponse struct {
    ProductID  string  `json:"product_id"`
    PredictQty float64 `json:"predict_qty"`
    RiskLevel  string  `json:"risk_level"`
    SuggestQty float64 `json:"suggest_qty"`
}

func Predict(req PredictRequest) (*PredictResponse, error) {
    // 参数校验
    if req.Days <= 0 || req.Days > 90 {
        return nil, errors.New("invalid days, must be 1-90")
    }
    
    // 获取历史数据并预测（简化实现）
    predictQty := calculatePredictQty(req.ProductID, req.Days)
    riskLevel := assessRisk(req.ProductID, predictQty)
    suggestQty := calculateSuggestQty(predictQty, riskLevel)
    
    return &PredictResponse{
        ProductID:  req.ProductID,
        PredictQty: predictQty,
        RiskLevel:  riskLevel,
        SuggestQty: suggestQty,
    }, nil
}

func calculatePredictQty(productID string, days int) float64 {
    // 实际实现：基于历史数据计算
    return 100.0
}

func assessRisk(productID string, predictQty float64) string {
    // 实际实现：根据库存和预测量评估风险
    return "medium"
}

func calculateSuggestQty(predictQty float64, riskLevel string) float64 {
    if riskLevel == "high" {
        return predictQty * 1.5  // 高风险多备货
    }
    return predictQty * 1.2
}
EOF

# 本地验证单元测试通过
go test ./tests/unit/inventory/...

# 提交开发代码
git add .
git commit -m "feat(REQ-16): implement AI inventory predictor with TDD

- Add unit tests for normal case, invalid params, high risk scenario
- Implement predictor with input validation
- Risk-based suggestion quantity calculation"
git push gitea dev/REQ-16
```

#### 2.2 测试分支：契约测试（测试负责）

```bash
git checkout test/REQ-16

# 基于 contract.spec.yaml 写契约测试
cat > tests/contract/inventory_predict_test.go << 'EOF'
package contract

import (
    "testing"
    "net/http"
    "encoding/json"
)

// 测试契约：正常请求响应
func TestPredictAPI_Contract_Normal(t *testing.T) {
    reqBody := map[string]interface{}{
        "product_id": "P001",
        "days":       7,
    }
    
    resp := CallAPI("POST", "/api/v1/inventory/predict", reqBody)
    
    // 契约：状态码必须是200
    if resp.StatusCode != 200 {
        t.Errorf("expected status 200, got %d", resp.StatusCode)
    }
    
    var result map[string]interface{}
    json.Unmarshal(resp.Body, &result)
    
    // 契约：必须包含这些字段
    requiredFields := []string{"product_id", "predict_qty", "risk_level", "suggest_qty"}
    for _, field := range requiredFields {
        if _, ok := result[field]; !ok {
            t.Errorf("missing required field: %s", field)
        }
    }
    
    // 契约：risk_level 必须是枚举值之一
    riskLevel := result["risk_level"].(string)
    validRisks := map[string]bool{"high": true, "medium": true, "low": true}
    if !validRisks[riskLevel] {
        t.Errorf("risk_level must be high/medium/low, got %s", riskLevel)
    }
}

// 测试契约：参数错误
func TestPredictAPI_Contract_InvalidParams(t *testing.T) {
    reqBody := map[string]interface{}{
        "product_id": "",  // 空ID，无效
        "days":       7,
    }
    
    resp := CallAPI("POST", "/api/v1/inventory/predict", reqBody)
    
    // 契约：状态码必须是400
    if resp.StatusCode != 400 {
        t.Errorf("expected status 400 for invalid params, got %d", resp.StatusCode)
    }
}

// 测试契约：days 边界值
func TestPredictAPI_Contract_BoundaryDays(t *testing.T) {
    // 契约：days=1 是允许的
    resp := CallAPI("POST", "/api/v1/inventory/predict", map[string]interface{}{
        "product_id": "P001",
        "days":       1,
    })
    if resp.StatusCode != 200 {
        t.Error("days=1 should be valid")
    }
    
    // 契约：days=90 是允许的
    resp = CallAPI("POST", "/api/v1/inventory/predict", map[string]interface{}{
        "product_id": "P001",
        "days":       90,
    })
    if resp.StatusCode != 200 {
        t.Error("days=90 should be valid")
    }
    
    // 契约：days=91 应该被拒绝
    resp = CallAPI("POST", "/api/v1/inventory/predict", map[string]interface{}{
        "product_id": "P001",
        "days":       91,
    })
    if resp.StatusCode != 400 {
        t.Error("days=91 should be invalid")
    }
}
EOF

# 提交契约测试
git add .
git commit -m "test(REQ-16): add contract tests based on spec

- Normal request/response schema validation
- Invalid parameter handling
- Boundary value testing for days parameter"
git push gitea test/REQ-16
```

#### 2.3 验收分支：验收用例（验收方负责）

```bash
git checkout ac/REQ-16

# 编写低代码验收用例
cat > tests/acceptance/cases/REQ-16.yaml << 'EOF'
version: "1.0"
req_id: "REQ-16"
title: "AI 智能库存预警"

acceptance_criteria:
  - id: "AC-01"
    title: "正常库存预测"
    given:
      - 商品 P001 存在历史销售数据
      - 当前库存为 50
    when:
      - action: call_api
        endpoint: "POST /api/v1/inventory/predict"
        params:
          product_id: "P001"
          days: 7
    then:
      - assert: response.status == 200
      - assert: response.predict_qty > 0
      - assert: response.risk_level in ["high", "medium", "low"]
      - assert: response.suggest_qty >= response.predict_qty

  - id: "AC-02"
    title: "高风险预警"
    given:
      - 商品 P002 当前库存为 5
      - 商品 P002 日均销量为 3
    when:
      - action: call_api
        endpoint: "POST /api/v1/inventory/predict"
        params:
          product_id: "P002"
          days: 7
    then:
      - assert: response.risk_level == "high"
      - assert: response.suggest_qty > 20

  - id: "AC-03"
    title: "每日预警任务"
    given:
      - 系统时间为凌晨 2:00
      - 存在高风险库存商品
    when:
      - action: trigger_cron
        job: "inventory.daily_alert"
    then:
      - assert: notification.sent == true
      - assert: notification.recipients contains "manager@store.com"
      - assert: notification.risk_items_count > 0

  - id: "AC-04"
    title: "参数校验"
    given:
      - 系统正常运行
    when:
      - action: call_api
        endpoint: "POST /api/v1/inventory/predict"
        params:
          product_id: "P001"
          days: 0  # 无效参数
    then:
      - assert: response.status == 400
      - assert: response.error_message exists
EOF

git add .
git commit -m "ac(REQ-16): add acceptance criteria

- Normal prediction flow
- High risk warning scenario
- Daily alert job
- Parameter validation"
git push gitea ac/REQ-16
```

### 阶段 3：合并到 feature 分支

```bash
# 三方都标记done后，合并
git checkout feature/REQ-16-ai-inventory

git merge dev/REQ-16 --no-ff -m "merge dev/REQ-16: unit tests and implementation"
git merge test/REQ-16 --no-ff -m "merge test/REQ-16: contract tests"
git merge ac/REQ-16 --no-ff -m "merge ac/REQ-16: acceptance criteria"

git push gitea feature/REQ-16-ai-inventory
```

### 阶段 4：TDD + 契约 Battle

```bash
# 本地运行
make test-unit          # 开发：验证单元测试
make test-contract      # 测试：验证契约测试

# 查看覆盖率
make test-coverage

# CI 自动执行
git push gitea feature/REQ-16-ai-inventory
```

**如果单元测试失败（开发修复）：**
```bash
# 查看失败详情
cat test-results/unit-test.log

# 开发修复代码
git checkout dev/REQ-16
# ... 修复代码 ...
git add .
git commit -m "fix(REQ-16): fix predictor boundary handling"
git push gitea dev/REQ-16

# 重新合并
git checkout feature/REQ-16-ai-inventory
git merge dev/REQ-16
git push gitea feature/REQ-16-ai-inventory
```

**如果契约测试失败（协商修复）：**
```bash
# 查看失败详情
cat test-results/contract-test.log

# 情况1：实现不符合契约 → 开发修
git checkout dev/REQ-16
# ... 修复实现 ...

# 情况2：契约不合理 → 测试更新契约测试
git checkout test/REQ-16
# ... 调整契约测试 ...
```

### 阶段 5：质量关卡

```bash
# 本地预检查
make lint
make security-scan

# AI Review
make ai-review REQ_ID=REQ-16
```

**如果失败（开发修复）：**
```bash
# 开发修复
git checkout dev/REQ-16
# ... 修复代码规范 / 重构 ...
git add .
git commit -m "refactor(REQ-16): fix lint issues per quality gate"
git push gitea dev/REQ-16

# 重新合并到feature，重新跑完整流程
git checkout feature/REQ-16-ai-inventory
git merge dev/REQ-16
git push gitea feature/REQ-16-ai-inventory
```

### 阶段 6：AI 验收

```bash
# 运行验收测试
make test-acceptance REQ_ID=REQ-16

# 生成报告
cat .reports/acceptance/REQ-16-report.md
```

**示例验收报告：**
```markdown
# AI 验收报告

**需求**: REQ-16 AI 智能库存预警  
**结果**: ⚠️ 部分通过 (3/4 通过)

| AC ID | 标题 | 结果 | 耗时 |
|-------|------|------|------|
| AC-01 | 正常库存预测 | ✅ 通过 | 2.5s |
| AC-02 | 高风险预警 | ✅ 通过 | 1.8s |
| AC-03 | 每日预警任务 | ❌ 失败 | 3.2s |
| AC-04 | 参数校验 | ✅ 通过 | 0.5s |

## 失败详情

### AC-03: 每日预警任务
**期望**: notification.sent == true  
**实际**: notification.sent == false
**原因**: 定时任务未触发通知逻辑

**建议修复**: 
- 检查 cron job 是否正确注册
- 确认高风险商品检测逻辑
```

**如果验收失败（开发修复）：**
```bash
# 开发修复
git checkout dev/REQ-16
# ... 修复业务逻辑 ...
git add .
git commit -m "fix(REQ-16): fix daily alert notification trigger"
git push gitea dev/REQ-16

# 重新合并，重新跑质量关卡+验收
git checkout feature/REQ-16-ai-inventory
git merge dev/REQ-16
git push gitea feature/REQ-16-ai-inventory
# CI自动触发：Linter → AI Review → 验收
```

### 阶段 7：发布

```bash
# 验收全绿后，合并到主分支
git checkout main
git merge feature/REQ-16-ai-inventory --no-ff

# 打标签
git tag -a "v2.21.0-REQ-16" -m "AI 智能库存预警功能"

git push gitea main --tags

# 自动部署
make deploy-production
```

---

## 常用命令速查

| 命令 | 说明 |
|------|------|
| `make contract-done REQ_ID=xx` | 标记契约设计完成 |
| `make spec-split REQ_ID=xx` | 运行 OpenSpec 拆分 |
| `make test-unit` | 运行单元测试（开发） |
| `make test-contract` | 运行契约测试（测试） |
| `make test-coverage` | 生成覆盖率报告 |
| `make lint` | 运行 Linter |
| `make lint-fix` | 自动修复 Linter 问题 |
| `make ai-review REQ_ID=xx` | 运行 AI Code Review |
| `make test-acceptance REQ_ID=xx` | 运行 AI 验收 |
| `make status REQ_ID=xx` | 查看需求状态 |

---

## 三角色职责清单

### 👨‍💻 开发职责

- [ ] 参与契约设计评审
- [ ] 基于开发规格写单元测试（TDD）
- [ ] 写实现代码让单元测试通过
- [ ] 单元测试覆盖率 ≥ 80%
- [ ] 修复 Linter / AI Review 问题
- [ ] 修复验收失败问题

### 🧪 测试职责

- [ ] 参与契约设计评审
- [ ] 基于契约规格写契约测试
- [ ] 验证边界值和错误场景
- [ ] 契约测试全部通过
- [ ] 审核单元测试覆盖情况（可选）

### ✅ 验收方职责

- [ ] 基于验收规格写低代码用例
- [ ] 覆盖核心业务场景
- [ ] 确保 Given/When/Then 完整
- [ ] AI 验收用例全部通过

---

## 状态追踪示例

```yaml
# .sisyphus/REQ-16/status.yaml
req_id: "REQ-16"
title: "AI 智能库存预警"
created_at: "2026-04-06T00:00:00Z"

phases:
  contract_design:
    status: done
    completed_at: "2026-04-06T01:00:00Z"
    reviewers: [backend, frontend, qa]
    
  spec_split:
    status: done
    completed_at: "2026-04-06T02:00:00Z"
    
  parallel_dev:
    dev_branch:
      status: done
      completed_at: "2026-04-06T06:00:00Z"
      commit: "abc123"
      unit_test_coverage: "87%"
    test_branch:
      status: done
      completed_at: "2026-04-06T07:00:00Z"
      commit: "def456"
      contract_test_count: 8
    ac_branch:
      status: done
      completed_at: "2026-04-06T08:00:00Z"
      commit: "ghi789"
      acceptance_cases_count: 4
      
  tdd_battle:
    status: passed
    completed_at: "2026-04-06T09:00:00Z"
    
  quality_gate:
    lint:
      status: passed
    ai_review:
      status: passed
      score: 92
      
  ai_acceptance:
    status: passed
    completed_at: "2026-04-06T11:00:00Z"
    passed: 4
    failed: 0
    
  release:
    status: done
    version: "v2.21.0-REQ-16"
```

---

## 问题排查

### 单元测试失败（开发处理）
```bash
# 查看详细日志
make test-unit-verbose

# 单独运行失败测试
go test -v -run TestPredict_NormalCase ./tests/unit/inventory/

# 调试特定测试
go test -v -run TestPredict_HighRisk ./tests/unit/inventory/ -debug
```

### 契约测试失败（测试/开发协商）
```bash
# 查看失败详情
make test-contract-verbose

# 检查契约规格是否更新
cat specs/REQ-16/contract.spec.yaml

# 情况1：实现问题 → 开发修
# 情况2：契约变更 → 测试更新契约测试
```

### 验收失败（开发处理）
```bash
# 查看执行日志
cat .reports/acceptance/REQ-16.log

# 手动执行单个用例调试
make test-acceptance-single REQ_ID=REQ-16 AC_ID=AC-03

# 查看失败现场截图/数据
cat .reports/acceptance/AC-03-failure.json
```
