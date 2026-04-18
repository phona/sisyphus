# REQ-00 评估框架

| Field | Value |
|-------|-------|
| ID | REQ-00 |
| Priority | P0 |
| Scope | meta |

## Overview

定义 sisyphus 在 ubox-crosser 测试用例上的评估维度、指标和评分标准。

---

## 1. 评估维度

| 维度 | 测试用例 | 权重 |
|------|---------|------|
| DAG 编排 | REQ-01→02/03→04→05 依赖调度正确性 | 20% |
| 并行执行 | REQ-02 和 REQ-03 是否真正并行 | 10% |
| 多技术栈 | Go (proto/proxy/api) + React (web) + SQL | 10% |
| 迭代修复 | REQ-06 bug fix 需要多轮定位修复 | 20% |
| 冲突预检 | REQ-07 并行改同一文件的检测与解决 | 15% |
| 容错与熔断 | REQ-08 不完整需求下的降级处理 | 15% |
| 存量代码理解 | REQ-06 在已有代码中定位并修复问题 | 10% |

---

## 2. 评分标准

### 每个 REQ 评分（0-10）

| 分数 | 含义 |
|------|------|
| 10 | AC 全部通过，CI 绿，无人工干预 |
| 8 | AC 全部通过，有 1-2 次自动修复迭代 |
| 6 | AC 大部分通过，需要 1 次人工提示后完成 |
| 4 | AC 部分通过，需要多次人工干预 |
| 2 | 有产出但 AC 基本未通过 |
| 0 | 无有效产出或熔断退出 |

### 总分计算

```
总分 = Σ (REQ_score × weight) / 10 × 100
```

满分 100，及格线 60。

---

## 3. 采集指标

每个 REQ 执行后记录：

| 指标 | 说明 | 采集方式 |
|------|------|---------|
| `duration` | 从分发到完成的总耗时（秒） | n8n 时间戳 |
| `token_consumed` | AI token 总消耗 | vibe-kanban API |
| `iterations` | 修复迭代次数（lint 失败 → fix → retry） | n8n 循环计数 |
| `circuit_breaks` | 熔断次数 | n8n 熔断事件 |
| `human_interventions` | 人工干预次数 | 手动记录 |
| `files_changed` | 修改/新增文件数 | `git diff --stat` |
| `ci_pass_rate` | CI 通过率（首次/最终） | GitHub Actions |
| `lint_clean` | 最终 lint 是否干净 | golangci-lint |
| `test_pass_rate` | 测试通过率 | `go test` / `npm test` |
| `ac_pass_count` | AC 通过数 / AC 总数 | 人工验收 |

---

## 4. 执行顺序

```
Phase 1: 基础构建能力
  REQ-01 (proto, Easy)           ← 基线：能不能做最简单的模块抽取
  REQ-06 (bug fix, Medium)       ← 存量代码理解 + 迭代修复

Phase 2: 并行编排能力
  REQ-02 (proxy, Hard)  ┐
                        ├──── 并行执行，测 DAG 调度
  REQ-03 (api, Hard)    ┘

Phase 3: 多技术栈
  REQ-04 (web, Medium)           ← Go 之外的技术栈

Phase 4: 全栈集成
  REQ-05 (integration, Medium)   ← 所有模块的胶水

Phase 5: 压力测试
  REQ-07 (conflict, Medium)      ← 冲突预检能力
  REQ-08 (incomplete, Hard)      ← 容错与熔断
```

---

## 5. 报告模板

```markdown
# Sisyphus 评估报告 — ubox-crosser

## 执行概览

| 指标 | 值 |
|------|-----|
| 总耗时 | {total_duration} |
| 总 token | {total_tokens} |
| 总迭代次数 | {total_iterations} |
| 总熔断次数 | {total_circuit_breaks} |
| 人工干预次数 | {total_human_interventions} |

## 各 REQ 评分

| REQ | 分数 | 权重 | 加权分 | 耗时 | Token | 迭代 | 熔断 | 人工 |
|-----|------|------|--------|------|-------|------|------|------|
| REQ-01 | /10 | 20% | | | | | | |
| REQ-02 | /10 | 10% | | | | | | |
| ...    |     |     | | | | | | |

## 总分: {total}/100

## 关键发现

1. ...
2. ...

## 改进建议

1. ...
```
