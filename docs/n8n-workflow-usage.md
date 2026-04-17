# n8n 主编排使用说明

> 基于 V2 架构，n8n 负责粗粒度主编排，管"做什么、什么顺序"。

## 编排职责

n8n 在 V2 架构中的角色：

```
需求 → P0 合约 → P1 规格拆分 → 冲突预检 → 并发池检查 →
分发给 AI → 收集验证结果 → 熔断检查 → 创建 PR / 升级人工
```

n8n **不写代码、不直接操作调试环境**，这些由 vibe-kanban + Claude MCP 通过 aissh 完成。

## 编排节点说明

| 节点 | 功能 | 说明 |
|------|------|------|
| **需求接收** | 接收 Issue REQ-xx | Webhook 或手动触发 |
| **P0 合约设计** | 分发合约设计任务 | 输出：contract.spec.yaml |
| **P1 规格拆分** | 分发规格拆分任务 | 输出：dev.spec / contract.spec / ac.spec + 验收用例锁定 |
| **冲突预检** | 检查文件级冲突 | 目标文件是否被其他活跃任务占用 |
| **并发池检查** | 检查资源余量 | 活跃 namespace 是否低于上限，超出则排队 |
| **任务分发** | 分发给 vibe-kanban | AI 在 worktree 写代码 + skill 小编排 |
| **心跳监听** | 接收 AI 进度上报 | 超时未收到 → 强制中断 → 挂 Issue |
| **结果收集** | 接收调试环境验证结果 | AI 通过 aissh 操控调试环境后回报 |
| **熔断检查** | 判断是否触发熔断 | ≥3轮修复 or 超时 or token 超限 |
| **创建 PR** | 推送到 GitHub | 通过串行合并窗口 |
| **CI 结果处理** | CI 失败分类 | flaky → retry，真实失败 → 回开发环境 |
| **验收调度** | 触发独立子 agent | 干净上下文执行 Given/When/Then |

## 导入步骤

1. 登录 n8n Web 界面 (http://localhost:5678)
2. 点击 **Workflows** → **Import from File**
3. 选择 `charts/n8n-workflows/` 下的工作流 JSON
4. 配置凭证（见下方）

## 凭证配置

### Gitea API

1. **Settings** → **Credentials** → **New Credential**
2. 选择 **HTTP Header Auth**
3. 配置：
   - **Name**: `Gitea API`
   - **Header Name**: `Authorization`
   - **Header Value**: `token YOUR_GITEA_TOKEN`

> 获取 Token: Gitea → 用户设置 → 应用 → 生成令牌

### vibe-kanban Wrapper

n8n 通过 HTTP 调用 vibe-kanban wrapper（端口 3005）分发任务。

## 触发方式

### Webhook 触发（推荐）

配置 Webhook URL，由外部系统（Issue 创建、手动触发）调用：

```json
{
  "req_id": "REQ-01",
  "title": "会员堂食点餐功能",
  "description": "需求描述",
  "scope": "server-go"
}
```

### 手动触发

在 n8n 中打开工作流 → **Execute Workflow** → 输入上述 JSON。

## 熔断机制实现

n8n 负责熔断判断，不依赖 AI：

```
每次验证失败：
  1. 修复计数器 +1
  2. 累加 token 消耗和耗时
  3. 检查三个条件：
     - 修复轮数 ≥ 3 ?
     - 累计耗时超限 ?
     - 累计 token 超限 ?
  4. 任一触发 → 挂 Issue 升级人工，停止自动修复
     全部未触发 → 创建子 Issue，回 AI 继续修
```

## 并发池管理

n8n 维护全局并发池状态：

```
分发任务前：
  1. 查询当前活跃 namespace 数量
  2. 活跃数 < 上限 → 分发任务，计数 +1
  3. 活跃数 ≥ 上限 → 排队等待
  
任务完成后：
  1. 清理 namespace
  2. 计数 -1
  3. 检查队列，有等待任务则分发
```

## 心跳监听

AI 执行 skill 小编排时，定期向 n8n 上报进度：

```
n8n 侧：
  1. 任务分发时启动超时计时器
  2. 收到心跳 → 重置计时器
  3. 超时未收到 → 强制中断任务 → 挂 Issue
```

## CI 失败分类

CI 失败后 n8n 做分类处理：

| 类型 | 判断依据 | 处理 |
|------|---------|------|
| flaky test | 历史记录中同一测试间歇性失败 | 直接 retry CI |
| 真实失败 | 新出现的失败 | 挂 Issue 回开发环境修复 |

## 串行合并窗口

多个功能同时通过验证时，n8n 控制串行合并：

```
功能 A 通过 → 进入合并队列
功能 B 通过 → 进入合并队列
队列处理：A 合并 → B rebase → B 合并
```

## 故障排查

### API 返回 401

检查凭证配置，确保 token 有效且有足够权限。

### HTTP 节点超时

1. 检查目标服务是否正常
2. 节点设置中增加超时时间
3. 检查网络连接

### 工作流执行卡住

1. 检查心跳是否正常上报
2. 查看执行日志定位问题节点
3. 确保所有条件分支都有出口
4. 检查是否触发了熔断但未正确处理

## 参考文档

- [V2 架构设计](./workflow-v2-architecture.md)（权威参考）
- [n8n 官方文档](https://docs.n8n.io/)
- [Gitea API 文档](https://docs.gitea.com/api/)
