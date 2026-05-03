# Design: orchestrator cross-repo env impl

## 模块切分

把 R1 / R2 / R3 / R6 的逻辑全部抽到 `orchestrator/cross_repo_env.py`（纯 Python，无 I/O 依赖）。这样：

- 单测不需要 mock kubectl exec 或 git；
- create_accept.py 只负责把 runner-pod 真实 I/O（`exec_in_runner`、`git show origin/<branch>:.sisyphus/env.yaml`）翻译成 cross_repo_env 可消化的输入；
- 后续若有别的 action 也要用拓扑排序（比如 dev_cross_check 想按多仓拓扑跑），可以直接复用。

## R1 manifest 形态

`Manifest` dataclass：

```python
@dataclass(frozen=True)
class Manifest:
    emits: tuple[str, ...]
    needs: tuple[str, ...]                   # OWNER/REPO
    inputs: dict[str, tuple[str, str]]       # ENV_VAR -> (repo, field)
    branches: dict[str, str]                 # class -> actual branch name
```

`parse_manifest(text: str) -> Manifest` 跑 yaml.safe_load + schema 校验：

1. 顶层只允许 `emits` / `needs` / `inputs` / `branches` 四 key；多余 key → 报错
2. `emits` 每项必须 non-empty str
3. `needs` 每项必须匹配 `OWNER/REPO` regex（`[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+`）
4. `inputs` 左侧必须合法 shell var name（`[A-Za-z_][A-Za-z0-9_]*`），右侧 `OWNER/REPO.field`，且 repo ∈ needs
5. `branches` 默认 `{develop: develop, release: release}`，缺省合并（不是替换）—— 用户可以只覆盖 develop 单 key

## R2 拓扑解析

`resolve_topology(source_repo, manifest_loader)` —— BFS 遍历 needs graph，DFS 检环，输出叶子优先的拓扑序（Kahn 算法变体）。`manifest_loader` 是 callable `repo -> Manifest | None`，把 git/runner I/O 推给 caller。

- 缺 manifest 的 needs 仓视为 leaf node（emits 空）→ R2-S10
- 同一仓被多次引用 → dedup，取最早能放下的位置（叶子优先）
- 检环失败给出"A → B → A"形式的环路名字 → R2-S8

## R3 workspace 路径

`workspace_dir_map(repos)` —— 输入仓全名列表，输出 `{full_name: subdir_basename}`：

- short name 唯一 → `<repo_short>` (`/workspace/source/<repo_short>/`，跟现有 sisyphus-clone-repos.sh 路径一致)
- short name 重复 → 改用 `<owner>__<repo_short>` 双下划线
- 单仓 REQ 自然走 short name，跟现状一字不差 → R3-S13

注意：sisyphus-clone-repos.sh 当前固定克隆到 `/workspace/source/<basename>/`。collision 场景需要传 `--basename` 参数，本 REQ 暂时只在 cross_repo_env.py 输出 mapping，clone 落盘交给 helper 按 short name 处理。如果实际撞短名（极罕见，因为 ttpos 全部在 ZonEaseTech org），需在 follow-up REQ 扩展 helper —— 此处先用 mapping 占位 + 单测覆盖逻辑；helper 改造留 ttpos 实际撞名时再做。

## R6 branch resolver

`resolve_branch(source_branch, source_manifest, needs_manifest, branch_exists)` —— 按 R6 4 步算法：

1. `branch_exists(needs_repo, source_branch)` → True：返该 branch（同名优先）
2. 反查 source_manifest.branches：哪个 class 的 value 是 source_branch（或 source_branch 是 base branch 自己）→ class
3. 若得到 class，查 needs_manifest.branches[class] → 候选 branch；`branch_exists(needs_repo, candidate)` 验
4. fail → 返 `BranchResolution(branch=None, reason=...)` —— caller emit ACCEPT_ENV_UP_FAIL

源分支是 `feat/REQ-xxx`：sisyphus-clone helper 现在拉 source repo 时实际 base 写在 ctx.branch。但 spec R6 的 source branch 概念是"本 REQ 当前checkout 的 branch 名字"，对 `feat/REQ-xxx` 来说同名分支只有 source repo 有。算法降级到 step 2/3：去找 class。

class 反查策略：
- 如果 source 分支正好等于 source_manifest.branches[<class>] 之一 → class 是这个 class（默认 develop / release）
- 否则把 source 分支的 base 视为 source_manifest 默认 develop class（feat branch 默认从 develop 切）

把 base branch 推断逻辑放在 `resolve_branch` 调用前的 `infer_branch_class(source_branch, source_manifest) -> str` helper，不混进核心逻辑。

## R4 / R10 编排

`create_accept.py` 多层路径流程：

```
1. ensure_runner_with_clone(source_repo + branch)             # 已就绪通常 idempotent skip
2. read source manifest from runner pod
3. if no manifest or no needs: 走 R8 单层路径（既有逻辑）
4. else:
   a. recursively load manifests for all needs (use git fetch + git show)
   b. resolve topology -> ordered list
   c. for each needs repo (excluding source): branch resolution + clone
   d. bundle = {}
   e. layers = []
   f. for repo in topo:
        start = monotonic()
        env = build_env_for_layer(repo, manifest, bundle)
        result = exec_in_runner(make accept-env-up at /workspace/source/<dir>)
        duration_ms = (monotonic() - start) * 1000
        if result.exit_code != 0:
          record layer.failed + remaining as skipped + failed_layer
          stage_runs.update_context(failed_layer=..., layers=...)
          return ACCEPT_ENV_UP_FAIL
        parse JSON last line; for each f in manifest.emits: missing -> failed_field
        merge fields into bundle[repo]
        layers.append(success)
   g. dispatch accept-agent with full bundle
```

## migration

`stage_runs.context` 加 JSONB 列（默认 NULL）；旧 row 不 backfill。R10 写入直接 `UPDATE ... SET context = $1` —— 一次写完整 context dict（dict.json）即可，不需 partial merge（一次 accept 一行）。

helper `close_latest_stage_run` 加 `context: dict | None = None` 参数，COALESCE 逻辑保留旧值。

## R5 passthrough

不写专门函数，create_accept 把 manifest.emits 列出的 field 用 `bundle[repo][field] = parsed_json[field]` 直接赋值（preserve native JSON type），dispatch 时整包 json.dumps。R5-S20（数值 / boolean）天然过。

## fail-loud 边界

- manifest yaml 解析崩 / schema 红 → ACCEPT_ENV_UP_FAIL（不 retry）
- needs 仓 git fetch 失败 → ACCEPT_ENV_UP_FAIL
- topology 检出环 → ACCEPT_ENV_UP_FAIL with cycle name
- branch resolution 失败 → ACCEPT_ENV_UP_FAIL with reason `branch_resolution_failed`
- emit field 缺 → ACCEPT_ENV_UP_FAIL with `failed_field`

ACCEPT_ENV_UP_FAIL 走既有 verifier 通道（不新增 escalate 路径）。

## 兼容路径

R8 backward compat 实现策略：检测 `/workspace/source/<source-repo>/.sisyphus/env.yaml` 文件存在。
- 不存在 → 调用 `_run_legacy_single_layer()` —— 完全保留既有 endpoint JSON parsing + thanatos block + lite fallback 逻辑（既有行为零字节差异）
- 存在但 needs 为空 → 同样走 legacy 路径（多 clone / topology 都没必要）

避免 invasive 改造让 R8 测试压力小，且老 REQ 跑起来是 byte-for-byte 不变。
