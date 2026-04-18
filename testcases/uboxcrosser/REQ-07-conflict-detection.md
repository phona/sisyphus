# REQ-07 并行任务文件冲突

| Field | Value |
|-------|-------|
| ID | REQ-07 |
| Priority | P1 |
| Difficulty | Medium |
| Scope | crosser-proxy, crosser-api |
| Dependencies | REQ-01 |

## Overview

设计一个**故意制造文件冲突**的场景：两个并行任务需要同时修改 `crosser-proto/message/message.go`。测试 sisyphus 的冲突预检和串行合并窗口能力。

---

## 1. 冲突场景设计

### 任务 A: 给 Message 添加 Version 字段

需求：在 `Message` 结构体中增加 `Version uint8` 字段，用于协议版本协商。

修改文件：
- `crosser-proto/message/message.go` — 添加字段
- `crosser-proxy/internal/server/proxy.go` — 发送消息时填充 Version
- `crosser-proxy/internal/client/controller.go` — 发送消息时填充 Version

### 任务 B: 给 Message 添加 Timestamp 字段

需求：在 `Message` 结构体中增加 `Timestamp int64` 字段，用于消息时序追踪。

修改文件：
- `crosser-proto/message/message.go` — 添加字段（**与任务 A 冲突**）
- `crosser-api/internal/handler/proxy.go` — 心跳上报解析 Timestamp

### 冲突点

两个任务都修改 `crosser-proto/message/message.go` 的同一个 struct：

```go
// 任务 A 的修改
type Message struct {
    Type      uint8  `json:"type"`
    ServeName string `json:"serve_name"`
    Password  string `json:"password"`
+   Version   uint8  `json:"version"`
}

// 任务 B 的修改
type Message struct {
    Type      uint8  `json:"type"`
    ServeName string `json:"serve_name"`
    Password  string `json:"password"`
+   Timestamp int64  `json:"timestamp"`
}
```

---

## 2. Acceptance Criteria

### AC-07.01 冲突预检

- **Given** 任务 A 和 B 同时分发
- **When** sisyphus 进行冲突预检（分发前的文件级检查）
- **Then** 检测到 `crosser-proto/message/message.go` 冲突，采取串行策略

### AC-07.02 串行合并

- **Given** 检测到冲突
- **When** sisyphus 选择串行执行（先 A 后 B 或先 B 后 A）
- **Then** 后执行的任务基于前一个的结果，最终两个字段都正确添加

### AC-07.03 合并结果正确

- **Given** 两个任务都完成
- **When** 检查 `message.go`
- **Then** Message 结构体同时包含 Version 和 Timestamp 字段，JSON tag 正确

### AC-07.04 降级策略（备选）

- **Given** 冲突预检失败（未检测到）
- **When** 第二个 PR 合并时 git conflict
- **Then** sisyphus 能自动解决 merge conflict 并重新验证

---

## 3. Evaluation Focus

| 能力 | 测试点 |
|------|--------|
| 冲突预检 | 分发前是否能检测到两个任务改同一文件 |
| 调度策略 | 检测到冲突后选择串行还是其他策略 |
| 冲突解决 | 如果预检失败，能否自动解 merge conflict |
| 最终正确性 | 两个字段都正确存在，CI 通过 |

---

## 4. Expected Behavior

**最佳路径**（10 分）：
1. 分发前扫描两个任务的目标文件
2. 发现冲突，将任务 B 排在任务 A 之后
3. 任务 A 完成并合并
4. 任务 B 基于最新代码执行，合并成功

**可接受路径**（6 分）：
1. 并行执行两个任务
2. 任务 A 先合并成功
3. 任务 B 合并时 conflict
4. 自动 rebase/merge resolve，重新验证后合并

**失败路径**（2 分）：
1. 并行执行
2. 冲突后熔断，需要人工干预
