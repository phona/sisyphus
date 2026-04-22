"""checkers/manifest_io.py 单测：mock RunnerController.exec_in_runner，验返 dict / 失败抛。"""
from __future__ import annotations

import textwrap

import pytest

from orchestrator.checkers import manifest_io
from orchestrator.k8s_runner import ExecResult


def _fake_controller(exit_code: int, stdout: str = "", stderr: str = "", duration: float = 0.1):
    class FakeRC:
        async def exec_in_runner(self, req_id, command, **kw):
            return ExecResult(
                exit_code=exit_code, stdout=stdout, stderr=stderr, duration_sec=duration,
            )
    return FakeRC()


# ── happy path ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_manifest_returns_dict(monkeypatch):
    yaml_body = textwrap.dedent(
        """\
        schema_version: 1
        req_id: REQ-9
        test:
          cmd: "make ci-unit-test"
          cwd: "source/foo"
          timeout_sec: 1200
        pr:
          repo: "phona/foo"
          number: 42
        """
    )
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_io.k8s_runner.get_controller",
        lambda: _fake_controller(0, yaml_body),
    )
    data = await manifest_io.read_manifest("REQ-9")
    assert isinstance(data, dict)
    assert data["test"]["cmd"] == "make ci-unit-test"
    assert data["test"]["cwd"] == "source/foo"
    assert data["pr"]["repo"] == "phona/foo"
    assert data["pr"]["number"] == 42


# ── exec fail ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_manifest_raises_on_cat_nonzero(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_io.k8s_runner.get_controller",
        lambda: _fake_controller(2, stdout="", stderr="cat: No such file"),
    )
    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await manifest_io.read_manifest("REQ-9")
    assert "exit 2" in str(exc.value)
    assert "No such file" in str(exc.value)


# ── yaml bad ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_manifest_raises_on_bad_yaml(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_io.k8s_runner.get_controller",
        lambda: _fake_controller(0, stdout="::: not valid yaml :::\n  - ["),
    )
    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await manifest_io.read_manifest("REQ-9")
    assert "yaml parse failed" in str(exc.value)


# ── yaml root 不是 dict ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_manifest_raises_on_non_dict_root(monkeypatch):
    monkeypatch.setattr(
        "orchestrator.checkers.manifest_io.k8s_runner.get_controller",
        lambda: _fake_controller(0, stdout="- item1\n- item2\n"),  # YAML list not dict
    )
    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await manifest_io.read_manifest("REQ-9")
    assert "must be object" in str(exc.value)


# ── exec 超时 ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_manifest_raises_on_timeout(monkeypatch):
    import asyncio

    class SlowRC:
        async def exec_in_runner(self, req_id, command, **kw):
            await asyncio.sleep(9999)
            return ExecResult(0, "", "", 0)

    monkeypatch.setattr(
        "orchestrator.checkers.manifest_io.k8s_runner.get_controller",
        lambda: SlowRC(),
    )

    async def fast_wait_for(coro, timeout):
        task = asyncio.ensure_future(coro)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            raise TimeoutError() from None

    monkeypatch.setattr("orchestrator.checkers.manifest_io.asyncio.wait_for", fast_wait_for)

    with pytest.raises(manifest_io.ManifestReadError) as exc:
        await manifest_io.read_manifest("REQ-9", timeout_sec=1)
    assert "timeout" in str(exc.value).lower()
