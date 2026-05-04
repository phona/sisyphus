# Sisyphus Development Playbook

> 一个人 + AI 搭平台的开发节奏。**Cap > Plan**。
> 本文是产品 owner 自用执行手册，不是技术架构文档。

权威架构：[architecture.md](architecture.md)
观测哲学：[observability.md](observability.md)

## 1. 当前阶段（0.x → 1.x 过渡）

| 阶段 | 标志 | 关键动作 |
|---|---|---|
| 0.x（current） | 核心管道通，dogfood 自己 + 1 个早期 user (ttpos) | 攒 5-10 个真 REQ 跑，不停下改 |
| 1.x | 可用，挡 80% ttpos 需求 | 接第 2 个业务仓 |
| 2.x | 规模化，多 user 多场景 | sisyphus 真正"挡需求让我做架构" |

**当前位置**：0.x 末尾。代码层 thanatos M2 已合，业务仓接入 PR 写出但卡 conflict 没合，端到端没真跑过。

## 2. 当前三步 plan（不展开成更多步）

```
1. 落地 thanatos        cap 1 周
   - .thanatos/skill.yaml on ttpos-flutter feat/develop-hwt
   - redroid + thanatos sidecar 在 vm-node04 K3s 实跑
   - 一个 minimal scenario 端到端跑通
   见 #248

2. 灌几个需求           cap 这周写 description，下周一批量派
   - 5 个 ttpos REQ description（每个 ≥200 字）
   - 不一句话派 REQ
   - 写在 BACKLOG.md（不在 BKD 不在 issue）

3. 迭代 loop            batch-of-5 节奏
   - 5 个 REQ 跑完看 pattern
   - 撞墙 log 不停
   - >= 3 次同类 pattern 才改 sisyphus
   - 不 hit-1-stop
```

**到 1.x 的硬指标**：5 个 ttpos REQ 端到端跑过 + 通过率统计。不是"sisyphus 改完美"。

## 3. 平台 vs 工具 mindset

| | 工具（错） | 平台（对） |
|---|---|---|
| 目标 | 一次解一个问题 | 多场景多用户多 driver |
| 工程量 | 1x | 10x（observability / 调度 / 恢复 / 文档） |
| 节奏 | hit-1-stop 改完再跑 | batch-of-5 攒 sample 再批量改 |
| dogfood | 跑通就够 | 跑 5-10 次看分布 |

承认在搭平台后，**termination accounting / retrospective / queue health 不是 over-engineering**——平台运营层必需。但**不在 0.x 阶段做**，1.x 之后再上。

## 4. 角色分工：脑容量是真瓶颈

一个人 + AI 的最大陷阱：AI 产能放大 10x → review/decision 翻 10x → 拍脑袋决策 → reject rate 高 → token 浪费 → self-doubt → 停下改 sisyphus。

**红线分工**：

| 必须你做（不能 AI 替） | AI 做 |
|---|---|
| **写 REQ description**（产品定义） | sisyphus 接住实现 |
| 设计 stage 哲学 / 优先级 | sisyphus 跑各 stage agent |
| 看 dogfood 反馈写 retrospective | agent 写第一稿 |
| **决策**（合 PR / reject / 关 issue） | - |
| 写 fixture（小但跨仓的 yaml） | - |

**最重要红线**：**写 REQ description 不能 AI 替**。这是你的 unique value，AI 只能放大不能 replace。10 分钟想清楚的 REQ description > 10 秒糊出去的，效果差 10x。

## 5. 周节奏

```
周一 (1-2h)
  - 看 BACKLOG.md → 挑 3-5 个 REQ
  - 每个写 200-300 字 description（不是一句话）
  - 派给 sisyphus

周二-周三 (each 1h，分两次看)
  - sisyphus 跑，你不参与生产
  - 看 stage_runs / PR / decision JSON 摘要
  - 撞墙的 log 一行
  - 不要回头改 sisyphus

周四 (2-3h)
  - 收本周 dogfood log
  - 找 pattern (>= 3 次同类问题)
  - 列下周改 sisyphus 清单（基于 pattern，不是 1-shot）

周五 (3-4h)
  - 改 sisyphus
  - 周末前 release v0.x.y

周末
  - 不开 sisyphus，离开。脑子需要 idle 时间想宏观
```

