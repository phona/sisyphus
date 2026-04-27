# Design — analyze 阶段 post-artifact-check

## 上下文

`spec_lint` 只调 `openspec validate` + `check-scenario-refs.sh`，关注
`specs/*/spec.md` 的格式。analyze prompt 里要求的另外两件产物——
`proposal.md` / `tasks.md`——并没有任何机械检查，agent 完全可以挂
`analyze` tag 把 issue 挪到 review 而**只生成空文件甚至完全不生成它们**，
sisyphus 仍会照常推进到 spec_lint。

REQ 的目标：在 analyze 完成 → spec_lint 之前夹一层最便宜的契约校验，
让 "self-reported pass but no artifacts" 立即在主链原地红掉。

## 决策

### 1. 单独 state，不复用 spec_lint 的 empty-source guard

候选 A（在 spec_lint 里加更多 guard）和候选 B（独立 checker）的对比：

| 维度 | A 加 guard | B 新 checker（采纳） |
|---|---|---|
| 关注点分离 | 模糊：spec_lint 既校验 spec 格式又校验产物存在性 | 清晰：artifact_check 校产物存在；spec_lint 校格式 |
| 可观测性 | artifact_checks 一行 stage=spec-lint，混在一起 | artifact_checks 单独一行 stage=analyze-artifact-check，仪表盘可独立看 |
| verifier 决策 | spec_lint fail 触发同一个 verifier prompt | 单独 verifier prompt，bias toward escalate（agent 没干活很难是 spec 错） |
| 改动表面 | 改 spec_lint._build_cmd | 加新 module + 新 transition |

采纳 B。`spec_lint` 已存在的 empty-source guard 保留不动（防 silent-pass 的最后一道
门），新 artifact_check 是更前置、更精确的检查。

### 2. 检查范围 = 三件产物 + 复选框 + spec-home 友好

analyze prompt 顶部 ✅ 清单包含：proposal.md、tasks.md、design.md（可选）、
contract.spec.yaml、spec.md、业务代码、unit test、PR opened with sisyphus label。

机械可校验的子集，**proposal/tasks 累积、spec.md 每仓必有**：

| 产物 | 检查方式 | 为何这样 |
|---|---|---|
| `openspec/changes/<REQ>/proposal.md` | 累积存在 + 非空（任一 eligible 仓） | 跨仓 spec-home 模式下 producer 仓才有 proposal |
| `openspec/changes/<REQ>/tasks.md` | 累积存在 + 非空 + ≥1 个 `- [ ]` / `- [x]` 复选框 | 同上累积 |
| `openspec/changes/<REQ>/specs/*/spec.md` | 每个 eligible 仓至少 1 个非空文件 | spec.md 必须每仓都有（每仓自带 capability） |
| 业务代码 / unit test | **不**机械校 | 不少 REQ 是 docs-only / spec-only，强制业务代码 diff 会误伤 |
| PR 已开 + label sisyphus | **不**机械校 | 跨网络 + GitHub API 限速；交给 pr_ci_watch（它本来就要轮 GH） |
| design.md | **不**机械校 | analyze prompt 写"如需"，可选 |
| contract.spec.yaml | **不**机械校 | 跨仓 REQ 才用；强校会误伤单仓 REQ |

### 3. 多仓遍历语义跟 spec_lint 对齐

依旧 `for repo in /workspace/source/*/`，每仓尝试 fetch + checkout `feat/<REQ>`：
- fetch 失败 → 仓 skip（不算 fail，agent 没改它）
- 所有 eligible 仓 spec.md 通过且累积 has_proposal=1 + has_tasks=1 → exit 0
- 任一 eligible 仓缺 spec.md / 累积 has_proposal=0 / has_tasks=0 → exit 1
- 0 仓 eligible（所有仓都 skip）→ exit 1（拒绝 silent-pass，跟 spec_lint 同语义）

### 4. 触发先后跟 verifier 决策路径

```
ANALYZING --(ANALYZE_DONE)--> ANALYZE_ARTIFACT_CHECKING
  |- pass --(ANALYZE_ARTIFACT_CHECK_PASS)--> SPEC_LINT_RUNNING
  |- fail --(ANALYZE_ARTIFACT_CHECK_FAIL)--> REVIEW_RUNNING
                                              |- verify.pass --> ANALYZE_ARTIFACT_CHECKING --(PASS)--> SPEC_LINT_RUNNING
                                              |- verify.fix --> FIXER_RUNNING (fixer=spec)
                                              |- verify.escalate --> ESCALATED
```

`_verifier._PASS_ROUTING["analyze_artifact_check"]` 走
`(ANALYZE_ARTIFACT_CHECKING, ANALYZE_ARTIFACT_CHECK_PASS)` —— `apply_verify_pass`
会先 CAS REVIEW_RUNNING → ANALYZE_ARTIFACT_CHECKING 再 emit PASS 让原主链
继续推到 SPEC_LINT_RUNNING。

### 5. fixer 倾向：spec

如果 verifier 判 fix（很少见，多数情况下是 escalate"agent 直接没干活"），
fixer 应该是 `spec` 而不是 `dev` —— 缺的是 `openspec/changes/<REQ>/` 下的产物。
verifier prompt 模板里写明这一点。

### 6. 为啥不强制 PR / 业务代码 diff

- PR 检查需要 GitHub REST 调用，有限速 + 网络抖动，不该塞进机械 checker（pr_ci_watch
  本就要拉 PR check-runs，complete-time 检查 PR 存在更自然）
- 业务代码 diff 对 docs/spec-only REQ 是误伤；analyze prompt 也没把它写成"必须"
- 三件 openspec 产物的存在性已经能挡住 90%+ "self-reported pass but no artifacts"
  场景；剩下的少数边缘情况（写了 spec 但没写代码）由 dev_cross_check / staging_test
  通过 lint 0 文件 / test 0 case 检出

### 7. infra-flake retry 复用

复用 `_flake.run_with_flake_retry`：DNS / kubectl exec channel / git fetch 抖动
也可能砸到本 checker。配置开关沿用 `settings.checker_infra_flake_retry_*`。

## Trade-offs

- **新加 1 个 stage** vs **塞进 spec_lint**：付出 1 个 state + 4 transition + 一份
  prompt 的代价，换来观测口径独立 + 关注点分离。判断值得。
- **不校业务代码** vs **校业务代码**：选不校，避免 docs-only REQ 误伤。极端"写
  了 spec 没写代码"由下游 stage 兜。
- **shell 实现** vs **Python**：shell 跟 spec_lint / dev_cross_check / staging_test
  对齐，可以走同一套 kubectl exec + flake retry；改 Python 要新建一套机制，不值得。

## Risks

1. **path 假设**：`/workspace/source/<repo>/openspec/changes/<REQ>/` 的形状假设
   sisyphus clone helper 已 stage（实际上 start_analyze 已经替 agent 做完）。
   如果未来路径约定改了，本 checker 跟 spec_lint 同样需要更新——影响面对齐。
2. **agent 写空文件骗过非空检查**：`-s` 检查仅校 size > 0。若 agent 真要骗，
   写 1 字节也能过。但成本 vs 真正动手写一份合规 proposal 差不多——这是预期外
   的恶意行为，由 verifier 主观判兜底，机械层不打这一仗。
