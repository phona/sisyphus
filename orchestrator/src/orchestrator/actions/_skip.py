"""通用 skip 路径：检查 settings.skip_<stage> 或 test_mode，匹配则直接 emit。

action 函数头部调一下：
    if rv := skip_if_enabled(stage="dev", emit=Event.DEV_DONE):
        return rv
后续逻辑跑真实 BKD 调用。
"""
from __future__ import annotations

import structlog

from ..config import settings
from ..state import Event

log = structlog.get_logger(__name__)


def _flag(stage: str) -> bool:
    """test_mode = 全部 skip；否则查具体 skip_<stage>"""
    if settings.test_mode:
        return True
    return getattr(settings, f"skip_{stage.replace('-', '_')}", False)


def skip_if_enabled(stage: str, emit: Event, *, req_id: str = "") -> dict | None:
    """如果该 stage 设了 skip，返一个 emit 字典让 engine 接力推状态机；否则返 None。

    Returns:
        {"skipped": True, "stage": stage, "emit": event_value} 或 None
    """
    if _flag(stage):
        log.warning("action.skipped", req_id=req_id, stage=stage, emit=emit.value,
                    test_mode=settings.test_mode)
        return {"skipped": True, "stage": stage, "emit": emit.value}
    return None
