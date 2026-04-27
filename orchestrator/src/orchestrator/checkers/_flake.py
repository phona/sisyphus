"""infra-flake 模式匹配 + bounded retry（REQ-checker-infra-flake-retry-1777247423）。

三个 kubectl-exec checker（spec_lint / dev_cross_check / staging_test）一次跑挂时，
若 stderr/stdout 命中 INFRA_FLAKE_PATTERNS 里的模式，整 cmd 重跑 max 次（含原跑共
max+1 次 attempt），中间隔 backoff_sec。pattern 表保守：只覆盖 100% 跟 agent 输出无关、
verifier 也只能判 escalate 的网络 / 注册中心 / kubectl exec 抖动。模糊失败（generic
make Error / exit 137 / unauthorized）**不**触发 retry，留给 verifier 主观判。

pr_ci_watch 不用本模块（自有 HTTP retry-until-deadline 模型）。
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

import structlog

from ..k8s_runner import ExecResult

log = structlog.get_logger(__name__)

# (regex, reason_tag) — tag 是稳定短词，artifact_checks.flake_reason 直接落，
# 后续 Metabase 看板按 tag 聚合 infra-flake 比例。新模式按需追加，**不**改既有 tag
# （改了既有 SQL 看板会断）。
INFRA_FLAKE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # ── DNS ────────────────────────────────────────────────────────────
    (re.compile(r"Could not resolve host", re.IGNORECASE), "dns"),
    (re.compile(r"Temporary failure in name resolution", re.IGNORECASE), "dns"),
    (re.compile(r"name or service not known", re.IGNORECASE), "dns"),

    # ── kubectl exec / SPDY channel ────────────────────────────────────
    (re.compile(r"error: unable to upgrade connection"), "kubectl-exec-channel"),
    (re.compile(r"error dialing backend"), "kubectl-exec-channel"),
    (re.compile(r"Error from server: error dialing"), "kubectl-exec-channel"),

    # ── git fetch / GitHub API 5xx / GHA 抖动 ──────────────────────────
    (re.compile(r"RPC failed; (curl 56|HTTP 5\d\d)"), "github-rpc"),
    (
        re.compile(
            r"fatal: unable to access .*?: "
            r"(Could not resolve host|Connection (?:reset|refused|timed out)"
            r"|gnutls_handshake|server certificate verification)"
        ),
        "github-fetch",
    ),
    (re.compile(r"remote end hung up unexpectedly"), "github-fetch"),
    (re.compile(r"\bearly EOF\b"), "github-fetch"),

    # ── container registry / docker pull ───────────────────────────────
    (re.compile(r"TOOMANYREQUESTS|toomanyrequests:", re.IGNORECASE), "registry-rate-limit"),
    (
        re.compile(
            r"Error response from daemon: Get .*?: "
            r"(?:dial tcp .*?(?:i/o timeout|connect: connection refused)"
            r"|net/http: TLS handshake timeout"
            r"|server gave HTTP response to HTTPS client)"
        ),
        "registry-network",
    ),
    (
        re.compile(r"failed to copy: httpReadSeeker: failed open: unexpected status code 5\d\d"),
        "registry-5xx",
    ),

    # ── go mod / language toolchain transient ──────────────────────────
    (
        re.compile(r"go: .*?: dial tcp .*?(i/o timeout|connect: connection refused)"),
        "go-mod",
    ),
    (re.compile(r"go: .*?: reading .*?: 5\d\d"), "go-mod"),
    (re.compile(r"npm ERR! (network|503|ETIMEDOUT|ECONNRESET|ENOTFOUND)"), "npm-network"),

    # ── apt mirror flaky ──────────────────────────────────────────────
    (
        re.compile(
            r"Failed to fetch http(?:s)?://.*?"
            r"(Connection (?:refused|timed out)|Temporary failure resolving)"
        ),
        "apt-mirror",
    ),
]


def classify_failure(stdout_tail: str, stderr_tail: str, exit_code: int) -> str | None:
    """看 stdout/stderr 是否命中 infra-flake 模式。

    Returns:
        reason_tag 字符串（命中），或 None（未命中 / pass）。

    Rules:
      - exit_code == 0 → 总返 None（pass 上不能挂 flake 标签）
      - 优先匹 stderr_tail（kubectl exec channel race / git fetch err 通常打 stderr）
      - 再匹 stdout_tail（部分工具把 err 输出到 stdout）
      - 第一个命中的 pattern 决定 tag（pattern 表顺序即优先级）
    """
    if exit_code == 0:
        return None
    for haystack in (stderr_tail, stdout_tail):
        if not haystack:
            continue
        for pattern, tag in INFRA_FLAKE_PATTERNS:
            if pattern.search(haystack):
                return tag
    return None


async def run_with_flake_retry(
    *,
    coro_factory: Callable[[], Awaitable[ExecResult]],
    stage: str,
    req_id: str,
    max_retries: int,
    backoff_sec: float,
) -> tuple[ExecResult, int, str | None]:
    """跑 coro_factory()；命中 flake 模式则同 cmd 重跑 max_retries 次。

    Args:
        coro_factory: 每次 attempt 调一次，返 ExecResult。**必须**每次返新协程
            （函数本身不能是 awaitable —— 协程一次 await 完就用完）。
        stage: 结构化日志用，例如 "spec_lint" / "dev_cross_check" / "staging_test"。
        req_id: 同上。
        max_retries: 0 = 关闭整套（行为退回 single-shot）。1 = 1 次 retry，
            最多 2 attempts。> 1 同理。
        backoff_sec: 每次 retry 前 sleep；0 = 不 sleep（unit test 用）。

    Returns:
        (final_exec_result, attempts, flake_reason)
        - attempts: ≥1，发生 retry 时 ≥2
        - flake_reason: 仅在确实发生 retry 时非 None
            - "flake-retry-recovered:<tag>" 表示重跑后 pass
            - "flake-retry-exhausted:<tag>" 表示重试用完仍 fail
            - <tag> 取**第一次**命中的 tag（即使后续 attempt 命中别的或没命中）

    边界：
      - 一次 pass / 一次 non-flake fail → 立即返 (result, 1, None)（**不**误吞业务错）
      - max_retries=0 + 一次 flake fail → 立即返 (result, 1, None)（关闭语义）
      - 所有 retry 均 flake fail → (last_result, max_retries+1, "exhausted:<first_tag>")
      - retry 中途 pass → (pass_result, attempt_n, "recovered:<first_tag>")
    """
    # max_retries=0 = single-shot 关闭语义：不分类，不挂 reason，直接返
    # 第一次结果。这样 "关闭 retry" 跟 "没发生 retry" 在 artifact_checks 表里
    # 形状一致（attempts=1, flake_reason=NULL），看板查询不用区分两种 None。
    if max_retries <= 0:
        result = await coro_factory()
        return result, 1, None

    first_tag: str | None = None
    last_result: ExecResult | None = None
    attempts = 0

    total_attempts = max_retries + 1
    for attempt in range(total_attempts):
        attempts = attempt + 1
        last_result = await coro_factory()

        if last_result.exit_code == 0:
            if first_tag is not None:
                # 首次失败被分类为 flake，本次 attempt 救回来
                reason = f"flake-retry-recovered:{first_tag}"
                log.info(
                    "checker.flake.recovered",
                    stage=stage, req_id=req_id, tag=first_tag,
                    attempts=attempts,
                )
                return last_result, attempts, reason
            # 一次过 pass
            return last_result, attempts, None

        # exit_code != 0
        tag = classify_failure(
            last_result.stdout, last_result.stderr, last_result.exit_code,
        )
        if tag is None:
            # 没命中 flake 模式：
            # - 首次（first_tag 仍 None）→ 真业务 fail，不重试，立即返
            # - 后续（first_tag 已设）→ 之前已经为 flake 消耗了 retry quota，
            #   本次仍 fail（虽然换了一个非 flake 错），整体视为 exhausted；
            #   reason 取首次 tag —— stderr_tail 显示最新输出，verifier 优先看
            #   stderr_tail 判，reason 是 informational metadata
            if first_tag is None:
                return last_result, attempts, None
            # fall through to "retry exhausted" path below
            break

        # 命中 flake
        if first_tag is None:
            first_tag = tag
        log.warning(
            "checker.flake.match",
            stage=stage, req_id=req_id, tag=tag,
            attempt=attempts, total_attempts=total_attempts,
            exit_code=last_result.exit_code,
        )

        # 还有 retry quota → sleep + 下一轮
        if attempt < total_attempts - 1:
            log.info(
                "checker.flake.retry",
                stage=stage, req_id=req_id, tag=tag,
                next_attempt=attempts + 1, backoff_sec=backoff_sec,
            )
            if backoff_sec > 0:
                await asyncio.sleep(backoff_sec)
            continue

    # retry 用完，last_result 仍 fail（pass case 上面已 early return）
    assert last_result is not None
    assert first_tag is not None
    reason = f"flake-retry-exhausted:{first_tag}"
    log.warning(
        "checker.flake.exhausted",
        stage=stage, req_id=req_id, tag=first_tag,
        attempts=attempts, exit_code=last_result.exit_code,
    )
    return last_result, attempts, reason