## 6. 月 / 季度节奏

| 周期 | 时长 | 内容 |
|---|---|---|
| 月初 | 1h | 定义本月 phase 目标 + cap 每周 REQ 数 + cap 每月 sisyphus 改动数 |
| 月中 | 1h | mid-review，砍做不完的 |
| 月末 | 2h | 写 monthly retrospective，更新 phase 计划 |

季度里程碑：

| 时间 | 阶段 | 标志 |
|---|---|---|
| 现在 ~ 1.5 月 | 0.x → 1.x | ttpos 端到端跑通 + 5 REQ 跑过 + 80% 命中率初步验证 |
| 1.5 ~ 3 月 | 1.x | 第二业务仓接入（ttpos-server-go / pma-vkb） |
| 3 ~ 6 月 | 2.x | 多 user，sisyphus 真正"挡需求让我做架构" |

## 7. 反 Pattern（每条都要警惕）

| 反 pattern | 表现 | 解药 |
|---|---|---|
| **"AI 产能 = 我产能" 错觉** | 派太多 REQ 看不过来 | 每天 review PR 上限 5 个，超过 batch / defer |
| **"想清楚再动手" 完美主义** | hit-1-stop loop | 平台没"想清楚再动手"——边跑边修。**但写 REQ description 必须想清楚**（产品工作） |
| **"这周改 sisyphus 这件事就解决了" 拖延** | 等改好才下一步 | sisyphus 永远改不完。每周 release 一次哪怕 trivial 也比"等改好"强 |
| **"周末加班赶进度"** | 一个人项目 burnout 风险 | 周末必须不开。脑子需要 idle 才有产品 sense |
| **"一句话派 REQ"** | sisyphus 派出 22K 行 PR | 至少 200 字 description，最低门槛 |
| **"hit 1 次就改 sisyphus"** | 把异常当 pattern | >= 3 次同类才动手；1 次 hit 只 log 不动 |
| **"AI 替我写 REQ description"** | 产品定义被 AI 替 | 红线，绝对不让 |
| **"全凭感觉决策"** | 因为没数据看 | 等 #241 落了用 SQL；之前只 trust strong signals |

## 8. Cap 列表（hard limit）

不是建议，是 hard limit。超过就 defer：

- 每周派 REQ：**3-5 个**
- 每天 review PR：**≤ 5 个**
- 每月 sisyphus release：**1 次**（哪怕 trivial）
- REQ description 字数：**≥ 200 字**
- 周末工作时间：**0**
- hit pattern 触发改 sisyphus：**≥ 3 次同类**

## 9. 当前已识别 issue 跟 phase 的对应

按 phase 重新审视，不按"立了 issue 就要做"逻辑：

| Issue | Phase 对应 | 现在做不做 |
|---|---|---|
| **#248 thanatos M3 落地** | 0.x → 1.x 必须 | ✅ 做 |
| **#243 intake preflight (含哲学一致性)** | 0.x 后期 | ⚠️ 撞 3 次再做 |
| **#247 adjustment dispatch** | 0.x → 1.x 必须（dogfood 没它跑不通） | ⚠️ 撞 3 次再做 |
| #240 verifier-fixer same-session | 1.x | ❌ 0.x 不做 |
| #241 agent_turns | 1.x（数据采集） | ❌ 0.x 不做 |
| #242 observability epic | 1.x（闭环） | ❌ 0.x 不做 |
| #244 termination accounting | 1.x（运营层） | ❌ 0.x 不做 |
| #245 weekly retrospective | 2.x（运营层） | ❌ 不做 |
| #246 PR queue health | 2.x（运营层） | ❌ 不做 |

