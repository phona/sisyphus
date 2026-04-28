# REQ-checker-no-feat-branch-fail-loud-1777123726: fix(checkers): dev_cross_check + staging_test fail loud when no feat branch exists

## 问题

REQ-checker-empty-source-1777113775 给三个机械 checker（spec_lint / dev_cross_check /
staging_test）加了三层 silent-pass 防护：
- Guard A：`/workspace/source` 不存在
- Guard B：`/workspace/source` 子目录数为 0
- Guard C：遍历后 `ran == 0`（每个仓都被跳过）

Guard C 只在**所有**仓都被跳过时触发。但每仓循环里还有一层条件 silent-skip：

```bash
if ! (cd "$repo" && git fetch origin "feat/<REQ>" ... ); then
  echo "[skip] $name: no feat branch / not involved"
  continue
fi
```

如果 clone helper 给某 REQ 克隆了 2+ 个仓（来自 `involved_repos`），但 analyze-agent
只往其中一部分推了 feat 分支，其余仓的 `[skip]` 静默放过，`ran > 0` 也会让 Guard C 不
触发——剩余仓的 ci-lint / 测试通过即整体 PASS，**漏掉**了 analyze-agent 没推的仓的真实
失败。

具体场景：analyze-agent 在 BKD intent 阶段声明 `involved_repos=[A, B]`，sisyphus
clone 两个仓到 `/workspace/source/{A,B}/`。agent 只在 repo-A 上做了改动并推到
`feat/<REQ>`，repo-B 没动。当前：
- spec_lint：repo-A 通过 + repo-B `[skip]` → ran=1 → PASS（不一定错——见下方对 spec_lint 的豁免）
- dev_cross_check：repo-A `make ci-lint` 通过 + repo-B `[skip]` → ran=1 → PASS（**错**：repo-B 的 lint 没有被跑过，但在 declared-involved 列表里）
- staging_test：同上（**错**：repo-B 的测试没有被跑过）

## 方案

只对 `dev_cross_check` 和 `staging_test` 改：把每仓 "no feat branch" 的 `[skip] +
continue` 改成 fail-loud：

```bash
if ! (cd "$repo" && git fetch origin "feat/<REQ>" ... ); then
  echo "=== FAIL <stage>: $name has no feat/<REQ> branch on origin — refusing to silent-pass ===" >&2
  fail=1
  continue
fi
```

设计要点：
1. **per-repo 归因**：消息里包含具体仓名，verifier-agent 看 stderr 立刻知道是 repo-B
   缺失，不需要去看 stage_runs 表里的 cmd 反推。
2. **不增加 ran**：缺 feat 分支的仓不算 "ran 过"，但 fail=1 已经记账。trailing
   `[ $fail -eq 0 ]` 会失败退出，无须依赖 Guard C。
3. **Guard C 保留但条件改严**：`if [ "$ran" -eq 0 ] && [ "$fail" -eq 0 ]`——
   只在仓有 feat 分支但全都缺 Makefile target 时触发（这才是 Guard C 现在唯一负责
   兜底的场景）。这样语义清晰：feat 缺失 / Makefile target 缺失 / lint 失败 三种
   stderr 信号互不混淆。
4. **沿用 silent-pass 拒绝信息**：保留 "refusing to silent-pass" 子串，verifier 提示词
   现有 substring matcher 不用改。
5. **continue 而非立即 exit 1**：保持遍历完成，把每个有问题的仓都列出来，verifier
   一次拿全 stderr，不用反复触发。

## spec_lint 不改的理由

spec changes 经常 collapse 到 spec_home repo（详见 CLAUDE.md "B.4 spec home repo"）：
跨仓 REQ 的 proposal / design / 跨仓集成 spec 只写在 home repo 一份，consumer repo 的
`openspec/changes/<REQ>/` 可能为空甚至 feat 分支都不需要 push（如果 consumer 仓本次
没有自身 spec 变更）。所以 "cloned but no feat branch" 在 spec_lint 上下文中可能合法。

dev_cross_check（lint 变更文件） + staging_test（跑 unit + integration）则不同：被
clone 的仓如果不属于本次改动，根本不会出现在 `involved_repos`；既然 cloned 了，必是
analyze-agent 声明要改的，必须 push feat 分支。

## 影响范围

只动两个文件 + 测试：
- `orchestrator/src/orchestrator/checkers/dev_cross_check.py`
- `orchestrator/src/orchestrator/checkers/staging_test.py`
- 测试更新：`orchestrator/tests/test_checkers_empty_source_guard.py` 拆开
  原本三 checker 共参化的 Guard C 测试（spec_lint 保留 "0 source repos eligible"
  断言；dev/staging 移到新 REQ 的测试文件）。
- 新增 `orchestrator/tests/test_checkers_no_feat_branch_fail_loud.py` 覆盖 CNFB-S1..S6
  共 6 条 scenario。

不改：
- `spec_lint`（语义不同，见上方理由）
- `pr_ci_watch`（不走 `/workspace/source` 模板）
- Postgres schema / Metabase 看板（错误形状已经被现有 stage_runs / verifier_decisions
  捕获，新 fail-loud 信息只是更精准的 stderr）
- BKD agent prompts（verifier substring matcher 已经认 "refusing to silent-pass"）
