"""健康端点单测：/livez / /readyz / /healthz（向后兼容）。

直接调 handler 函数，不走 TestClient lifespan（避免连 DB/startup 副作用）。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── /livez ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_livez_always_ok():
    from orchestrator.main import livez
    result = await livez()
    assert result == {"status": "ok"}


# ── /healthz (deprecated alias) ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_healthz_ok():
    from orchestrator.main import healthz
    result = await healthz()
    assert result["status"] == "ok"


# ── readyz helpers ────────────────────────────────────────────────────────────

def _fake_pool(ok: bool = True):
    conn = AsyncMock()
    if ok:
        conn.fetchval = AsyncMock(return_value=1)
    else:
        conn.fetchval = AsyncMock(side_effect=Exception("db down"))
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return pool


def _fake_httpx_client(status_code: int = 200, error: Exception | None = None):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    hc = AsyncMock()
    if error:
        hc.get = AsyncMock(side_effect=error)
    else:
        hc.get = AsyncMock(return_value=mock_resp)
    hc.__aenter__ = AsyncMock(return_value=hc)
    hc.__aexit__ = AsyncMock(return_value=False)
    return hc


# ── /readyz — all pass ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_readyz_all_ok(monkeypatch):
    import orchestrator.k8s_runner as k8s_mod
    import orchestrator.store.db as db_mod
    from orchestrator.main import readyz

    monkeypatch.setattr(db_mod, "get_pool", lambda: _fake_pool(ok=True))
    # K8s controller not initialized → RuntimeError → skip (not a failure)
    monkeypatch.setattr(k8s_mod, "get_controller", lambda: (_ for _ in ()).throw(RuntimeError("not init")))

    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(200)):
        result = await readyz()

    assert result == {"status": "ok"}


# ── /readyz — DB 挂了 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_readyz_db_fail(monkeypatch):
    from fastapi.responses import JSONResponse

    import orchestrator.k8s_runner as k8s_mod
    import orchestrator.store.db as db_mod
    from orchestrator.main import readyz

    monkeypatch.setattr(db_mod, "get_pool", lambda: _fake_pool(ok=False))
    monkeypatch.setattr(k8s_mod, "get_controller", lambda: (_ for _ in ()).throw(RuntimeError("not init")))

    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(200)):
        result = await readyz()

    assert isinstance(result, JSONResponse)
    assert result.status_code == 503
    import json
    body = json.loads(result.body)
    assert body["status"] == "not_ready"
    assert "db" in body["failed"]


# ── /readyz — BKD 连接失败 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_readyz_bkd_connect_fail(monkeypatch):
    from fastapi.responses import JSONResponse

    import orchestrator.k8s_runner as k8s_mod
    import orchestrator.store.db as db_mod
    from orchestrator.main import readyz

    monkeypatch.setattr(db_mod, "get_pool", lambda: _fake_pool(ok=True))
    monkeypatch.setattr(k8s_mod, "get_controller", lambda: (_ for _ in ()).throw(RuntimeError("not init")))

    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(error=Exception("connection refused"))):
        result = await readyz()

    assert isinstance(result, JSONResponse)
    import json
    body = json.loads(result.body)
    assert "bkd" in body["failed"]


# ── /readyz — BKD 返回 5xx ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_readyz_bkd_5xx(monkeypatch):
    from fastapi.responses import JSONResponse

    import orchestrator.k8s_runner as k8s_mod
    import orchestrator.store.db as db_mod
    from orchestrator.main import readyz

    monkeypatch.setattr(db_mod, "get_pool", lambda: _fake_pool(ok=True))
    monkeypatch.setattr(k8s_mod, "get_controller", lambda: (_ for _ in ()).throw(RuntimeError("not init")))

    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(503)):
        result = await readyz()

    assert isinstance(result, JSONResponse)
    import json
    body = json.loads(result.body)
    assert "bkd" in body["failed"]


# ── /readyz — K8s API 失败 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_readyz_k8s_fail(monkeypatch):
    from fastapi.responses import JSONResponse

    import orchestrator.k8s_runner as k8s_mod
    import orchestrator.store.db as db_mod
    from orchestrator.main import readyz

    monkeypatch.setattr(db_mod, "get_pool", lambda: _fake_pool(ok=True))

    mock_controller = MagicMock()
    mock_controller.namespace = "sisyphus-runners"
    mock_controller.core_v1.list_namespaced_pod = MagicMock(side_effect=Exception("k8s api error"))
    monkeypatch.setattr(k8s_mod, "get_controller", lambda: mock_controller)

    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(200)):
        result = await readyz()

    assert isinstance(result, JSONResponse)
    import json
    body = json.loads(result.body)
    assert "k8s" in body["failed"]
    assert "db" not in body["failed"]
    assert "bkd" not in body["failed"]


# ── /readyz — K8s 用 namespaced pod list（issue #344） ───────────────────────

@pytest.mark.asyncio
async def test_readyz_k8s_uses_namespaced_pod_list(monkeypatch):
    """探活必须走 list_namespaced_pod，而不是要 cluster-wide RBAC 的 list_namespace。"""
    import orchestrator.k8s_runner as k8s_mod
    import orchestrator.store.db as db_mod
    from orchestrator.main import readyz

    monkeypatch.setattr(db_mod, "get_pool", lambda: _fake_pool(ok=True))

    mock_controller = MagicMock()
    mock_controller.namespace = "sisyphus-runners"
    mock_controller.core_v1.list_namespaced_pod = MagicMock(return_value=MagicMock(items=[]))
    monkeypatch.setattr(k8s_mod, "get_controller", lambda: mock_controller)

    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(200)):
        result = await readyz()

    assert result == {"status": "ok"}
    mock_controller.core_v1.list_namespaced_pod.assert_called_once_with(
        "sisyphus-runners", limit=1, _request_timeout=2,
    )
    assert not mock_controller.core_v1.list_namespace.called


# ── /readyz — K8s 未初始化不算失败 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_readyz_k8s_not_initialized_ok(monkeypatch):
    import orchestrator.k8s_runner as k8s_mod
    import orchestrator.store.db as db_mod
    from orchestrator.main import readyz

    monkeypatch.setattr(db_mod, "get_pool", lambda: _fake_pool(ok=True))
    monkeypatch.setattr(k8s_mod, "get_controller", lambda: (_ for _ in ()).throw(RuntimeError("not init")))

    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(200)):
        result = await readyz()

    # RuntimeError from get_controller is skipped — not a readiness failure
    assert result == {"status": "ok"}


# ── /readyz — 多个依赖同时挂 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_readyz_multiple_fail(monkeypatch):
    from fastapi.responses import JSONResponse

    import orchestrator.k8s_runner as k8s_mod
    import orchestrator.store.db as db_mod
    from orchestrator.main import readyz

    monkeypatch.setattr(db_mod, "get_pool", lambda: _fake_pool(ok=False))

    mock_controller = MagicMock()
    mock_controller.namespace = "sisyphus-runners"
    mock_controller.core_v1.list_namespaced_pod = MagicMock(side_effect=Exception("k8s error"))
    monkeypatch.setattr(k8s_mod, "get_controller", lambda: mock_controller)

    with patch("httpx.AsyncClient", return_value=_fake_httpx_client(error=Exception("bkd down"))):
        result = await readyz()

    assert isinstance(result, JSONResponse)
    import json
    body = json.loads(result.body)
    assert set(body["failed"]) == {"db", "bkd", "k8s"}
