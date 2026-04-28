# Proposal: dev_cross_check BASE_REV 优先用仓真实 default_branch

## 背景

REQ-audit-business-repo-makefile-1777125538 audit-report.md §2.3 实证：
`ttpos-server-go` / `ttpos-flutter` 默认分支都是 `release`。当时 sisyphus
`dev_cross_check` checker 计算 BASE_REV 用静态链：

```bash
base_rev=$(git merge-base HEAD origin/main 2>/dev/null \
        || git merge-base HEAD origin/develop 2>/dev/null \
        || git merge-base HEAD origin/dev 2>/dev/null \
        || echo "")
```

`origin/main` / `origin/develop` / `origin/dev` 三条 ref 在这两个仓都不存在
→ 整条链 fall-through → `BASE_REV=""` → `ci-lint` 退化为全量扫。

后果：
- ttpos-ci 契约的"仅 lint 变更文件"语义失效
- golangci-lint 全量扫把仓里历史 lint debt 全爆给 verifier-agent，可能误判成
  本 REQ 引入的 lint 错（audit-report.md §2.4 已记录）
- 大仓全量扫 5 min+，runner pod 还得绕 timeout

`docs/integration-contracts.md §2.2` 当时把这种行为标成"已知降级"，但没说明
"为什么不读仓自己的默认分支"——其实没有不能读的理由。

## 目标

让 `dev_cross_check` checker 优先读 **仓真实 default_branch** 算 BASE_REV，
仅当读不到时才退到静态链兜底。

## 方案

`git clone` 自动设置 `refs/remotes/origin/HEAD` 符号引用，指向仓被 clone 时
GitHub 上的默认分支。`sisyphus-clone-repos.sh` 用 `git clone --depth=1 + git
config remote.origin.fetch +refs/heads/*:refs/remotes/origin/* + git fetch
--all --tags`，整套不会清掉 `origin/HEAD` 引用。

dev_cross_check 在算 BASE_REV 前多一步：

```bash
default_branch=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null \
                 | sed 's@^origin/@@' || true)
base_rev=$(([ -n "$default_branch" ] && git merge-base HEAD "origin/$default_branch" 2>/dev/null) \
        || git merge-base HEAD origin/main 2>/dev/null \
        || git merge-base HEAD origin/master 2>/dev/null \
        || git merge-base HEAD origin/develop 2>/dev/null \
        || git merge-base HEAD origin/dev 2>/dev/null \
        || echo "")
```

变化：
1. 多一行 `git symbolic-ref` resolve `origin/HEAD` → 拿到例如 `release`
2. 在静态链最前面插入 `[ -n "$default_branch" ] && git merge-base HEAD "origin/$default_branch"`，
   由 `[ -n ... ]` gate 防默认分支为空时给 `origin/` 触发歧义错误
3. 静态链补 `master`（pre-existing 漏项；`master` 比 `dev` 更常见但当时不在链里）
4. 全 miss 仍退空字符串（保留向后兼容："仓没默认分支 ref" 不应让 checker 红）

## 影响

- ✅ ttpos-server-go (release) / ttpos-flutter (release) 等仓 BASE_REV 计算从"必空"变成"实际 SHA"，ci-lint 走增量
- ✅ 默认 main / develop / dev 的仓行为不变（前两条之一一定命中）
- ✅ 没有 `origin/HEAD` 符号引用的仓（罕见；手动 mirror clone 等）退到静态链
- ⚠️ 假如某仓真有 `origin/HEAD` 但本地 ref 没追到（比如未 fetch），`git
  symbolic-ref` 仍能读 `.git/refs/remotes/origin/HEAD` 文件本身，所以即使没 fetch
  default_branch 那个 ref，仍能打印出 branch 名；之后 merge-base 失败时退到静态链

## 改的文件

- `orchestrator/src/orchestrator/checkers/dev_cross_check.py` —— 唯一一处真改业务逻辑
- `orchestrator/tests/test_checkers_dev_cross_check.py` —— 更新断言 + 加 BASE_REV 顺序专项测试
- `orchestrator/src/orchestrator/actions/create_dev_cross_check.py` —— header 注释更新
- `orchestrator/src/orchestrator/prompts/{analyze,bugfix}.md.j2`
- `orchestrator/src/orchestrator/prompts/verifier/dev_cross_check_{success,fail}.md.j2`
- `docs/integration-contracts.md` §2 表格 + §2.2 BASE_REV 约定
- `docs/architecture.md` dev-cross-check 表格 + 流程示例
- `docs/state-machine.md` `dev-cross-check-running` 行
- `docs/cookbook/ttpos-flutter-makefile.md` §5 BASE_REV 约定（Flutter 视角）

## 不在范围

- 修 `scripts/sisyphus-clone-repos.sh` 里 `git checkout main` / `git reset --hard
  origin/main` 的硬编码（默认分支非 main 时 update 路径会挂；属于另一类 bug，独
  立 REQ 修）
- 改 ttpos-ci 仓 `ci-flutter.yml` 把 `BASE_REF` 拼写改回 `BASE_REV`（业务仓的事，
  cookbook §8 已记录建议）
- 给 staging_test / spec_lint checker 加 BASE_REV（这俩 checker 不需要 BASE_REV）
