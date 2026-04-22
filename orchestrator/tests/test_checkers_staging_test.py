"""checkers/staging_test.py 单测：mock manifest_io + RunnerController，验 CheckResult 字段。

M11：run_staging_test 不再收 test_cmd 参数，自己从 manifest.yaml 读 test.cmd / cwd / timeout。
"""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.checkers import manifest_io
from orchestrator.checkers._types import CheckResult
from orchestrator.checkers.staging_test import run_staging_test
from orchestrator.k8s_runner import ExecResult


def make_fake_controller(exit_code: int, stdout: str = "", stderr: str = "", duration: float = 1.0):
    class FakeRC:
        async def exec_in_runner(self, req_id, command, **kw):
            # 记下真正跑的命令，单测拿这个 assert
            FakeRC.last_cmd = command
            return ExecResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration)
    FakeRC.last_cmd = ""
    return FakeRC


def patch_manifest(monkeypatch, manifest: dict):
    async def fake_read(req_id, timeout_sec=30):
        return manifest
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.manifest_io.read_manifest",
        fake_read,
    )


# ── pass：验 final_cmd 正确拼出 cd + cmd ──────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_pass(monkeypatch):
    patch_manifest(monkeypatch, {
        "test": {"cmd": "make ci-unit-test", "cwd": "source/foo", "timeout_sec": 1200},
    })
    FakeRC = make_fake_controller(exit_code=0, stdout="ok\n", stderr="", duration=3.5)
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_staging_test("REQ-1")

    assert isinstance(result, CheckResult)
    assert result.passed is True
    assert result.exit_code == 0
    assert result.stdout_tail == "ok\n"
    assert result.stderr_tail == ""
    assert result.duration_sec == 3.5
    assert result.cmd == "cd /workspace/source/foo && make ci-unit-test"
    assert FakeRC.last_cmd == "cd /workspace/source/foo && make ci-unit-test"


# ── fail ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_fail(monkeypatch):
    patch_manifest(monkeypatch, {
        "test": {"cmd": "make ci-unit-test", "cwd": "source/foo"},
    })
    FakeRC = make_fake_controller(exit_code=1, stdout="FAIL\n", stderr="panic: nil ptr\n", duration=2.0)
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_staging_test("REQ-2")

    assert result.passed is False
    assert result.exit_code == 1
    assert result.stdout_tail == "FAIL\n"
    assert result.stderr_tail == "panic: nil ptr\n"


# ── stdout/stderr tail 截尾 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_truncates_tails(monkeypatch):
    patch_manifest(monkeypatch, {
        "test": {"cmd": "make ci-unit-test", "cwd": "source/foo"},
    })
    big_out = "x" * 5000
    big_err = "e" * 4000
    FakeRC = make_fake_controller(exit_code=0, stdout=big_out, stderr=big_err)
    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: FakeRC(),
    )
    result = await run_staging_test("REQ-3")

    assert len(result.stdout_tail) == 2048
    assert len(result.stderr_tail) == 2048
    assert result.stdout_tail == big_out[-2048:]
    assert result.stderr_tail == big_err[-2048:]


# ── timeout ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_timeout(monkeypatch):
    patch_manifest(monkeypatch, {
        "test": {"cmd": "make ci-unit-test", "cwd": "source/foo", "timeout_sec": 30},
    })

    class SlowRC:
        async def exec_in_runner(self, req_id, command, **kw):
            await asyncio.sleep(9999)
            return ExecResult(exit_code=0, stdout="", stderr="", duration_sec=0)

    monkeypatch.setattr(
        "orchestrator.checkers.staging_test.k8s_runner.get_controller",
        lambda: SlowRC(),
    )

    async def fast_wait_for(coro, timeout):
        task = asyncio.ensure_future(coro)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            raise TimeoutError() from None

    monkeypatch.setattr("orchestrator.checkers.staging_test.asyncio.wait_for", fast_wait_for)

    with pytest.raises(TimeoutError):
        await run_staging_test("REQ-4")


# ── manifest 缺 test 段 → 抛 ManifestReadError ──────────────────────────

@pytest.mark.asyncio
async def test_run_staging_test_raises_when_manifest_missing_test(monkeypatch):
    patch_manifest(monkeypatch, {"schema_version": 1})
    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await run_staging_test("REQ-5")
    assert "test" in str(exc.value)


@pytest.mark.asyncio
async def test_run_staging_test_raises_when_test_missing_cmd(monkeypatch):
    patch_manifest(monkeypatch, {"test": {"cwd": "source/foo"}})
    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await run_staging_test("REQ-6")
    assert "cmd" in str(exc.value)


@pytest.mark.asyncio
async def test_run_staging_test_raises_when_test_missing_cwd(monkeypatch):
    patch_manifest(monkeypatch, {"test": {"cmd": "make ci-unit-test"}})
    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await run_staging_test("REQ-7")
    assert "cwd" in str(exc.value)
