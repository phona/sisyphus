# REQ-router-noise-filter-1777109307: fix(webhook): drop issue.updated without REQ tag and intent tag

## 问题

BKD webhook 把整 project 所有 `issue.updated` 推过来，包括跟任何 sisyphus REQ 工作流
都不相关的 issue：用户手动改的 lab 票、看板列上随手拖动改 status、贴评论等。

`webhook.py` 现有的早期 noise filter（line 138-152）只挡 `session.completed`：

```python
if (
    body.event == "session.completed"
    and not router_lib.extract_req_id(tags)
):
    ...skip
```

`issue.updated` 走不到这条 filter。它们流到下游：

1. `obs.record_event("webhook.received", ...)` —— observability 表灌满无关事件，污染 Q1/Q2 看板
2. `router_lib.derive_event(body.event, tags)` —— 跑完后绝大多数返 `None`
3. `event is None` → return skip （**功能上没事**，但白跑一遭）
4. `event_seen` 表写一条 dedup 记录 + mark_processed —— Postgres 写放大，纯噪音

实测：BKD 一个有几十张 issue 的 project，光"用户拖卡"就能每分钟产几次 `issue.updated`，
对应 sisyphus 这边几次无效写。

## 根因

早期 noise filter 范围太窄：只覆盖 `session.completed`，没覆盖 `issue.updated`。
设计当时只考虑了 "session 事件可能是别的 REQ 的孤儿" 这一种噪音；没考虑 BKD 把 project
所有 issue 的 update 都广播这件事。

## 方案

扩展早期 noise filter，把 `issue.updated` 也纳入：

- `issue.updated` 唯一两种合法触发场景：
  1. **已 tag REQ-N 的 issue 上的更新** —— 如 race fallback 路径（`issue.updated` 看到
     stage tag + result tag 后兜底 fire 主链事件，见 router.py:226-256）
  2. **用户在 intent issue 上打 `intent:intake` / `intent:analyze` tag** —— 触发新 REQ
     创建（INTENT_INTAKE / INTENT_ANALYZE 入口）
- 其他都是噪音，**早 skip 早开**

filter 条件：`issue.updated` 且**没** REQ-N tag 且**没** intent 入口 tag → skip。

具体做法（webhook.py 改动）：

1. 把现有 session filter 旁边的判断重组为更通用的 noise filter 段。
2. 新增 `has_req_tag = bool(router_lib.extract_req_id(tags))` 和
   `has_intent_tag = "intent:intake" in tags or "intent:analyze" in tags`。
3. session.completed 路径不变（保持 "没 REQ tag → skip"）。
4. 新增 issue.updated 路径："没 REQ tag 且没 intent tag → skip"。
5. 命中时一样：写 mark_processed（让 BKD 不重发）+ return skip + log.debug + 不调
   obs.record_event。

## 取舍

- **不挡 session.failed**：scope 控住。session.failed 比 session.completed 罕见，
  noise 量小；保留它落 obs 也方便排查 "哪个无关 BKD agent 挂了"。
- **保留 dedup 写**：filter 命中也调 `dedup.mark_processed(pool, eid)`。理由跟现有
  session filter 一致：BKD 至少一次重发，让重发也走同一 skip 路径，避免重复 BKD `get_issue`
  / log spam。`event_seen` 行写一条 NULL→NOW 的 update 是 cheap，比下游路径便宜。
- **不引新 setting / env**：filter 是硬规则，不需要运维开关。要"暂时关 filter 排查问题"
  的场景没业务价值。
- **filter 在 tag resolution 之后**：跟现有 session filter 同位置。issue.updated 几乎都
  带 tags（BKD payload 自带），所以前置 BKD `get_issue` 不会被 noise 触发。结构对称，
  改动小。
- **`init:STATE` tag 不算入合法入口**：实际使用都是 `init:STATE` + `REQ-N` 配对（中流
  注入既有工作流），单独打 `init:STATE` 不带 REQ 就过 filter 没意义；需要这种用法的
  场景另外加 `intent:analyze` 即可。

## 兼容性

- 既有 REQ：本来就走 has_req_tag=True 路径，行为不变。
- 既有 INTENT 入口：has_intent_tag=True 路径，行为不变。
- 现有 session.completed filter 路径：行为不变（同条件、同返回）。
- 唯一变化：之前会 fall through 到 `event is None → skip` 的 issue.updated noise，现在
  在更早处 skip。downstream 行为完全相同（最终都 return skip + mark_processed）；只是少
  跑 `obs.record_event` 和 `derive_event`。无 migration。
