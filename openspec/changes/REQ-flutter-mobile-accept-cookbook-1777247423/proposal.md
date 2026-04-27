# Proposal: ttpos-flutter mobile accept-env-up Makefile cookbook

## 背景

REQ-flutter-makefile-cookbook-1777133078 已经在 sisyphus 仓里加了
`docs/cookbook/ttpos-flutter-makefile.md`，覆盖 Flutter 仓作为 **source repo**
时的 `ci-lint` / `ci-unit-test` / `ci-integration-test` Makefile recipe。

但该 cookbook §4.3 只用一个最小 Makefile 片段「点了一下」Flutter 仓**自承
integration repo**（不用 arch-lab、不起 emulator、纯 mock backend HTTP 验证）的场景。
没有：

- 决策树（什么时候选 self-hosted、什么时候选 arch-lab）
- mock backend stack 设计（`tests/docker-compose.accept.yml` 该长什么样）
- accept-agent 的 scenario 限制（哪些 UI scenario 验不了）
- SISYPHUS_NAMESPACE 并发隔离的具体玩法
- 排查清单 / 反模式

工程师真要在 Flutter 仓里加 `accept-env-up` 时，§4.3 那 20 行不够用。
本 REQ 把这块内容拆出来单独做一份 cookbook。

## 范围

本 REQ 的产物**全在 sisyphus 仓**：

1. **新增** `docs/cookbook/ttpos-flutter-mobile-accept-env.md` —— Flutter 仓
   自承 integration repo 的 accept-env-up/down 完整食谱（9 节）。
2. **更新** `docs/cookbook/ttpos-flutter-makefile.md` §4.3 —— 留 minimal Makefile
   片段作索引示例，把"完整食谱"指引到新 cookbook；§9 关系表追加一列。
3. **更新** `docs/integration-contracts.md` §4.2.2 —— 加一行引用，让查契约
   的人能找到 mobile self-hosted 路径。

**不改** ttpos-flutter / ttpos-arch-lab 等业务仓本身 —— 那是后续接入实现 REQ
的工作（业务团队按本 cookbook 落地）。

## 方案

新 cookbook `ttpos-flutter-mobile-accept-env.md` 大纲：

```
§0 TL;DR — 3 件事
§1 决策树：什么时候用本 cookbook（vs arch-lab）
§2 repo 布局（自承 integration 后）
§3 mock backend stack: tests/docker-compose.accept.yml
§4 Makefile: accept-env-up / accept-env-down recipe
§5 accept-agent 怎么用 endpoint（scenario 限制）
§6 SISYPHUS_NAMESPACE 与并发隔离
§7 跟其它 cookbook 的关系（三方对比表 + 流程图）
§8 排查清单
§9 不要做的事（反模式）
```

跟另外两份 cookbook 的边界：

| | ttpos-flutter-makefile（已存在） | **本 REQ：ttpos-flutter-mobile-accept-env**（新增） | ttpos-arch-lab-accept-env（已存在） |
|---|---|---|---|
| repo 角色 | source repo | 自承 integration repo | integration repo |
| 关注 target | ci-* | accept-env-up/down | accept-env-up/down |
| emulator | 无 | 无 | 有 |
| 适用面 | 必备 | 过渡 / 团队没 arch-lab | 完整 mobile e2e |

## 关键设计决策

1. **拆 cookbook 而不是膨胀 §4.3**：新 cookbook 单独覆盖 Flutter 自承场景，
   ttpos-flutter-makefile.md 仍以 source repo 视角为主，§4.3 留索引片段。读者
   按角色找文件，不用跳节。

2. **不重复 arch-lab 的 emulator 路径**：本 cookbook 强约定**不起 emulator**。
   要 UI flow 直接用 arch-lab cookbook，不允许在 Flutter 仓里塞 emulator
   helm chart（污染 source repo 角色）。

3. **stdout 末行 JSON 加扩展键 `stack`**：契约必需仍是 `endpoint`；
   `stack: "flutter-self-hosted"` 是非契约扩展，给 accept-agent prompt
   按 lab 来源做条件化（避免在自承 lab 里跑只有 emulator 才能验的 scenario）。

4. **mock backend healthcheck 必填**：`docker compose --wait` 没 healthcheck
   只等到容器 running，不等业务就绪；cookbook 在范本里硬规定 healthcheck。

5. **`SISYPHUS_NAMESPACE` 显式分配 host 端口**：`ports: ["8080"]` 让 compose
   自分配 host 端口，配合 `-p $SISYPHUS_NAMESPACE` 避免并发跑多 REQ 撞车。
   写清楚为什么不能写 `8080:8080`。

## 依赖 / 前置

- `docs/cookbook/` 目录已存在（前两份 cookbook 已落地）
- openspec 已在 sisyphus 仓初始化
- 无跨仓依赖（只改 sisyphus 仓文档）
- 无代码改动（不动 orchestrator/ checker/）
