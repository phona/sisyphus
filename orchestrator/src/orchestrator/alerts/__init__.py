"""alerts 包：DB 持久化 + Telegram 推送。

用法：
    from orchestrator import alerts
    from orchestrator.alerts import tg

    alert_id = await alerts.insert(severity="critical", reason="...", ...)
    await tg.send_critical("🚨 *sisyphus alert*\n...")
"""
from __future__ import annotations

from ..store import alerts as _store, db


async def insert(
    *,
    severity: str,
    reason: str,
    hint: str | None = None,
    suggested_action: str | None = None,
    req_id: str | None = None,
    stage: str | None = None,
) -> int:
    """写一条 alert，返回 id。"""
    pool = db.get_pool()
    return await _store.insert_alert(
        pool,
        severity=severity,
        reason=reason,
        hint=hint,
        suggested_action=suggested_action,
        req_id=req_id,
        stage=stage,
    )
