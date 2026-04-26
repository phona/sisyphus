"""dev_cross_check：开发交叉验证（M1 checker，for-each-repo）。

多仓重构后：每个 source repo 在 runner pod 里挂在 /workspace/source/<repo-name>/。
checker 遍历 /workspace/source/*，含 Makefile `ci-lint` target 的仓逐一跑
`BASE_REV=$(git merge-base HEAD origin/<default_branch>) make ci-lint`；任一失败整体红。
每仓失败时 echo `=== FAIL: $repo ===` 到 stderr。

ci-lint 是 ttpos-ci 标准契约：仅 lint 变更文件 (BASE_REV 缺失则全量)。

feat/<REQ> 缺失语义（REQ-checker-no-feat-branch-fail-loud-1777123726）：
sisyphus-clone-repos.sh 把 involved_repos 列的每个仓 clone 进 /workspace/source/，
所以 /workspace/source/<repo>/ 存在 ⇒ analyze-agent 声明该仓 involved，**必须**
推到 feat/<REQ> 分支。fetch + checkout feat/<REQ> 失败 → fail loud（fail=1 + 拒绝
silent-pass 信息），不再 [skip] 噤声。Makefile 缺 ci-lint target 仍 [skip]（仓本身
不可 lint，与 analyze-agent 无关）。

BASE_REV 计算（REQ-fix-base-rev-default-branch-1777214183）：先读
`git symbolic-ref refs/remotes/origin/HEAD` 拿仓**实际**默认分支（`origin/<name>`，
clone 时自动设置），再退到静态链 `origin/main → origin/master → origin/develop →
origin/dev → 空字符串`。修这条之前默认分支非 main/develop/dev 的仓（如 ttpos-server-go
+ ttpos-flutter 默认 `release`）会全 fall through 到空，ci-lint 退化全量扫，
BASE_REV 增量功能形同虚设——见 REQ-audit-business-repo-makefile-1777125538 audit-report.md §2.3。
"""
from __future__ import annotations

import asyncio
import time

import structlog

from .. import k8s_runner
from ._types import CheckResult

log = structlog.get_logger(__name__)

_TAIL = 2048


