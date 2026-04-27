"""checkers/_flake.py 单测：classify_failure pattern 命中 + run_with_flake_retry 行为矩阵。

覆盖 spec scenarios CIFR-S1..S9（`openspec/changes/REQ-checker-infra-flake-retry-1777247423`）。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.checkers._flake import (
    INFRA_FLAKE_PATTERNS,
    classify_failure,
    run_with_flake_retry,
)
from orchestrator.k8s_runner import ExecResult

# ── classify_failure: pattern 命中 ───────────────────────────────────────


def test_classify_dns_match_could_not_resolve():
    """CIFR-S1: 'Could not resolve host' → tag='dns'."""
    assert classify_failure(
        stdout_tail="",
        stderr_tail="fatal: Could not resolve host github.com",
        exit_code=128,
    ) == "dns"


def test_classify_dns_match_temporary_failure():
    """另一个 DNS 模式：'Temporary failure in name resolution'."""
    assert classify_failure(
        stdout_tail="",
        stderr_tail="curl: (6) Temporary failure in name resolution",
        exit_code=6,
    ) == "dns"


def test_classify_dns_match_name_or_service_not_known():
    assert classify_failure(
        stdout_tail="",
        stderr_tail="ssh: Could not resolve hostname: Name or service not known",
        exit_code=255,
    ) == "dns"


def test_classify_kubectl_exec_channel_upgrade():
    assert classify_failure(
        stdout_tail="",
        stderr_tail="error: unable to upgrade connection: container not found",
        exit_code=1,
    ) == "kubectl-exec-channel"


def test_classify_kubectl_exec_channel_dialing_backend():
    assert classify_failure(
        stdout_tail="",
        stderr_tail="Error from server: error dialing backend: dial tcp ...",
        exit_code=1,
    ) == "kubectl-exec-channel"


def test_classify_github_rpc_5xx():
    assert classify_failure(
        stdout_tail="",
        stderr_tail="error: RPC failed; HTTP 502 curl 22 The requested URL returned error: 502",
        exit_code=128,
    ) == "github-rpc"


def test_classify_github_fetch_connection_reset():
    assert classify_failure(
        stdout_tail="",
        stderr_tail="fatal: unable to access 'https://github.com/x/y.git/': Connection reset by peer",
        exit_code=128,
    ) == "github-fetch"


def test_classify_github_fetch_remote_hung_up():
    assert classify_failure(
        stdout_tail="",
        stderr_tail="fatal: the remote end hung up unexpectedly",
        exit_code=128,
    ) == "github-fetch"


def test_classify_registry_rate_limit():
    assert classify_failure(
        stdout_tail="",
        stderr_tail="toomanyrequests: You have reached your pull rate limit.",
        exit_code=1,
    ) == "registry-rate-limit"


def test_classify_registry_network_tls_handshake():
    assert classify_failure(
        stdout_tail="",
        stderr_tail=(
            "Error response from daemon: Get https://ghcr.io/v2/: "
            "net/http: TLS handshake timeout"
        ),
        exit_code=1,
    ) == "registry-network"


def test_classify_registry_5xx_http_seeker():
    assert classify_failure(
        stdout_tail="",
        stderr_tail=(
            "failed to copy: httpReadSeeker: failed open: "
            "unexpected status code 502 Bad Gateway"
        ),
        exit_code=1,
    ) == "registry-5xx"


def test_classify_go_mod_dial_tcp_timeout():
    assert classify_failure(
        stdout_tail=(
            "go: github.com/foo/bar@v0.1.0: "
            "Get \"https://proxy.golang.org/github.com/foo/bar/@v/v0.1.0.info\": "
            "dial tcp 142.250.x.y:443: i/o timeout"
        ),
        stderr_tail="",
        exit_code=1,
    ) == "go-mod"


def test_classify_npm_network():
    assert classify_failure(
        stdout_tail="",
        stderr_tail="npm ERR! network ETIMEDOUT https://registry.npmjs.org/foo",
        exit_code=1,
    ) == "npm-network"


def test_classify_apt_mirror_failed_to_fetch():
    assert classify_failure(
        stdout_tail="",
        stderr_tail=(
            "E: Failed to fetch http://archive.ubuntu.com/foo "
            "Connection timed out [IP: 91.x.x.x 80]"
        ),
        exit_code=100,
    ) == "apt-mirror"


def test_classify_pattern_table_has_eight_distinct_tags():
    """spec 要求至少 8 类（DNS / kubectl-exec / github / registry-rate / registry-net /
    go-mod / npm / apt-mirror）。"""
    tags = {tag for _, tag in INFRA_FLAKE_PATTERNS}
    expected = {
        "dns",
        "kubectl-exec-channel",
        "github-rpc",
        "github-fetch",
        "registry-rate-limit",
        "registry-network",
        "registry-5xx",
        "go-mod",
        "npm-network",
        "apt-mirror",
    }
    # 至少包含 expected 的全部 ——（若新增 tag 不应破已有契约）
    assert expected.issubset(tags), f"missing tags: {expected - tags}"


# ── classify_failure: 不命中 ─────────────────────────────────────────────


def test_classify_generic_make_failure_returns_none():
    """CIFR-S2: 真业务 fail → None（不能被误吞当 flake）。"""
    assert classify_failure(
        stdout_tail="--- FAIL: TestFoo (0.10s)\n",
        stderr_tail="make: *** [Makefile:42] Error 1",
        exit_code=2,
    ) is None


def test_classify_unauthorized_returns_none():
    """auth 错（人为 token 配错）不是 flake，不重试。"""
    assert classify_failure(
        stdout_tail="",
        stderr_tail="Error response from daemon: unauthorized: authentication required",
        exit_code=1,
    ) is None


def test_classify_manifest_unknown_returns_none():
    """image tag 不存在不是 flake。"""
    assert classify_failure(
        stdout_tail="",
        stderr_tail="manifest unknown: manifest unknown",
        exit_code=1,
    ) is None


def test_classify_pass_returns_none_even_with_flake_text():
    """CIFR-S3: exit_code=0 即使 stderr 有 flake 模式也返 None（pass 上挂 retry 标签错位）。"""
    assert classify_failure(
        stdout_tail="",
        stderr_tail="warning: Could not resolve host backup.example.com",
        exit_code=0,
    ) is None


def test_classify_empty_inputs_returns_none():
    assert classify_failure(stdout_tail="", stderr_tail="", exit_code=1) is None


def test_classify_stderr_priority_over_stdout():
    """stderr 命中优先 —— 即使 stdout 也含别的 pattern。
    （取首先 stderr 命中的；保证 verifier 看 stderr 跟 reason tag 一致。）"""
    tag = classify_failure(
        stdout_tail="go: foo: dial tcp ...: i/o timeout",  # go-mod
        stderr_tail="fatal: Could not resolve host github.com",  # dns
        exit_code=1,
    )
    assert tag == "dns"


# ── run_with_flake_retry: 行为矩阵 ──────────────────────────────────────


def _factory(*results: ExecResult):
    """造一个 coro_factory，按 results 顺序返一次一次 ExecResult。"""
    seq = list(results)
    calls = {"n": 0}

    async def _f():
        calls["n"] += 1
        if not seq:
            raise RuntimeError("factory exhausted")
        return seq.pop(0)

    return _f, calls


@pytest.mark.asyncio
async def test_retry_single_pass_attempts_1_reason_none():
    """CIFR-S4: 一次过 pass → attempts=1, reason=None."""
    factory, calls = _factory(
        ExecResult(exit_code=0, stdout="ok", stderr="", duration_sec=1.0),
    )
    result, attempts, reason = await run_with_flake_retry(
        coro_factory=factory, stage="test", req_id="REQ-X",
        max_retries=2, backoff_sec=0,
    )
    assert result.exit_code == 0
    assert attempts == 1
    assert reason is None
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retry_non_flake_failure_no_retry(monkeypatch):
    """CIFR-S5: 非 flake fail → 不重试；asyncio.sleep 不被调。"""
    sleep_calls = {"n": 0}

    async def _spy_sleep(s):
        sleep_calls["n"] += 1

    monkeypatch.setattr("orchestrator.checkers._flake.asyncio.sleep", _spy_sleep)
    factory, calls = _factory(
        ExecResult(
            exit_code=2, stdout="--- FAIL: TestFoo",
            stderr="make: *** Error 1", duration_sec=1.0,
        ),
    )
    result, attempts, reason = await run_with_flake_retry(
        coro_factory=factory, stage="test", req_id="REQ-X",
        max_retries=2, backoff_sec=5,
    )
    assert result.exit_code == 2
    assert attempts == 1
    assert reason is None
    assert calls["n"] == 1
    assert sleep_calls["n"] == 0  # 不 sleep（没进 retry loop）


@pytest.mark.asyncio
async def test_retry_flake_then_pass_recovered():
    """CIFR-S6: 一次 flake fail → 二次 pass → reason='flake-retry-recovered:dns'."""
    factory, calls = _factory(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
        ExecResult(exit_code=0, stdout="ok", stderr="", duration_sec=1.0),
    )
    result, attempts, reason = await run_with_flake_retry(
        coro_factory=factory, stage="test", req_id="REQ-X",
        max_retries=1, backoff_sec=0,
    )
    assert result.exit_code == 0
    assert attempts == 2
    assert reason == "flake-retry-recovered:dns"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_retry_flake_twice_exhausted():
    """CIFR-S7: 两次都 flake → reason='flake-retry-exhausted:dns'."""
    factory, calls = _factory(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
    )
    result, attempts, reason = await run_with_flake_retry(
        coro_factory=factory, stage="test", req_id="REQ-X",
        max_retries=1, backoff_sec=0,
    )
    assert result.exit_code == 128
    assert attempts == 2
    assert reason == "flake-retry-exhausted:dns"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_retry_max_zero_disables_retry():
    """CIFR-S8: max_retries=0 → 不重试，flake fail 也直接返 attempts=1 reason=None."""
    factory, calls = _factory(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
    )
    result, attempts, reason = await run_with_flake_retry(
        coro_factory=factory, stage="test", req_id="REQ-X",
        max_retries=0, backoff_sec=0,
    )
    assert result.exit_code == 128
    assert attempts == 1
    assert reason is None
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retry_backoff_zero_skips_sleep(monkeypatch):
    """CIFR-S9: backoff_sec=0 → asyncio.sleep 不被调."""
    sleep_calls = {"n": 0}

    async def _spy_sleep(s):
        sleep_calls["n"] += 1

    monkeypatch.setattr("orchestrator.checkers._flake.asyncio.sleep", _spy_sleep)
    factory, _ = _factory(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
        ExecResult(exit_code=0, stdout="ok", stderr="", duration_sec=1.0),
    )
    await run_with_flake_retry(
        coro_factory=factory, stage="test", req_id="REQ-X",
        max_retries=1, backoff_sec=0,
    )
    assert sleep_calls["n"] == 0


@pytest.mark.asyncio
async def test_retry_backoff_positive_sleeps_between_attempts(monkeypatch):
    """backoff_sec>0 → 第二次 attempt 前 sleep 一次（每次 retry 一 sleep）."""
    sleep_args: list[float] = []

    async def _spy_sleep(s):
        sleep_args.append(s)

    monkeypatch.setattr("orchestrator.checkers._flake.asyncio.sleep", _spy_sleep)
    factory, _ = _factory(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
        ExecResult(exit_code=0, stdout="ok", stderr="", duration_sec=1.0),
    )
    await run_with_flake_retry(
        coro_factory=factory, stage="test", req_id="REQ-X",
        max_retries=1, backoff_sec=15,
    )
    assert sleep_args == [15]


@pytest.mark.asyncio
async def test_retry_first_tag_persists_even_when_second_fails_differently():
    """edge case: 首次 dns flake → retry → 第二次 registry-rate-limit flake → 还 fail。
    reason 取第一次的 tag（设计意图）。"""
    factory, _ = _factory(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
        ExecResult(
            exit_code=1, stdout="",
            stderr="toomanyrequests: pull rate limit", duration_sec=1.0,
        ),
    )
    result, attempts, reason = await run_with_flake_retry(
        coro_factory=factory, stage="test", req_id="REQ-X",
        max_retries=1, backoff_sec=0,
    )
    assert result.exit_code == 1
    assert attempts == 2
    assert reason == "flake-retry-exhausted:dns"  # 首次 tag


@pytest.mark.asyncio
async def test_retry_flake_then_non_flake_returns_exhausted_first_tag():
    """edge case: 首次 flake → retry 后命中真业务 fail → reason 仍 exhausted:<first_tag>。
    设计意图：reason 只反映 *分类*，stderr_tail 是最新输出。"""
    factory, _ = _factory(
        ExecResult(
            exit_code=128, stdout="",
            stderr="fatal: Could not resolve host github.com", duration_sec=1.0,
        ),
        ExecResult(
            exit_code=2, stdout="--- FAIL: TestFoo",
            stderr="make: *** Error 1", duration_sec=1.0,
        ),
    )
    result, attempts, reason = await run_with_flake_retry(
        coro_factory=factory, stage="test", req_id="REQ-X",
        max_retries=1, backoff_sec=0,
    )
    assert result.exit_code == 2
    assert attempts == 2
    assert reason == "flake-retry-exhausted:dns"


# ── propagation: 异常应该上抛（asyncio.TimeoutError 等不被 retry 吞 ──────


@pytest.mark.asyncio
async def test_retry_propagates_timeout_error():
    """TimeoutError（或一般异常）应当上抛，不被 retry helper 吞。"""

    async def _factory():
        raise TimeoutError("inner timeout")

    with pytest.raises(TimeoutError):
        await run_with_flake_retry(
            coro_factory=_factory, stage="test", req_id="REQ-X",
            max_retries=1, backoff_sec=0,
        )


# ── default checks ──────────────────────────────────────────────────────


def test_check_result_attempts_default_is_1():
    """CIFR-S9 (CheckResult): 不传 attempts default=1, reason default=None."""
    from orchestrator.checkers._types import CheckResult
    result = CheckResult(
        passed=True, exit_code=0, stdout_tail="", stderr_tail="",
        duration_sec=1.0, cmd="x",
    )
    assert result.attempts == 1
    assert result.reason is None


def test_run_with_flake_retry_signature_returns_three_tuple():
    """API 契约：run_with_flake_retry 必须返 3-tuple (ExecResult, int, str|None)。"""
    factory, _ = _factory(
        ExecResult(exit_code=0, stdout="ok", stderr="", duration_sec=0.1),
    )
    out = asyncio.run(run_with_flake_retry(
        coro_factory=factory, stage="t", req_id="R",
        max_retries=0, backoff_sec=0,
    ))
    assert isinstance(out, tuple)
    assert len(out) == 3
    assert isinstance(out[0], ExecResult)
    assert isinstance(out[1], int)
    assert out[2] is None or isinstance(out[2], str)