**0.x 阶段只做 3 件事**：#248 thanatos / 必要时 #247 / 必要时 #243。其它全部 defer。

## 10. BACKLOG.md 模板

放在 sisyphus 仓根目录或者你工作目录，每周更新：

```markdown
# Sisyphus Product Backlog

## Phase 0.5 (this week, due 周五)
- [ ] thanatos 落地具体步骤

## Phase 0.6 (next week)
- [ ] 5 个 ttpos REQ description（不立即派）
- [ ] 周一批量派
- [ ] 周四看分布

## Phase 0.7 (after 5-REQ batch)
- [ ] 改 sisyphus 基于 pattern (≥3 次同类)

## Backlog (no commit yet)
- ttpos 需求 #1: <200 字 description>
- ttpos 需求 #2: ...
- ...

## 红线（每天看一眼）
- 不一句话派 REQ
- 不 hit-1-stop
- 不周末加班
- 不让 AI 写 REQ description
```

## 11. 一个人 + AI 项目的死亡螺旋警告

如果出现以下任一信号，立即 stop & think：

- 这周派的 REQ reject rate > 50%
- 周末工作 > 4 小时
- 连续 2 周 sisyphus 没 release
- BACKLOG.md 没更新 > 2 周
- 自己说不出"sisyphus 这周帮我做完了什么 ttpos 需求"

死亡螺旋的解：**stop coding for 1 day, write retrospective**。问自己：

1. 上次真为 ttpos 业务做事是什么时候？
2. 现在做的所有事跟"挡 80% ttpos 需求"距离多远？
3. 是不是把工具当成了目的？

## 12. Dogfood 期间 prompt 热更

改 `.j2` 模板无需重建镜像。前置一次性配置完成后，每次改模板只需 ~30s。

### 前置（只需做一次）

```bash
# 1. 建 ConfigMap（内容来自本地 .j2 文件）
make hotreload-prompts

# 2. helm 开启挂载 flag
helm -n sisyphus upgrade orch ./orchestrator/helm \
  -f my-values.yaml \
  --set prompts.configMap.enabled=true \
  --set image.tag="<current-sha>" \
  --set runner.image="<current-runner-sha>"
```

### 日常热更（改完模板即跑）

```bash
make hotreload-prompts
```

这条命令做三件事：
1. 把 `orchestrator/src/orchestrator/prompts/` 下所有 `.j2`（含 `verifier/`、`_shared/`、`_shared/hooks/`）同步到 4 个 ConfigMap
2. `kubectl -n sisyphus rollout restart deploy/orch-sisyphus-orchestrator`
3. 等 rollout 完成（`rollout status`）

全程约 30s，新请求即用新模板。

### 实现原理

- k8s ConfigMap key 不允许含 `/`，故 4 个目录层用 4 个独立 ConfigMap，每个挂载到 pod 对应路径：

  | ConfigMap | mountPath |
  |---|---|
  | `sisyphus-prompts` | `/etc/sisyphus/prompts/` |
  | `sisyphus-prompts-verifier` | `/etc/sisyphus/prompts/verifier/` |
  | `sisyphus-prompts-shared` | `/etc/sisyphus/prompts/_shared/` |
  | `sisyphus-prompts-shared-hooks` | `/etc/sisyphus/prompts/_shared/hooks/` |

- `SISYPHUS_PROMPTS_DIR=/etc/sisyphus/prompts` 注入到 pod，`prompts/__init__.py` 读环境变量优先使用该目录；目录为空（ConfigMap 尚未建立）时自动回退 package dir（prod 安全）。
- `prompts.configMap.enabled=false`（默认）= 完全不挂 ConfigMap，prod 路径不变。

## 13. 一句话总结

**节奏不靠规划，靠 cap。** 设定每周 3-5 REQ / 每月 1 个 sisyphus release / 周末空白 / 不一句话派 REQ。Cap 是 hard limit，超过就 defer。