def _build_cmd(req_id: str) -> str:
    """遍历 /workspace/source/*/，先切到 feat/<REQ>，对含 ci-lint target 的仓跑 make ci-lint。

    Empty-source guard（防 silent-pass）：
    - /workspace/source 不存在或没任何子目录 → 直接 exit 1
    - 任一 cloned repo 缺 feat/<REQ> 分支 → fail=1 + 拒绝 silent-pass stderr
      （REQ-checker-no-feat-branch-fail-loud-1777123726：clone helper 已克隆该仓 ⇒
      该仓 involved；analyze-agent 没推 feat 分支是结构性失败，不再 [skip] 噤声）
    - 遍历后 ran=0（所有仓都缺 ci-lint target）→ exit 1
      checker 不能在零信号情况下报 pass。

    BASE_REV 计算（REQ-fix-base-rev-default-branch-1777214183）：先 resolve 仓**实际**
    默认分支，再退到静态链。
    1. `git symbolic-ref --short refs/remotes/origin/HEAD` → 例如 `origin/release`，
       `git clone` 时自动设置；剥掉 `origin/` 前缀拿到 `<default_branch>` 名
    2. 顺序尝试：`origin/<default_branch>` → `origin/main` → `origin/master`
       → `origin/develop` → `origin/dev` → 空字符串（ci-lint 退化为全量扫描）

    修这条前默认分支非 main/develop/dev 的仓（ttpos-server-go / ttpos-flutter 默认
    `release`）BASE_REV 必空 → 增量 lint 形同虚设。
    """
    return (
        "set -o pipefail; "
        "if [ ! -d /workspace/source ]; then "
        '  echo "=== FAIL dev_cross_check: /workspace/source missing — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "repo_count=$(find /workspace/source -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l); "
        'if [ "$repo_count" -eq 0 ]; then '
        '  echo "=== FAIL dev_cross_check: /workspace/source empty (0 cloned repos) — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "fail=0; "
        "ran=0; "
        "for repo in /workspace/source/*/; do "
        '  name=$(basename "$repo"); '
        # 暴露 fetch 真错（之前 2>/dev/null 把 auth/network/dns 失败全吞，silent-pass
        # guard 一律打成"branch 不存在"，REQ-ttpos-pat-validate-v4 实证：branch 真在
        # origin，fetch 失败原因被掩盖 → verifier 8min 思考还判不准）。
        # 现在：捕 stderr，rev-parse 单独验 origin ref 是否真到位，失败时把 fetch 真
        # 错塞进 silent-pass 信息里。
        f'  fetch_err=$(cd "$repo" && git fetch origin "feat/{req_id}" 2>&1 || true); '
        f'  if ! (cd "$repo" && git rev-parse --verify "refs/remotes/origin/feat/{req_id}" >/dev/null 2>&1); then '
        f'    echo "=== FAIL dev_cross_check: $name has no feat/{req_id} branch reachable on origin — refusing to silent-pass ===" >&2; '
        '    echo "git fetch stderr: $fetch_err" >&2; '
        "    fail=1; "
        "    continue; "
        "  fi; "
        f'  cd "$repo" && git checkout -B "feat/{req_id}" "origin/feat/{req_id}" >/dev/null 2>&1; '
        # ci-lint target 检测：用 `make -p -n` 解析 Makefile（含 include 子 mk）
        # 而非 `grep '^ci-lint:'` 顶层（实证 ttpos-server-go：ci-* 在
        # ttpos-scripts/lint-ci-test.mk via `include`，顶层 grep 漏判 →
        # checker 误报"0 source repos eligible" silent fail）。
        # `make -p -n || true` 抑制 Makefile 评估期错误（如 include 子 mk 缺 env / target body
        # 求值挂）；外层 `set -o pipefail` 否则让 pipe 整体 fail → grep 看不到 → 误判"0 eligible"。
        # 实证 ttpos v8 (REQ-ttpos-validate-end-to-end) 卡这个：ttpos-server-go Makefile 里
        # ci-lint target 真存在 (via include) 但 make 评估时某处 exit 非零，pipefail 把 grep 吞光。
        '  if [ -f "$repo/Makefile" ] && (cd "$repo" && (make -p -n 2>/dev/null || true) | grep -q \'^ci-lint:\'); then '
        # BASE_REV 计算：先读 origin/HEAD 符号引用拿仓实际 default_branch（git clone
        # 自动设置），再退静态链 main → master → develop → dev → ""。
        # 修这条前默认分支非 main/develop/dev 的仓（ttpos-server-go / ttpos-flutter 默
        # 认 release）整条链全 miss → BASE_REV 必空 → ci-lint 退化为全量扫，增量 lint
        # 形同虚设（实证 REQ-audit-business-repo-makefile-1777125538 audit-report.md §2.3）。
        '    default_branch=$(cd "$repo" && git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed \'s@^origin/@@\' || true); '
        '    base_rev=$(cd "$repo" && ( '
        '              ([ -n "$default_branch" ] && git merge-base HEAD "origin/$default_branch" 2>/dev/null) '
        '              || git merge-base HEAD origin/main 2>/dev/null '
        '              || git merge-base HEAD origin/master 2>/dev/null '
        '              || git merge-base HEAD origin/develop 2>/dev/null '
        '              || git merge-base HEAD origin/dev 2>/dev/null '
        '              || echo "")); '
        # 先跑 ci-setup 拉 deps（per ttpos-ci 契约，对齐 ci-go.yml 流：ci-setup → ci-lint）。
        # 实证 ttpos v9：ci-lint 第一次跑触发 go mod download 大量 alibabacloud SDK，
        # 没 ci-setup 预热 → 5min timeout (-1)。`|| true` 不让 setup 失败拦 lint。
        '    echo "=== dev_cross_check (ci-setup): $name ==="; '
        '    (cd "$repo" && make ci-setup) || true; '
        '    echo "=== dev_cross_check (ci-lint): $name (BASE_REV=$base_rev) ==="; '
        '    if ! (cd "$repo" && BASE_REV="$base_rev" make ci-lint); then '
        '      echo "=== FAIL: $name ===" >&2; '
        "      fail=1; "
        "    fi; "
        "    ran=$((ran+1)); "
        "  else "
        '    echo "[skip] $name: no make ci-lint target"; '
        "  fi; "
        "done; "
        'if [ "$ran" -eq 0 ] && [ "$fail" -eq 0 ]; then '
        f'  echo "=== FAIL dev_cross_check: 0 source repos eligible (no make ci-lint target on feat/{req_id}) — refusing to silent-pass ===" >&2; '
        "  exit 1; "
        "fi; "
        "[ $fail -eq 0 ]"
    )


async def run_dev_cross_check(
    req_id: str,
    *,
    timeout_sec: int = 900,
) -> CheckResult:
    """kubectl exec runner -- <for-each-repo make ci-lint>。"""
    rc = k8s_runner.get_controller()
    cmd = _build_cmd(req_id)
    log.info(
        "checker.dev_cross_check.start",
        req_id=req_id, timeout=timeout_sec,
    )
    started = time.monotonic()

    try:
        exec_result = await asyncio.wait_for(
            rc.exec_in_runner(req_id, cmd, timeout_sec=timeout_sec),
            timeout=timeout_sec + 10,
        )
    except TimeoutError:
        log.error(
            "checker.dev_cross_check.timeout", req_id=req_id,
        )
        return CheckResult(
            passed=False, exit_code=-1,
            stdout_tail="", stderr_tail=f"dev cross-check 超时 {timeout_sec}s",
            duration_sec=time.monotonic() - started, cmd=cmd,
        )

    passed = exec_result.exit_code == 0
    log.info(
        "checker.dev_cross_check.done",
        req_id=req_id,
        passed=passed, exit_code=exec_result.exit_code,
        duration_sec=round(exec_result.duration_sec, 2),
    )
    return CheckResult(
        passed=passed,
        exit_code=exec_result.exit_code,
        stdout_tail=exec_result.stdout[-_TAIL:],
        stderr_tail=exec_result.stderr[-_TAIL:],
        duration_sec=exec_result.duration_sec,
        cmd=cmd,
    )
