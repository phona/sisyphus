# REQ-fix-orch-source-repo-tag-1777824479

> closes phona/sisyphus#362

## Why

5/3 dogfood Phase D 派 REQ-chore-d-dogfood-marker-1777823112 立即 escalate：

```
clone.exec: default_base="feat/develop-hwt", base_overrides={}
clone.failed: "validating base branch 'feat/develop-hwt' for phona/sisyphus"
=== FAIL clone: base branch 'feat/develop-hwt' not found on origin for phona/sisyphus ===
```

REQ 是给 ttpos-flutter 的（base `feat/develop-hwt`，跟仓 default branch 一致），
但 orchestrator 跑去 clone `phona/sisyphus@feat/develop-hwt` —— sisyphus 没这分支。

根因：

- 部署 helm `env.default_involved_repos: [phona/sisyphus]`（单仓自 dogfood 配的）。
- 直接 analyze 入口 + ctx 没 involved_repos + tags 也没 `repo:<org>/<name>` →
  multi-layer fallback 落到 L4 = `phona/sisyphus`。
- 实际 REQ 应当 clone `ZonEaseTech/ttpos-flutter`，但 orch 缺一个**per-REQ
  source repo override** 的概念。把 helm `default_involved_repos` 改成 ttpos
  会破坏所有 sisyphus 自 dogfood REQ；只能临时绕开（`--no-intent` 直 BKD 模式
  失去 orch pipeline dogfood 价值）。

类似 case：跨 project / 跨 lab 派 REQ 都会撞，cross-repo env spec
（#342 #359 Phase B）已实现 manifest reader，但**入口仍假设源仓 = helm 默认**。

## What changes

加一条 `source-repo:<org>/<name>` BKD intent issue tag，作为 multi-layer
involved_repos fallback 的 **L0**（最高优先级），赢过 intake / ctx / `repo:` /
settings.default：

- ` _clone.py` 新增 `_extract_source_repo_tags()` + `_SOURCE_REPO_TAG_PREFIX`
  常量，复用现有 `_REPO_SLUG_RE` 校验规则。`_extract_repo_tags` 重构为
  `_extract_tags_with_prefix(tags, prefix, *, log_event)` 内部 helper，
  避免两套 prefix 解析逻辑并行漂移。
- `resolve_repos()` 在 layer table 顶部插入新行 `("tags.source-repo", ...)`，
  其它层顺序不变。
- 多个 `source-repo:` tag 合并成列表（跟 `repo:` 一致）。
- 非法 slug 走 `clone.invalid_source_repo_tag` warning。

### 为什么 L0 而不是 L3 同层？

`source-repo:` 跟 `base:` tag 是同语义 —— 显式 per-REQ override，赢过其它
一切。而现有 `repo:` tag 是「ctx 完全空时的 fallback」，加性、不抢 ctx。两个
不同 use case：

| tag | 优先级 | 典型用途 |
|---|---|---|
| `source-repo:` (L0) | 顶层 | helm 默认错了，本 REQ explicitly 换仓 |
| `repo:` (L3) | ctx 空时 fallback | intake 没跑、helm 没配，BKD tag 手动声明 |

用户在 BKD intent issue 上挂 `source-repo:ZonEaseTech/ttpos-flutter` 即可
让 orch 把 clone 目标改到 ttpos-flutter，不必改 helm values，不必走 intake，
也不影响其它 REQ。

## Impact

- 修：所有「helm default = repoA、本 REQ 给 repoB」场景（dogfood 期跨仓 REQ
  全卡的根因）
- 不破坏：现有 `repo:` tag / settings.default_involved_repos 行为完全不变
  （仅在新 layer 没命中时按原顺序走）
- 不动 contract：BKD `base:<branch>` / `base:<repo>:<branch>` tag 不变
- 不动 sisyphus-clone-repos.sh：clone helper 只接收最终 repo list，不管是
  哪一层算出来的

## Affected specs

- `multi-layer-involved-repos-fallback`：
  - MODIFIED 一条 Requirement（4-layer → 5-layer，新 L0）+ 1 个 scenario
    (S1 优先级)
  - ADDED 一条 Requirement 约束 `source-repo:` tag 的 slug 校验 + extractor
    行为 + 与 `repo:` tag 互不干扰
  - ADDED 一个 scenario 复现 closes #362 的实证 case

## Affected code

- `orchestrator/src/orchestrator/actions/_clone.py` — 新增 prefix /
  extractor / docstring；refactor `_extract_repo_tags` 走共享 helper；
  `resolve_repos` 加 L0 layer
- `orchestrator/tests/test_contract_clone_fallback_direct_analyze.py` —
  扩 `test_resolve_repos_layer_priority` 覆盖 L0
- `orchestrator/tests/test_contract_source_repo_tag_override.py` (新文件) —
  per-REQ override + closes #362 实证 case + extractor 校验
