# REQ-06 Bug Fix: 并发竞态修复

| Field | Value |
|-------|-------|
| ID | REQ-06 |
| Priority | P0 |
| Difficulty | Medium |
| Scope | crosser-proxy (存量代码) |
| Dependencies | None（直接在现有 master 代码上操作） |

## Overview

ubox-crosser 的 ProxyServer 存在多个并发安全问题。本用例要求 sisyphus **在现有代码中定位、修复并验证**这些问题，测试其存量代码理解和迭代修复能力。

**注意：这不是 greenfield 任务，必须修改已有文件，不是新建模块。**

---

## 1. Bug Description

### Bug 1: controllers map 无锁并发读写

**文件**: `server/server.go`

`ProxyServer.controllers` 是一个 `map[string]*controller`，被多个 goroutine 并发读写：
- `handleLoginRequest()` 写入：`p.controllers[serveName] = controller`
- `handleConnection()` 读取：`p.controllers[reqMsg.ServeName]`
- `handleAuthRequest()` 读取：`controller, ok := p.controllers[serveName]`

Go map 不是并发安全的，高并发下会 panic: `concurrent map read and map write`。

### Bug 2: initWorker 共享切片无锁

**文件**: `server/server.go`

`initWorker()` 中多个 goroutine 同时操作 `*pListenedAddr`（共享切片指针）：
```go
for _, config_ := range configs {
    go server.initWorker(&listenedAddr, config_)  // 并发写 listenedAddr
}
```

### Bug 3: heartbeat timer 无保护

**文件**: `client/controller.go`

`Controller.heartBeatTimer` 在 `login()` 中创建，但 `Run()` 循环可能在 timer 未初始化时访问。`ctlConn` 的读写也缺乏同步保护。

---

## 2. Acceptance Criteria

### AC-06.01 Race Detection

- **Given** 修复后的代码
- **When** `go test -race ./...`
- **Then** 零 race condition 报告

### AC-06.02 并发压力测试

- **Given** 修复后的 proxy-server
- **When** 10 个 client 同时连接、登录、建立 worker
- **Then** 无 panic，所有连接正常建立

### AC-06.03 现有测试不回归

- **Given** 修复后的代码
- **When** 运行集成测试 `make test-integration`
- **Then** 所有现有测试继续通过

### AC-06.04 最小改动

- **Given** diff 输出
- **When** 审查修改范围
- **Then** 只修改必要文件，不做无关重构

---

## 3. Expected Fix Approach

sisyphus 应该：

1. **定位问题**：通过 `go vet -race` 或代码审查发现竞态
2. **选择方案**：`sync.RWMutex` 保护 controllers map，或改用 `sync.Map`
3. **修复 + 测试**：每个 bug 修复后跑 `-race` 验证
4. **可能需要多轮迭代**：首次修复可能不完整（比如只修了 map 没修 slice），需要再次检测并修复

---

## 4. Evaluation Focus

| 能力 | 测试点 |
|------|--------|
| 代码理解 | 能否准确定位 3 个并发问题 |
| 修复质量 | 选择的同步方案是否合理（RWMutex vs sync.Map） |
| 迭代能力 | 第一轮修不完是否能自动发现并继续 |
| 最小侵入 | 是否只改必要的代码，不做额外重构 |
| 回归意识 | 修完是否主动跑现有测试验证 |

---

## 5. Key Files

修改范围应限于：

- `server/server.go` — controllers map 加锁, initWorker 修复
- `server/controller.go` — 可能需要调整 controller 的并发访问
- `client/controller.go` — heartbeat timer 和 ctlConn 同步保护
- 新增 `server/server_test.go` 或 `server/race_test.go` — 并发测试
