# REQ-08 不完整需求下的容错测试

| Field | Value |
|-------|-------|
| ID | REQ-08 |
| Priority | P1 |
| Difficulty | Hard |
| Scope | crosser-api |
| Dependencies | REQ-03 |

## Overview

故意给出一个**信息不完整、存在歧义**的需求，测试 sisyphus 的容错和降级能力：能否识别缺失信息、合理假设、或正确熔断升级。

---

## 1. 不完整需求（故意的）

> 给 crosser-api 加一个"审计日志"功能。
> 所有操作都要记录下来。
> 要能查询。

**故意缺失的信息：**
- 没说"所有操作"具体包含哪些（API 调用？用户登录？服务变更？proxy 事件？）
- 没说日志存储方式（同一个 SQLite？独立文件？独立表？）
- 没说查询 API 的过滤条件（按时间？按用户？按操作类型？按服务？）
- 没说日志保留策略（永久？按天清理？按大小轮转？）
- 没说日志格式（结构化 JSON？纯文本？）
- 没说是否需要分页

---

## 2. Acceptance Criteria

### 路径 A: 合理假设并实现（最佳）

sisyphus 能：
1. 识别出需求不完整
2. 做出合理假设并**记录假设**（在 Issue/PR 中说明）
3. 实现一个合理的 MVP 版本
4. CI 通过

评分标准：
- 假设合理（比如"记录所有 API 调用"而不是"记录所有系统调用"）：+3
- 假设被明确记录：+2
- 实现可用且 CI 通过：+3
- 有查询 API 且支持基本过滤：+2

### 路径 B: 请求澄清（可接受）

sisyphus 能：
1. 识别出需求不完整
2. 在 Issue 中列出需要澄清的问题
3. 等待人工回复（或超时后降级）

评分标准：
- 问题列表准确覆盖关键缺失信息：+5
- 超时后合理降级（做 MVP 而不是空等）：+3
- 无降级，直接熔断：+2

### 路径 C: 盲目实现（较差）

sisyphus 不识别需求不完整，直接按自己理解实现：
- 如果结果合理且可用：4 分
- 如果结果过度设计或方向偏差：2 分
- 如果实现失败或不可用：0 分

### 路径 D: 直接熔断（最差）

无法处理模糊需求，直接放弃：0 分

---

## 3. Evaluation Focus

| 能力 | 测试点 |
|------|--------|
| 需求分析 | 能否识别出哪些信息缺失 |
| 合理假设 | 做的假设是否符合常识（不过度也不过简） |
| 沟通能力 | 是否在 Issue/PR 中记录了假设和决策 |
| 降级策略 | 无法确认时是做 MVP 还是死等还是放弃 |
| 实现质量 | 最终产出是否可用 |

---

## 4. 参考实现（仅用于评估，不提供给 sisyphus）

一个合理的 MVP 应该包含：

```
crosser-api/internal/
├── model/
│   └── audit_log.go          # AuditLog 模型
├── repository/
│   └── audit_log.go          # 日志存取
├── middleware/
│   └── audit.go              # HTTP 中间件，自动记录 API 调用
└── handler/
    └── audit.go              # GET /api/v1/audit/logs?user=&action=&from=&to=&page=&size=
```

数据库表：
```sql
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT,
    action TEXT NOT NULL,         -- "POST /api/v1/services"
    resource TEXT,                -- "service:test_svc"
    request_body TEXT,
    response_code INTEGER,
    ip_address TEXT,
    created_at INTEGER NOT NULL
);
```

合理假设清单：
- "所有操作" = 所有 REST API 调用（通过中间件自动采集）
- 存储 = 同一个 SQLite 数据库的 audit_logs 表
- 查询 = 支持按用户、操作、时间范围过滤 + 分页
- 保留 = 默认不清理（MVP 阶段）
- 格式 = 结构化 JSON API 响应

---

## 5. 预期迭代路径

大概率需要多轮迭代：

```
Round 1: 实现基本审计中间件 + 存储
         → 可能 lint 或测试失败

Round 2: 修复 lint（比如 error 未检查）
         → 可能查询 API 缺分页

Round 3: 补分页 + 时间过滤
         → CI 可能因 migration 顺序问题失败

Round 4: 修 migration
         → 最终通过
```

这种多轮迭代正是测试 sisyphus 修复循环和熔断机制的最佳场景。
