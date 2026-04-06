# n8n AI 驱动测试工作流使用说明

## 工作流概述

该工作流实现了完整的 AI 驱动自动化测试流程，包括：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        AI 驱动自动化测试工作流                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   🚀 触发 → 📋 契约设计 → ✂️ OpenSpec拆分 → ⚡ 并行开发 →                    │
│                                                                             │
│   🎯 TDD Battle → 🔍 质量关卡 → ✅ AI验收 → 🚀 发布                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 导入步骤

### 1. 导入工作流

1. 登录 n8n Web 界面 (http://localhost:5678)
2. 点击左侧菜单 **Workflows** → **Import from File**
3. 选择 `docs/n8n-ai-testing-workflow.json`
4. 点击 **Import**

### 2. 配置凭证

#### Gitea API 凭证

1. 在 n8n 中进入 **Settings** → **Credentials**
2. 点击 **New Credential**
3. 选择 **HTTP Header Auth**
4. 配置：
   - **Name**: `Gitea API`
   - **Header Name**: `Authorization`
   - **Header Value**: `token YOUR_GITEA_TOKEN`

> 获取 Gitea Token: 登录 Gitea → 用户设置 → 应用 → 生成令牌

### 3. 更新工作流中的凭证引用

检查以下节点，确保使用正确的凭证：
- 创建契约设计任务
- 检查契约规格
- 创建分支 PR
- 检查三方状态
- 创建合并 PR
- 创建 Battle 失败 Issue
- 创建质量关卡失败 Issue
- 创建验收失败 Issue
- 创建发布 PR
- 合并到 Master
- 触发自动部署

## 工作流节点说明

| 阶段 | 节点 | 功能 |
|------|------|------|
| **触发** | Manual Trigger | 手动触发工作流 |
| **初始化** | 初始化需求上下文 | 设置需求ID、标题、各阶段状态 |
| **Phase 0** | 创建契约设计任务 | 在 Gitea 创建契约设计 Issue |
| | 检查契约规格 | 检查 `contract.spec.yaml` 是否存在 |
| **Phase 1** | OpenSpec 拆分 | 拆分需求为开发/契约/验收规格 |
| **Phase 2** | 创建并行分支 | 创建 dev/test/ac 三个分支 |
| | 创建分支 PR | 为每个分支创建 Pull Request |
| | 检查三方状态 | 检查 `.sisyphus/{req-id}/status.yaml` |
| **Phase 3** | 运行 TDD Battle | 执行单元测试和契约测试 |
| | Battle 通过? | 条件判断，失败则创建 Issue |
| **Phase 4** | 运行质量关卡 | 执行 Linter 和 AI Code Review |
| | 质量关卡通过? | 条件判断，失败则创建 Issue |
| **Phase 5** | 运行 AI 验收 | 执行低代码验收用例 |
| | 验收通过? | 条件判断，失败则创建 Issue |
| **发布** | 创建发布 PR | 创建合并到 master 的 PR |
| | 合并到 Master | 自动合并 PR |
| | 触发自动部署 | 触发 Gitea Actions 部署 |
| **报告** | 生成测试报告 | 汇总各阶段结果 |

## 触发方式

### 方式一：手动触发

1. 在 n8n 中打开工作流
2. 点击 **Execute Workflow**
3. 在弹出的对话框中输入 JSON：

```json
{
  "req_id": "REQ-01",
  "title": "会员堂食点餐功能"
}
```

### 方式二：Webhook 触发（可选配置）

1. 添加 **Webhook** 节点替换 Manual Trigger
2. 配置 Webhook URL
3. 在 Gitea 中配置 webhook 调用该 URL

### 方式三：定时触发（可选配置）

1. 添加 **Schedule Trigger** 节点
2. 配置定时规则（如每小时检查一次状态）

## 各阶段失败处理

| 失败阶段 | 自动处理 | 人工介入 |
|---------|---------|---------|
| TDD Battle | 创建 Issue 通知开发 | 开发修复代码后重新运行 |
| 质量关卡 | 创建 Issue 通知开发 | 开发修复规范后重新运行 |
| AI 验收 | 创建 Issue 通知开发 | 开发修复后从质量关卡重新运行 |

## 扩展建议

### 1. 集成真实测试执行

将以下模拟节点替换为实际调用：

```javascript
// 运行 TDD Battle - 替换为实际调用
const unitTestResult = await $httpRequest({
  method: 'POST',
  url: 'http://your-ci-server/run-unit-tests',
  body: { branch: input.branch_name }
});

const contractTestResult = await $httpRequest({
  method: 'POST', 
  url: 'http://your-ci-server/run-contract-tests',
  body: { branch: input.branch_name }
});
```

### 2. 集成 AI Code Review

添加 AI 节点调用 LLM API：

```javascript
// AI Code Review 节点
const reviewResult = await $httpRequest({
  method: 'POST',
  url: 'https://api.anthropic.com/v1/messages',
  headers: {
    'Authorization': 'Bearer YOUR_CLAUDE_API_KEY',
    'Content-Type': 'application/json'
  },
  body: {
    model: 'claude-3-5-sonnet-20241022',
    max_tokens: 4000,
    messages: [{
      role: 'user',
      content: `请对以下代码进行 Review，参考规格文件：\n${specContent}\n\n代码：\n${codeContent}`
    }]
  }
});
```

### 3. 集成通知系统

添加 **Slack** 或 **企业微信** 节点：

```javascript
// Slack 通知节点
return [{
  json: {
    channel: '#dev-alerts',
    text: `🚨 ${input.req_id} 验收失败，请查看 ${input.issue_url}`
  }
}];
```

## 文件输出

工作流执行完成后会生成 JSON 格式的测试报告：

```json
{
  "req_id": "REQ-01",
  "title": "会员堂食点餐功能",
  "status": "completed",
  "phases": {
    "spec_split": { "status": "passed", ... },
    "parallel_dev": { "status": "passed", ... },
    "tdd_battle": { "status": "passed", ... },
    "quality_gate": { "status": "passed", ... },
    "ai_acceptance": { "status": "passed", ... }
  },
  "summary": {
    "coverage": 85,
    "contract_tests": 12,
    "acceptance_cases": 6
  }
}
```

## 故障排查

### 问题：Gitea API 返回 401

**解决**: 检查凭证配置，确保 token 有效且有足够权限

### 问题：HTTP Request 节点超时

**解决**: 
1. 检查 Gitea 服务是否正常运行
2. 在节点设置中增加超时时间
3. 检查网络连接

### 问题：工作流执行卡住

**解决**:
1. 检查是否有无限循环
2. 查看执行日志定位问题节点
3. 确保所有条件分支都有出口

## 参考文档

- [AI 驱动自动化测试方案](./ai-driven-testing-workflow.md)
- [n8n 官方文档](https://docs.n8n.io/)
- [Gitea API 文档](https://docs.gitea.com/api/)
