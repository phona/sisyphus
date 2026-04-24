"""Telegram 推送：critical alert 才推，失败只 log 不阻塞主流程。"""
from __future__ import annotations

import structlog
import httpx

from ..config import settings

log = structlog.get_logger(__name__)


async def send_critical(text: str) -> bool:
    """严重 alert 推 TG。失败只 log warning，不阻塞主流程。"""
    if not settings.tg_bot_token or not settings.tg_chat_id:
        return False
    url = f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage"
    payload = {"chat_id": settings.tg_chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(url, json=payload)
            return r.status_code == 200
    except Exception as e:
        log.warning("tg.send_failed", error=str(e))
        return False
