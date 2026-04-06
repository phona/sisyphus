# 分布式 CI 架构 - 实施与验证计划

> 先在本地 Kind 集群实验验证，再上生产

## 当前状态

| 组件 | 状态 | 说明 |
|------|------|------|
| Kind 集群 | ✅ 已部署 | 1 control-plane |
| Gitea | ✅ 运行中 | http://gitea-http.sisyphus.svc.cluster.local:3000 |
| Act Runner | ✅ 运行中 | DinD 模式，self-hosted 标签 |
| PostgreSQL | ✅ 运行中 | 共享数据库 |
| n8n | ❌ 未部署 | 下一步部署 |
| vibe-kanban | ⚠️ 基础部署 | 需要扩展 CI 功能 |

## 实验验证路线图

### Stage 1: 补齐基础设施

**目标**: 部署 n8n，扩展 vibe-kanban

```bash
# 1. 部署 n8n
make deploy-n8n

# 2. 扩展 vibe-kanban 添加 API 端点
# - /api/fix-tdd
# - /api/fix-quality
# - /api/fix-acceptance

# 3. 配置 n8n 凭证
# - Gitea API Token
# - vibe-kanban API Token
```

**验证标准**:
- [ ] n8n Web 界面可访问
- [ ] vibe-kanban API 响应正常
- [ ] n8n 能调用 vibe-kanban API

### Stage 2: 单阶段闭环验证 (重点)

**目标**: 验证 Phase 3 (TDD Battle) 完整闭环

```
开发者 push ──► 测试机 CI 失败 ──► n8n 收到通知
     ▲                                          │
     │                                          ▼
  重新触发 ◄── 测试机 CI 通过 ◄── vibe-kanban 修复代码
```

**实施步骤**:

1. **创建测试项目** (`ttpos-server-go` 或新建)

2. **配置测试机 CI** (`.gitea/workflows/ci-report.yml`)
   ```yaml
   name: CI Report
   on: [push]
   jobs:
     test:
       runs-on: self-hosted-test
       steps:
         - uses: actions/checkout@v4
         - run: make ci-lint || echo "LINT_FAILED=1" >> $GITHUB_ENV
         - run: make ci-unit-test || echo "TEST_FAILED=1" >> $GITHUB_ENV
         - name: Report to n8n
           if: failure()
           run: |
             curl -X POST "http://n8n.sisyphus.svc.cluster.local:5678/webhook/ci-failed" \
               -d '{"project":"'${GITHUB_REPOSITORY}'","branch":"'${GITHUB_REF_NAME}'","failed":true}'
   ```

3. **配置 n8n 工作流**
   - Webhook 接收 CI 失败
   - HTTP Request 调用 vibe-kanban `/api/fix-tdd`
   - 等待修复完成后重新触发 CI

4. **故意制造 CI 失败**
   ```go
   // 写一段有 lint 错误的代码
   func BadCode() {
       x := 1  // unused variable
   }
   ```

5. **验证自动修复闭环**
   - push 代码
   - CI 失败 → n8n 收到通知
   - n8n 调用 vibe-kanban
   - Claude 修复代码
   - 重新 push → CI 通过

**验证标准**:
- [ ] CI 失败时自动触发修复
- [ ] vibe-kanban 能调用 Claude
- [ ] 修复后的代码能通过 CI
- [ ] 整个过程无需人工干预

### Stage 3: 双阶段串联验证

**目标**: Phase 3 (TDD) → Phase 4 (质量关卡)

```
TDD 通过 ──► 触发质量关卡 ──► Lint 失败 ──► 修复 ──► 质量关卡通过
```

**验证标准**:
- [ ] TDD 通过后自动进入质量关卡
- [ ] 质量关卡失败能触发修复
- [ ] 修复后自动重新跑质量关卡

### Stage 4: 全阶段验证

**目标**: 完整 6 阶段工作流

```
REQ-01 ──► P0 ──► P1 ──► P2 ──► P3 ──► P4 ──► P5 ──► P6
```

**验证标准**:
- [ ] 能从需求创建到发布完成
- [ ] 各阶段失败都能自动修复
- [ ] Issue 归档完整
- [ ] 可追踪每个修复步骤

## 文件变更清单

### 新增文件

```
docs/
├── distributed-ci-architecture.md    # 架构文档 (已创建)
├── implementation-plan.md            # 本文件
└── experiment-results.md             # 实验结果记录 (待创建)

charts/
├── n8n.yaml                          # n8n 部署配置
└── gitea-act-runner-test.yaml        # 测试机 Runner (专用标签)

.gitea/workflows/
├── ci-report.yml                     # 测试机 CI，失败时报告
├── tdd-battle.yml                    # Phase 3
├── quality-gate.yml                  # Phase 4
└── ai-acceptance.yml                 # Phase 5

scripts/
├── setup-experiment.sh               # 实验环境初始化
├── run-experiment.sh                 # 运行单阶段实验
└── verify-experiment.sh              # 验证实验结果
```

### 修改文件

```
projects/vibe-kanban/
├── Dockerfile                        # 添加 CI 工具
└── src/
    └── api/
        └── ci-fix.js                 # 添加修复 API 端点

Makefile                              # 添加实验命令
README.md                             # 更新架构说明
```

## 实验命令

```bash
# 初始化实验环境
make experiment-init

# 运行单阶段实验 (TDD Battle)
make experiment-tdd REQ_ID=REQ-TEST-01

# 运行双阶段实验
make experiment-tdd-quality REQ_ID=REQ-TEST-02

# 运行全阶段实验
make experiment-full REQ_ID=REQ-TEST-03

# 查看实验结果
make experiment-logs REQ_ID=REQ-TEST-01

# 清理实验数据
make experiment-clean
```

## 实验检查清单

### Stage 2 检查清单

| 检查项 | 命令/方法 | 预期结果 |
|--------|----------|---------|
| n8n 运行中 | `kubectl get pod -n sisyphus -l app=n8n` | Running |
| vibe-kanban API 可用 | `curl vibe-kanban:3000/api/health` | OK |
| CI 失败触发 webhook | 故意 push 坏代码 | n8n 收到请求 |
| vibe-kanban 调用 Claude | 查看 vibe-kanban 日志 | MCP 调用成功 |
| 修复后的代码正确 | 查看 Gitea commit | 修复了问题 |
| CI 重新运行通过 | 查看 Gitea Actions | ✅ Passed |

### Stage 4 检查清单

| 检查项 | 验证方法 |
|--------|---------|
| 完整流程时间 | < 30 分钟 (简单需求) |
| Issue 归档 | Gitea 有完整的 CI Failed Issue |
| 修复可追溯 | 每个修复都有 commit 记录 |
| 无需人工干预 | 从需求到发布全自动 |

## 上生产 checklist

### 迁移前准备

- [ ] Stage 4 实验通过 3 次以上
- [ ] 文档完整更新
- [ ] 回滚方案准备

### 生产部署

- [ ] GitHub 仓库创建
- [ ] GitHub Actions Runner 配置
- [ ] n8n 生产部署
- [ ] vibe-kanban 生产部署
- [ ] 网络配置 (HTTPS/防火墙)
- [ ] 监控告警配置

### 验证

- [ ] 生产环境首次完整流程
- [ ] 性能测试 (并发需求处理)
- [ ] 监控数据正常

## 问题记录

| 时间 | 问题 | 解决方案 | 状态 |
|------|------|---------|------|
| - | - | - | - |

## 参考

- [分布式 CI 架构](./distributed-ci-architecture.md)
- [AI 驱动测试工作流](./ai-driven-testing-workflow.md)