**Cap 比 plan 重要**——一个人 + AI 的项目死在"我应该再做点"的本能上，活在"我今天不做了"的纪律上。

## 13. 运维 Troubleshooting

### Helm field manager 冲突（`conflict with "kubectl-patch"`）

**症状**

```
Error: UPGRADE FAILED: conflict occurred while applying object sisyphus/orch-sisyphus-orchestrator /v1, Kind=ConfigMap:
  Apply failed with 1 conflict: conflict with "kubectl-patch" using v1: .data.SISYPHUS_SKIP_ACCEPT
```

**根因**

之前用过 `kubectl patch` / `kubectl edit` 直接改了 helm chart 管的资源。helm upgrade 走 server-side apply，看见同一字段同时被 `helm` 和 `kubectl-patch` 两个 field manager 声明 → conflict。

**诊断**

```bash
kubectl get <kind> <name> -n sisyphus -o yaml --show-managed-fields \
  | grep -A5 managedFields
```

找到占用字段的 manager（如 `kubectl-patch`），确认是哪个字段冲突。

**临时解（短期 unblock）**

选项 A — 让 helm 抢回 ownership（推荐）：

```bash
helm template <release> <chart> -f values.yaml \
  | kubectl apply -f - --server-side --force-conflicts -n sisyphus
```

选项 B — 手动把字段 ownership 转回 helm：

```bash
kubectl patch <kind> <name> -n sisyphus \
  --type=json \
  -p='[{"op":"replace","path":"/data/<FIELD>","value":"<VALUE>"}]' \
  --field-manager=helm
```

选项 B 只转一个字段，冲突字段多时改用选项 A。

**长期修法**（跟踪 #297，待后续 PR）

用脚本批量把所有字段 ownership 归还给 helm，或在 helm chart 上加 `force-conflicts` annotation。#297 继续 open 跟踪。

**预防原则**

- **禁止** 用 `kubectl patch` / `kubectl edit` 修改 chart 管的资源字段。
- 要改配置 → 改 `values/` 下的 yaml + `helm upgrade --reuse-values -f new-values.yaml`。
- 紧急临时改用 `helm upgrade --set key=value` 而非直接 kubectl 写字段。

## 14. Issue triage discipline（2026-05-04 dogfood 加固）

**触发场景**：跑真链路 dogfood 时，每条 REQ 串过 7 个 surface（业务仓 / runner / orch / BKD / lab / accept-env / GH CI），第一次跑必撞洞。本能反应是"撞一条立 issue 立 PR 修"——这是 §11 死亡螺旋。

### 14.1 撞墙优先进 BACKLOG.md，不立 issue

- 1-2 次 hit → [BACKLOG.md](../BACKLOG.md) 一行 log（"# REQ-x 撞墙记录"区）
- ≥3 次同类 hit → 才立 issue（playbook §8 hard cap）
- 立 issue = 隐性承诺修；BACKLOG.md = 只观察

跑完一批（5 条 REQ）回头看 BACKLOG.md，找 ≥3 次同类的 pattern 才动手改 sisyphus。

### 14.2 优先级 label 体系

| label | 含义 | 0.x 处理 |
|---|---|---|
| **P0** | 阻塞 80% ttpos 命中率 / Phase A prerequisite | 立刻做 |
| **P1** | 服务 sisyphus 改进，间接提升命中率（接入面真痛点） | 跟 P0 串行做 |
| **P3** | meta / self-monitoring | 1.x 之后 |
| **defer-1.x** | sisyphus 自身 polish / 1-shot fix / devx 加速 / 工程美化 | 0.x 不开 issue 详情，1.x 阶段重审 |

### 14.3 defer-1.x 触发条件（立 issue 时强制自查）

任一即应打 `defer-1.x`：

