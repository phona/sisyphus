"""dev_admission 机械层（M14d）：每个 dev agent 完成后做两项 sisyphus 侧复验。

1. **scope 越界检查**：git diff --name-only 查本 dev agent 有没改自己 scope 外的文件
   - pre-commit-acl.sh (in-band hook) 已拦了一道，这层是防 hook 被 --no-verify 跳过
2. **manifest.pr.{repo, number} 都填齐**：dev agent 必须开 PR 并回写 manifest

两项任一失败 → CheckResult(passed=False)。上层（verifier / gate）收了 False 决定
走 fix 还是 escalate。

主观层 verifier（dev_success）在 PR2 框架上层跑，本 checker 只做机械验证。
"""
from __future__ import annotations

import asyncio
import fnmatch
import time

import structlog

from .. import k8s_runner
from . import manifest_io
from ._types import CheckResult

log = structlog.get_logger(__name__)

_TAIL = 2048
_DIFF_TIMEOUT = 30


def _match_any(path: str, patterns: list[str]) -> bool:
    """fnmatch 风格匹配；pattern 以 / 结尾视为目录前缀。"""
    for pat in patterns:
        # 目录前缀：`foo/` 匹配 `foo/**`
        if pat.endswith("/"):
            if path.startswith(pat) or path == pat.rstrip("/"):
                return True
            continue
        # glob 或直接等
        if fnmatch.fnmatch(path, pat):
            return True
        if path == pat:
            return True
        # pattern 是目录前缀（不带 /）则匹配其下所有文件
        if path.startswith(pat + "/"):
            return True
    return False


def check_scope(changed_files: list[str], scope: list[str]) -> list[str]:
    """返回越界文件列表（空 = 全部在 scope 内）。

    scope 空列表视为"未限制"，返空。
    """
    if not scope:
        return []
    return [f for f in changed_files if not _match_any(f, scope)]


async def _git_diff_changed(req_id: str, base_ref: str, repo_cwd: str) -> list[str]:
    """在 runner pod 里跑 git diff --name-only <base>...HEAD，返 file list。

    repo_cwd: 相对 /workspace 的 repo 路径（通常是 source/<repo>）。
    """
    cmd = f"cd /workspace/{repo_cwd} && git diff --name-only {base_ref}...HEAD"
    rc = k8s_runner.get_controller()
    exec_result = await asyncio.wait_for(
        rc.exec_in_runner(req_id, cmd, timeout_sec=_DIFF_TIMEOUT),
        timeout=_DIFF_TIMEOUT + 10,
    )
    if exec_result.exit_code != 0:
        raise RuntimeError(
            f"git diff 在 {repo_cwd} 失败（exit {exec_result.exit_code}）："
            f"{(exec_result.stderr or '').strip()[:200]}"
        )
    return [line.strip() for line in exec_result.stdout.splitlines() if line.strip()]


def check_pr_manifest(manifest: dict) -> str | None:
    """dev 完成后 manifest.pr.{repo, number} 都该填了。

    返 None = OK；返 str = 错误原因。
    """
    pr = manifest.get("pr")
    if not isinstance(pr, dict):
        return "manifest 缺 pr 段"
    if not pr.get("repo"):
        return "manifest.pr.repo 未填"
    num = pr.get("number")
    if not isinstance(num, int) or num < 1:
        return f"manifest.pr.number 未填或非法：{num!r}"
    return None


async def run_dev_admission(
    req_id: str,
    *,
    task_scope: list[str] | None = None,
    repo_cwd: str = "source",
    base_ref: str = "origin/main",
) -> CheckResult:
    """复验：scope + manifest.pr。

    Args:
        req_id: REQ 标识
        task_scope: 本 dev agent 的 scope（manifest.parallelism.dev[i].scope）；
                    None/[] = 跳过 scope 检查（单 dev 模式）
        repo_cwd: 跑 git diff 的 cwd（相对 /workspace）
        base_ref: git diff 的对比基线（默认 origin/main）
    """
    started = time.monotonic()

    # 1. manifest PR 检查（早出：读 manifest 失败没法继续）
    try:
        manifest = await manifest_io.read_manifest(req_id)
    except manifest_io.ManifestReadError as e:
        return CheckResult(
            passed=False, exit_code=2,
            stdout_tail="", stderr_tail=f"read manifest 失败：{e}"[-_TAIL:],
            duration_sec=time.monotonic() - started,
            cmd="read_manifest",
        )

    pr_err = check_pr_manifest(manifest)
    if pr_err:
        log.info("checker.dev_admission.pr_missing", req_id=req_id, error=pr_err)
        return CheckResult(
            passed=False, exit_code=1,
            stdout_tail="", stderr_tail=pr_err[-_TAIL:],
            duration_sec=time.monotonic() - started,
            cmd="check_pr_manifest",
        )

    # 2. scope 检查（task_scope 为空 = 单 dev 模式，跳过）
    if task_scope:
        try:
            changed = await _git_diff_changed(req_id, base_ref, repo_cwd)
        except TimeoutError:
            return CheckResult(
                passed=False, exit_code=-1,
                stdout_tail="", stderr_tail=f"git diff timeout after {_DIFF_TIMEOUT}s",
                duration_sec=time.monotonic() - started,
                cmd=f"git diff {base_ref}...HEAD",
            )
        except Exception as e:
            return CheckResult(
                passed=False, exit_code=2,
                stdout_tail="", stderr_tail=f"git diff 失败：{e}"[-_TAIL:],
                duration_sec=time.monotonic() - started,
                cmd=f"git diff {base_ref}...HEAD",
            )

        out_of_scope = check_scope(changed, task_scope)
        if out_of_scope:
            log.info(
                "checker.dev_admission.scope_violation",
                req_id=req_id, scope=task_scope, out_of_scope=out_of_scope,
            )
            msg = (
                f"越界文件 {len(out_of_scope)} 个，scope={task_scope}：\n"
                + "\n".join(f"  - {f}" for f in out_of_scope[:20])
            )
            return CheckResult(
                passed=False, exit_code=1,
                stdout_tail="\n".join(changed[:50])[-_TAIL:],
                stderr_tail=msg[-_TAIL:],
                duration_sec=time.monotonic() - started,
                cmd=f"git diff {base_ref}...HEAD",
            )

    duration = time.monotonic() - started
    log.info(
        "checker.dev_admission.ok",
        req_id=req_id, scope=task_scope,
        pr_repo=manifest.get("pr", {}).get("repo"),
        pr_number=manifest.get("pr", {}).get("number"),
        duration_sec=round(duration, 2),
    )
    return CheckResult(
        passed=True, exit_code=0,
        stdout_tail="", stderr_tail="",
        duration_sec=duration, cmd="dev_admission",
    )
