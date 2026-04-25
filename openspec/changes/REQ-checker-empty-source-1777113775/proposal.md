# REQ-checker-empty-source-1777113775: fix(checkers): mechanical checker silent-pass when /workspace/source empty

## 问题

三个机械 checker（`spec_lint` / `dev_cross_check` / `staging_test`）在 runner pod
里都用同一个 shell 模板遍历业务源码：

```bash
for repo in /workspace/source/*/; do
  ...
done
[ $fail -eq 0 ]
```

如果 `/workspace/source` 不存在 / 是空目录 / 全部子目录都被 skip（feat 分支
fetch 不到、Makefile target 不全、openspec/changes/<REQ>/ 没写）——

- bash 默认 glob 行为下，没匹配项就用字面 pattern (`/workspace/source/*/`) 当
  唯一一项进 loop
- 字面项 `cd` 失败 → fetch test 触发 skip 路径 → `continue`
- 循环结束 `fail` 还是 `0`
- exit code 0 = **PASS**

也就是说 checker 在**完全没跑任何工具**的情况下报 pass。orchestrator 据此
emit `*_PASS` 事件，状态机推下一 stage，直到 pr_ci_watch 才可能在 GitHub 上
看见根本没 PR / PR 早就红了。这条噪音决策被记到 `stage_runs` /
`verifier_decisions`，腐蚀 M7+M14e 看板的"checker 准确率"指标。

## 根因

shell `for ... in <glob>` + 没有 nullglob + 没有 ran-counter，导致
"零迭代 / 全跳过 = 全 OK" 的误读，是三个 checker 共有的形状缺陷。
checker `_build_cmd()` 写的时候只想着"有 repo 时怎么跑"，没把"没 repo"
当成必须显式失败的状态。

silent-pass 跟 sisyphus 哲学正面冲突：

- **机械 checker 是唯一裁判** —— 在完全没收集到信号的情况下报 pass，
  等于把"没数据"当成"全 OK"，是裁判失职
- **失败先验，再试错** —— 没数据应当走 `REVIEW_RUNNING` 让 verifier-agent
  主观判断（多半是 escalate "源码丢了"），而不是装作绿灯通过

## 方案

在 `_build_cmd()` 模板的开头和结尾加三道 guard，每道都直接 `exit 1`
并 echo `=== FAIL <stage>: ... ===` 到 stderr 让 verifier 看清：

### Guard A：源目录存在性

```bash
if [ ! -d /workspace/source ]; then
  echo "=== FAIL <stage>: /workspace/source missing — refusing to silent-pass ===" >&2
  exit 1
fi
```

抓 PVC 没挂 / clone helper 从没跑过的情况。

### Guard B：源目录非空

```bash
repo_count=$(find /workspace/source -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
if [ "$repo_count" -eq 0 ]; then
  echo "=== FAIL <stage>: /workspace/source empty (0 cloned repos) — refusing to silent-pass ===" >&2
  exit 1
fi
```

抓"目录存在但里面 0 个 cloned repo"的情况。`find -mindepth 1 -maxdepth 1 -type d`
精确数子目录数，不会被普通文件 / 残留 lock 文件误判。

### Guard C：至少一个 repo 真跑过工具

主循环里加 `ran` 计数器，**只在真的 invoked 工具的分支** `ran=$((ran+1))`：

- spec_lint：仓有 `feat/<REQ>` 分支 + 有 `openspec/changes/<REQ>/` 目录
- dev_cross_check：仓有 `feat/<REQ>` 分支 + Makefile 含 `^ci-lint:` target
- staging_test：仓有 `feat/<REQ>` 分支 + Makefile 含 `^ci-unit-test:` + `^ci-integration-test:` target

循环结束后：

```bash
if [ "$ran" -eq 0 ]; then
  echo "=== FAIL <stage>: 0 source repos eligible (no feat/<REQ> branch with required artifact) — refusing to silent-pass ===" >&2
  exit 1
fi
```

抓"目录里有 repo，但 agent 没往 `feat/<REQ>` push 任何东西 / 没写 openspec /
没贴 ttpos-ci 标准 Makefile target"的情况。

### 三 checker 同样改

silent-pass 是模板共有缺陷，三个 checker 同形修，避免再有第四个 checker
照着旧模板 copy-paste 时复发。

## 取舍

- **为什么 guard 做在 shell cmd 里而不是 Python 包装层** —— checker 的
  pass/fail 信号唯一来源是 `exec_in_runner` 的退码，让 shell 自己 exit 1
  最简单；Python 层加 result.exit_code 后处理会跟"timeout/exception → fail"
  的现有路径分叉。

- **为什么 0 eligible 算 fail 不算 skip** —— 在 sisyphus 状态机里，进入
  `spec_lint` / `dev_cross_check` / `staging_test` checker 的前提是 analyze /
  dev 阶段已经 done，agent 已经声明它写完了。这种情况下 0 eligible 仅有两种
  解释：**(i) agent 谎报 done 但其实没 push**、**(ii) 环境问题（PVC 丢失 /
  clone 失败）**。两种都该走 `REVIEW_RUNNING` 给 verifier 兜，绝不是绿灯。

- **为什么用 `find -mindepth 1 -maxdepth 1 -type d` 而不是 `shopt -s nullglob`** ——
  `shopt` 是 bash builtin，但 cmd 在 `kubectl exec ... -- bash -c` 下面跑，
  混杂层多了一旦 shell 不是 bash（busybox sh 之类）就静默失败。`find` 是
  POSIX coreutils，在 sisyphus runner pod 镜像里恒定存在，行为确定。

- **跨 stage 的 silent-pass 漏洞还有么** —— `pr_ci_watch` 是直接调
  GitHub REST API，没有 `/workspace/source/*` 形状的循环；不需要同样的 guard。
  本 REQ 仅覆盖三个吃 runner pod shell 模板的 checker。

## 影响面

- 仅改 `orchestrator/src/orchestrator/checkers/{spec_lint,dev_cross_check,staging_test}.py`
  的 `_build_cmd()` 函数；checker action / 状态机 / event 表无变化。
- 退码 0 的语义不变（"任一仓任一检查失败 → exit 1"原样保留）。
- 退码 1 增加 4 条新原因（missing / empty / 0-eligible × 3 stages），verifier
  prompt **不需要改** —— stderr `=== FAIL <stage>: ... ===` 自描述。
- 历史上跑过 silent-pass 的 REQ 不回溯（事件已经记录在 `stage_runs`），
  本次仅修向前的行为。
