# Sisyphus Product Backlog

> 产品 owner 自用执行清单。**不是 issue tracker** —— issue 立项纪律见 [docs/playbook.md §14](docs/playbook.md)。

---

## Phase 0.5（this week，due 周五）

聚焦 P0 三件套（不开新坑）：

- [ ] **#369** ttpos-flutter ci-setup 缺 .fvm/flutter_sdk symlink → runner 跑不动 ci-lint
- [ ] **#333** 业务仓 ↔ ttpos-arch-lab ↔ sisyphus orch 三方联动契约（root cause，70% 接入面问题收敛在此）
- [ ] **#248** thanatos M3 端到端 ttpos acceptance dogfood

## Phase 0.6（next week，after P0 三件套通）

- [ ] 写 5 个 ttpos REQ description（≥200 字 each，BACKLOG 不在 BKD 不在 issue）
- [ ] 周一批量派
- [ ] 周二-周四撞墙记录到下方"撞墙记录"区，**不立 issue**
- [ ] 周四 review pattern（≥3 次同类才立 issue）

## Phase 0.7（after 5-REQ batch）

- [ ] 解封 defer-1.x：跑完 batch 后 review，BACKLOG 撞 ≥3 次的提回 P1，否则保持 defer
- [ ] 改 sisyphus 基于 pattern（不 1-shot，不 hit-1-stop）
- [ ] release v0.x.y

---

## ttpos REQ description 草稿区（不立即派）

> 每条 ≥200 字。10 分钟想清楚 > 10 秒糊出去。

- REQ-1: <待写>
- REQ-2: <待写>
- REQ-3: <待写>
- REQ-4: <待写>
- REQ-5: <待写>

---

## 撞墙记录区（dogfood 期间，每条 REQ 跑全链路时撞洞填这里）

> 格式：`YYYY-MM-DD REQ-xxx <stage>: <一行描述>`
> 撞 ≥3 次同类才立 issue。1-2 次只 log。

### 已撞 (running tally)

<!-- 例：
- 2026-05-04 REQ-foo accept-env-up: adb device 硬编 localhost:5555 不匹配 redroid emulator-5554
- 2026-05-05 REQ-bar accept-env-up: 同上
- 2026-05-06 REQ-baz accept-env-up: 同上 → ≥3 次，promote → 立 issue (#321 已立)
-->

(尚无记录)

### Pattern 统计（周四 review）

<!-- 同类 hit ≥ 3 次的提到这里：
- pattern: <一句话> | hits: 3 | next: 立 issue / 提 P1
-->

(尚无)

---

## 红线（每天看一眼）

- ❌ 不一句话派 REQ（≥200 字 description）
- ❌ 不 hit-1-stop（≥3 次同类才动手）
- ❌ 不周末加班
- ❌ 不让 AI 写 REQ description
- ❌ **不撞墙立刻立 issue**（默认走 BACKLOG，[playbook §14](docs/playbook.md)）

## 死亡螺旋自检（playbook §11）

每周一回答一遍：

1. 上次真为 ttpos 业务做事是什么时候？
2. 现在做的事跟"挡 80% ttpos 需求"距离多远？
3. 这周 sisyphus 帮我做完了哪条 ttpos 需求？

第 3 题答不出 = 死亡螺旋触发，stop & write retrospective。
