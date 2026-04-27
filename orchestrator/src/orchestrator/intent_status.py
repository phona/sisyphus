"""BKD intent issue statusId 同步：REQ 进终态时把 BKD kanban 卡推到对应列。

设计点（详见 openspec/changes/REQ-bkd-intent-statusid-sync-1777280751/proposal.md）：

- DONE → "done"，ESCALATED → "review"。BKD 没原生 "escalated" status；"review"
  是看板"待审查"列，跟 verifier-escalate 对齐（webhook._push_upstream_status 也
  这么处理 verifier issue 自身），语义就是"等人介入"。
- best-effort：BKD 不可达只 log warning，不抛。状态机 / runner cleanup 跟 BKD 解耦。
- 不读 / 不动 tags：那是 escalate.merge_tags_and_update / done_archive 的活，本模块
  只 PATCH statusId。
- 调用方：engine.step 终态分支（DONE / ESCALATED via 正常 transition）+
  actions.escalate SESSION_FAILED self-loop inner CAS to ESCALATED（engine 看是
  self-loop，hook 不 fire，escalate 自己负责）。
"""
from __future__ import annotations

import structlog

from .bkd import BKDClient
from .config import settings
from .state import ReqState

log = structlog.get_logger(__name__)


# REQ 终态 → BKD kanban statusId。其他 state 没对应 status，调用方拿到 None 应跳过。
STATE_TO_STATUS_ID: dict[ReqState, str] = {
    ReqState.DONE:      "done",
    ReqState.ESCALATED: "review",
}


def status_id_for(state: ReqState) -> str | None:
    """返回 ReqState 对应的 BKD statusId；非终态返回 None。"""
    return STATE_TO_STATUS_ID.get(state)


async def patch_terminal_status(
    *,
    project_id: str,
    intent_issue_id: str | None,
    terminal_state: ReqState,
    source: str,
) -> bool:
    """REQ 进终态时 PATCH BKD intent issue 的 statusId。

    返回值仅供测试断言：
    - True：PATCH 已发出（不论 BKD 实际响应是 200 还是 5xx 抛异常被吞掉）
    - False：跳过（intent_issue_id 为空 / 非终态 state）

    生产代码不需要看返回值——失败已 log warning，不抛任何异常。
    """
    if not intent_issue_id:
        log.debug(
            "intent_status.skip_no_intent_id",
            project_id=project_id,
            state=terminal_state.value,
            source=source,
        )
        return False
    target_status = status_id_for(terminal_state)
    if target_status is None:
        log.debug(
            "intent_status.skip_non_terminal",
            state=terminal_state.value,
            source=source,
        )
        return False
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            await bkd.update_issue(
                project_id=project_id,
                issue_id=intent_issue_id,
                status_id=target_status,
            )
    except Exception as e:
        log.warning(
            "intent_status.patch_failed",
            project_id=project_id,
            intent_issue_id=intent_issue_id,
            status_id=target_status,
            source=source,
            error=str(e),
        )
        return True
    log.info(
        "intent_status.patched",
        project_id=project_id,
        intent_issue_id=intent_issue_id,
        status_id=target_status,
        source=source,
    )
    return True
