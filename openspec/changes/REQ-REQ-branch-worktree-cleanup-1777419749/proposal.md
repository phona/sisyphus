# REQ-REQ-branch-worktree-cleanup-1777419749: fix branch and worktree cleanup bugs

## 问题

sisyphus 仓库积累了 745+ 个 git 分支和 117 个 git worktree，严重污染仓库：

- **495 个 `bkd/*` 分支**：BKD workspace 创建，结束后不删除
- **159 个 `feat/REQ-*` 分支**：PR 合并后不删除
- **135 个已合并到 main**：其中 132 个 bkd/ + 3 个 feat/REQ-*

## 根因分析

1. **PR merged hook 不删分支**：`.github/workflows/sisyphus-pr-merged-hook.yml` 在 PR 合并时只通知 orchestrator (`POST /admin/req/{id}/pr-merged`)，没有删除 feat/REQ-* 分支
2. **BKD workspace 结束后不删 bkd/* 分支**：`engine.py` 的 `_cleanup_runner_on_terminal` 只清理 K8s Pod + PVC，不清理 git worktree + bkd/* 分支
3. **runner_gc 不兜底**：`gc_orphan_pods`/`gc_orphan_pvcs` 只扫 K8s 资源，不扫 git 资源

## 方案

### 1. GitHub Actions — PR merged 后自动删分支

修改 `.github/workflows/sisyphus-pr-merged-hook.yml`：在 "Notify orchestrator of PR merge" 步骤之后，加一步：

```yaml
      - name: Delete merged branch
        if: steps.extract.outputs.req_id != ''
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          branch="${{ github.event.pull_request.head.ref }}"
          echo "Deleting merged branch: $branch"
          gh api repos/${{ github.repository }}/git/refs/heads/$branch -X DELETE \
            || echo "Branch $branch already deleted or not found"
```

### 2. Orchestrator — REQ 终态时清理 bkd/* worktree + 分支

修改 `orchestrator/src/orchestrator/engine.py`：

在 `_cleanup_runner_on_terminal` 函数中，`cleanup_runner` 之后，加一步清理 git worktree + 分支的逻辑：

- 在 runner pod 里执行（git 仓库在 PVC 里）
- 扫描 `git worktree list --porcelain` 中位于 `worktrees/` 下的 worktree
- 逐个 `git worktree remove --force` + `git branch -D`
- 用 `try/except` 包裹，失败不阻塞主流程（fire-and-forget）

## 取舍

- **为什么不把 git 清理合进 runner_gc** —— runner_gc 扫的是 K8s 资源（Pod/PVC），git 资源是 runner pod 内的文件系统状态，扫描维度不同。合进去让 runner_gc 职责不纯。而且终态清理是即时行为（transition 时触发），不是周期性 GC。
- **为什么通过 runner pod exec 而不是 orchestrator 本地执行** —— git 仓库在 runner pod 的 PVC 里，orchestrator 没有直接访问权限。通过 `exec_in_runner` 在 pod 内执行是最小侵入方案。
- **为什么只清理 `bkd/*` worktree 而不清理 feat/REQ-* 的 worktree** —— feat/REQ-* 分支在 PR 合并后由 GitHub Actions 删除（方案 1），对应的 worktree 在 PR 合并时已被清理。剩下的 orphan 主要是 bkd/* 的。
- **为什么用 `--force` 删 worktree** —— worktree 可能有未跟踪文件，`--force` 确保清理成功。
- **为什么 git cleanup 失败不阻塞状态机** —— 跟 cleanup_runner 的语义一致：fire-and-forget，失败由 runner_gc 兜底或人工处理。

## 影响面

- 改 `.github/workflows/sisyphus-pr-merged-hook.yml`：加 delete merged branch 步骤
- 改 `orchestrator/src/orchestrator/engine.py`：`_cleanup_runner_on_terminal` 加 git 清理
- 新增 unit test：`test_engine.py` 中验证 `exec_in_runner` 被调用 + 失败不阻塞
- 不动 `runner_gc.py` / `state.py` / `actions/` / migrations / BKD 集成层。
