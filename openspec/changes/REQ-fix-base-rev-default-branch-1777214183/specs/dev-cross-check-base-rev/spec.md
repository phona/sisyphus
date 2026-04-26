# dev-cross-check-base-rev (delta)

## ADDED Requirements

### Requirement: dev_cross_check MUST 优先用仓真实 default_branch 算 BASE_REV

The dev_cross_check checker SHALL resolve each cloned repo's actual default branch from `refs/remotes/origin/HEAD` (set automatically by `git clone`) before computing `BASE_REV`, and MUST fall through to a static candidate chain `main → master → develop → dev → empty string` only when the symbolic ref is unresolvable or its branch has no merge-base with `HEAD`. The shell command produced by `_build_cmd` MUST execute the following per cloned repo under `/workspace/source/*/` that has a `ci-lint` Makefile target:

```bash
default_branch=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null \
                 | sed 's@^origin/@@' || true)
base_rev=$(([ -n "$default_branch" ] && git merge-base HEAD "origin/$default_branch" 2>/dev/null) \
        || git merge-base HEAD origin/main 2>/dev/null \
        || git merge-base HEAD origin/master 2>/dev/null \
        || git merge-base HEAD origin/develop 2>/dev/null \
        || git merge-base HEAD origin/dev 2>/dev/null \
        || echo "")
BASE_REV="$base_rev" make ci-lint
```

The system MUST gate the dynamic merge-base attempt behind `[ -n "$default_branch" ]` so that an unset / empty `default_branch` does not invoke `git merge-base HEAD origin/`, which is ambiguous. When all attempts fail, `BASE_REV` MUST be the empty string so that `ci-lint` recipes honoring `${BASE_REV:+--new-from-rev=$BASE_REV}` degrade to a full scan.

#### Scenario: DCC-S1 BASE_REV 用仓 origin/HEAD 解析的默认分支（release 仓）

- **GIVEN** a cloned repo at `/workspace/source/ttpos-server-go/` whose
  `git symbolic-ref refs/remotes/origin/HEAD` resolves to
  `refs/remotes/origin/release` (因 GitHub 默认分支为 `release`)
- **WHEN** `dev_cross_check._build_cmd("REQ-X")` is executed in the runner pod
- **THEN** the shell sets `default_branch=release`, then `BASE_REV=$(git
  merge-base HEAD origin/release)` 计算成功，`make ci-lint` 收到非空
  `BASE_REV`，仅扫该仓 feat 分支相对 `release` 的变更文件

#### Scenario: DCC-S2 origin/HEAD 缺失时退到静态 main 链

- **GIVEN** a cloned repo where `git symbolic-ref --short
  refs/remotes/origin/HEAD` exits non-zero (e.g. mirror clone) AND `origin/main`
  exists
- **WHEN** `dev_cross_check._build_cmd("REQ-X")` is executed
- **THEN** `default_branch` 为空，由 `[ -n "$default_branch" ]` gate 跳过
  branch-specific merge-base，落到 `git merge-base HEAD origin/main`，
  `BASE_REV` 取 main merge-base SHA

#### Scenario: DCC-S3 默认分支既不是 main 也不是 master/develop/dev 但 origin/HEAD 命中

- **GIVEN** a repo whose default branch is `trunk` and `origin/HEAD` 正确指向
  `origin/trunk`
- **WHEN** dev_cross_check 跑
- **THEN** `default_branch=trunk`，`git merge-base HEAD origin/trunk` 计算成功；
  整条静态链不被触达（短路）

#### Scenario: DCC-S4 全 miss 退空字符串，ci-lint 退化全量扫

- **GIVEN** a repo with no `origin/HEAD` symbolic ref and no `origin/main`
  / `origin/master` / `origin/develop` / `origin/dev` refs（reusable mirror clone
  scenario）
- **WHEN** dev_cross_check 跑
- **THEN** `BASE_REV=""`，`make ci-lint` 接到空字符串走全量扫；这是已知降级路径，
  不报 silent-pass / fail（已有 `_TAIL` 机制把 stderr/stdout 汇报给 verifier）

#### Scenario: DCC-S5 静态链顺序固定 main → master → develop → dev

- **GIVEN** _build_cmd 的输出
- **WHEN** 检查 `default_branch=` 之后的子串顺序
- **THEN** `git merge-base HEAD origin/main` 出现在 `git merge-base HEAD
  origin/master` 之前；`master` 在 `develop` 之前；`develop` 在
  `git merge-base HEAD origin/dev 2>/dev/null` 之前