1. 是 sisyphus 自身 fix/refactor，不是接入面（业务仓 / lab / runner Pod / GH CI）问题
2. <3 次同类 hit 证据（看 BACKLOG.md，找不到 3 条就 defer）
3. 是工程美化（重命名、entrypoint 扩展、devx 加速、observability 加深）
4. 是 1.x 阶段架构重构（preset 化、契约 v2、跨仓抽象）

### 14.4 defer-1.x 解封条件

跑完一个 batch（5 条 REQ）后 review 一次：

- 期间在 BACKLOG.md 撞 ≥3 次同类 → 移除 `defer-1.x`，标 P1
- 没撞过 → 保持 defer
- 撞了 1-2 次 → 留 defer，BACKLOG.md log 继续累积

### 14.5 例外

下列即使是 sisyphus 自身 fix 也不 defer：

- 真生产事故（pod 起不来 / runner 全死 / orch crash loop）
- 影响多 user 的 root cause issue（如 #333 三方契约）
- P0 / P1 接入面已识别痛点

## 15. Dogfood 全链路推进操作手册（强推模式）

> 0.x 阶段唯一允许的工作模式：**强推 ttpos REQ，撞洞挂 issue，不修 bug 先**。
> 跟 §14 互补：§14 管"立不立 issue"，§15 管"立完之后做不做"。

### 15.1 操作循环（贴墙上）

```
1. 派 3-5 条 ttpos REQ（≥200 字 description 各）
2. 跑全链路
3. 撞洞 → 挂 issue（或 BACKLOG.md log）→ 跳过这条 REQ → 派下一条
4. 不开 PR、不派 fixer、不 manual fix（除阻塞类）
5. 周末看 issue list，≥3 次同类才下周修
```

### 15.2 撞洞判别表

| 场景 | 处理 |
|---|---|
| 第 1 次撞某洞 | 挂 issue / BACKLOG log → 跳过这条 REQ |
| 同一洞第 2 次（多条 REQ 撞同一洞） | **阻塞类，最 dirty 的 hack 让它过**（不修对） |
| 多条 REQ 撞不同洞 | 一律 log + skip |
| ≥3 次同类 hit | 周末 review 时立项修 |

### 15.3 阻塞类 hack 原则

- ssh / kubectl 直接干，**不入 helm chart、不入 sisyphus orch 代码**
- 30 秒能 dirty 过去 → hack；30 秒不行 → log + skip 这一关
- hack 不是 fix，1 周后跟 issue 一起 review

### 15.4 死规矩 4 条（dogfood 期间不能违反）

1. **不开 PR review**——本地改本地推 main，没有 review 环节（这周特批，1.x 之后恢复）
2. **不派 sub-issue / fixer agent**——sisyphus 自己想干啥都按住
3. **不改 sisyphus prompt / state machine / verifier**——只允许改 raw bug（"clone 路径错了"这种）
4. **不写新文档**（playbook / 契约 / retrospective 全停手）——全部留到 5 条 REQ 跑过之后

### 15.5 AI 协作红线

详见 [CLAUDE.md "Dogfood 期间 AI 协作红线"](../CLAUDE.md)。AI 老把 user 拐去修 bug——session 开始 3 句话内 user 自己念一遍 §15.1 + §15.4，告诉 AI 哪条违反了立刻打断。

### 15.6 退出条件

- 5 条 REQ 跑过（含 hack）→ 进入 review week，整理 issue pattern → 系统化修
- 1 周内 0 条 REQ 通过 → 触发 §11 死亡螺旋自检（停 1 天写 retrospective）

### 15.7 真稳定的判据

| 级别 | 标准 |
|---|---|
| v0.x stable | 5 条 ttpos REQ 端到端跑过，期间 sisyphus 主链 0 改动 |
| v1.x stable | 50 条 REQ 跨仓跨场景跑过，期间 sisyphus 月均 1 改动 |
| v2.x stable | 500 条 REQ 多 user 多场景，sisyphus 季均 1 改动 |

**稳定不是改出来的，是不改改出来的。**
