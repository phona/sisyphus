# REQ-fix-clone-per-repo-base-1777808457

> closes phona/sisyphus#345

## Why

orchestrator 在 5/3 self-dogfood 期间多次撞 clone failure：

```
{"event": "clone.exec", "default_base": "develop", "base_overrides": {"phona/sisyphus": "main"}}
[sisyphus-clone-repos] validating base branch 'develop' for phona/sisyphus ...
=== FAIL clone: base branch 'develop' not found on origin for phona/sisyphus ===
```

`phona/sisyphus` 没有 `develop`，部署时配的 per-repo override
`env.default_base_branches: {phona/sisyphus: main}` 应该把它带到 `main`，但实际
log 里 `base_overrides` 已经是 `{"phona/sisyphus": "main"}`，clone 脚本却仍按
`develop` 校验 —— 说明 override 被脚本无视了。

根因：
- `start_analyze` 把 `settings.default_base_branches`（key 是 helm
  values 里写的 `phona/sisyphus` owner/repo 形式）合并进 `base_overrides`。
- `_clone.py` 透传成 `--base-for phona/sisyphus main`。
- `sisyphus-clone-repos.sh` 的 `_resolve_base()` 按 `basename`（`sisyphus`）查
  `REPO_BASE_MAP`，命中不到 → 落回 `--base develop` → 校验失败。

实证 5/3 deploy 撞 3 次（smoke v1/v2 都因此 escalate）；任何把 helm
`env.default_base_branches` 写成 `<owner>/<repo>` 形式的多仓部署都受影响。

## What changes

两处归一化（contract = base_overrides key 永远是 basename）：

1. **`scripts/sisyphus-clone-repos.sh`**：`--base-for KEY VAL` 在写入
   `REPO_BASE_MAP` 时把 KEY 归一到 basename（剥 `<owner>/` 前缀 + `.git` 后缀）。
   防御性修复 —— `--base-for owner/repo X` 跟 `--base-for repo X` 都正确。
2. **`orchestrator.router.normalize_base_overrides()`**：把 dict 所有 key 归一
   到 basename。`actions/start_analyze.py` 跟
   `actions/start_analyze_with_finalized_intent.py` 在合并完
   `settings.default_base_branches` 之后调一次，保证下游所有消费者
   （`_clone.py` / `resolve_base_branch`）拿到的 dict key 形式一致。

两改一前一后冗余 —— 任一独立都能修 bug，叠加后契约更明确：
- 上游归一化 = canonical form 只有一种
- 下游归一化 = 防御 future caller 直接漏归一

## Impact

- 修：所有 helm `env.default_base_branches` 配 `<owner>/<repo>` 形式的部署
- 不破坏：原 `<basename>` 形式的配置（双向兼容）
- 不动 contract：BKD `base:<repo>:<branch>` tag 形式不变（router
  `extract_base_branches` 已经按 basename 写 key）

## Affected specs

- `server-side-clone-and-no-env-fallback`：ADDED 一条 Requirement
  约束 base override key 归一化语义（顺手覆盖 `--base-for` 接受双形式）

## Affected code

- `scripts/sisyphus-clone-repos.sh` — `--base-for` 入口归一
- `orchestrator/src/orchestrator/router.py` — 新增 `normalize_base_overrides`
- `orchestrator/src/orchestrator/actions/start_analyze.py` — 调归一
- `orchestrator/src/orchestrator/actions/start_analyze_with_finalized_intent.py` — 同上
- `scripts/test_clone_repos.sh` — 新增 2 个 case
- `orchestrator/tests/test_router.py` — 新增 `test_normalize_base_overrides`
