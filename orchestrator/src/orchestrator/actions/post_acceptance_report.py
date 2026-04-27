"""post_acceptance_report (REQ-bkd-acceptance-feedback-loop-1777278984).

teardown_accept_env emits TEARDOWN_DONE_PASS → state CAS'd to
PENDING_USER_REVIEW → 本 action 跑。

设计 = "0 黑话纯原语"：
- 不开新 BKD agent / 不解释自由文本评论
- 只通过 BKD intent issue 既有 primitives 通知用户：
  · 加一个 ``acceptance-pending`` tag（让 BKD 看板能筛"等用户验收"列）
  · follow_up_issue 把验收报告作为 chat 消息贴上去（用户看得见）
- 不动 statusId（用户驱动 statusId 表态：done=approve / review|blocked=fix）

幂等：merge_tags_and_update 重复 add 同一个 tag 是 no-op；follow_up 重复贴消息
会重复，但 watchdog 不会 retry 这个 action（idempotent=True 是元数据，重跑由
engine 自身的 CAS 抗）。
"""
from __future__ import annotations

from datetime import UTC, datetime

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)

# 加在 BKD intent issue 上的"等用户验收"标识 tag。
ACCEPTANCE_PENDING_TAG = "acceptance-pending"


def _render_acceptance_message(*, req_id: str, pr_urls: dict[str, str] | None) -> str:
    """给 BKD intent issue 贴的 acceptance 通知消息。

    内容上写死说明用户应该改 statusId 来表态 —— 把"如何反馈"做成纯原语。
    """
    lines = [
        f"🤖 sisyphus 验收已通过（REQ {req_id}）— 等你拍板",
        "",
        "## PR",
    ]
    if pr_urls:
        for repo, url in sorted(pr_urls.items()):
            lines.append(f"- {repo}: {url}")
    else:
        lines.append("- (no PR URLs recorded in ctx; check feat/<REQ> on involved repos)")
    lines.extend([
        "",
        "## 拍板方式",
        "",
        "改本 BKD issue 的 **statusId** 即可（不解释自由文本）：",
        "",
        "- `done` → approve，sisyphus 立即合 PR 归档",
        "- `review` 或 `blocked` → 不满意，sisyphus 进 escalated（reason=user-requested-fix）",
        "  之后你可以在 chat 里 follow-up 描述具体要改啥",
        "- 其他 statusId（`working` / `todo` 等）→ sisyphus 继续等",
        "",
        f"sisyphus tag `{ACCEPTANCE_PENDING_TAG}` 已贴在本 issue 上。",
    ])
    return "\n".join(lines)


@register("post_acceptance_report", idempotent=True)
async def post_acceptance_report(*, body, req_id, tags, ctx):
    """accept teardown 通过后：把验收报告贴到 BKD intent issue。

    idempotent: 重跑只会再 add 同一个 tag (no-op) + 重复 follow_up 一次。
    第一次以后基本不会再触发 —— 进 PENDING_USER_REVIEW 后只剩用户改 statusId
    才能再 transition 出去。
    """
    proj = body.projectId
    ctx = ctx or {}
    intent_issue_id = ctx.get("intent_issue_id")
    if not intent_issue_id:
        log.warning(
            "post_acceptance_report.no_intent_issue_id",
            req_id=req_id,
            reason="ctx missing intent_issue_id; cannot notify user",
        )
        return {"acceptance_reported": False, "reason": "no intent_issue_id"}

    pr_urls = ctx.get("pr_urls") or {}
    msg = _render_acceptance_message(req_id=req_id, pr_urls=pr_urls)

    bkd_ok = False
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            # 1. 加 acceptance-pending tag（idempotent merge）
            await bkd.merge_tags_and_update(
                proj, intent_issue_id, add=[ACCEPTANCE_PENDING_TAG],
            )
            # 2. 贴 acceptance 通知消息
            await bkd.follow_up_issue(
                project_id=proj, issue_id=intent_issue_id, prompt=msg,
            )
        bkd_ok = True
    except Exception as e:
        # 失败不阻塞状态机：state 已经 CAS'd 到 PENDING_USER_REVIEW，用户可以
        # 直接看 BKD UI 状态判断（dashboard / 状态机层）。本 action 失败只 log。
        log.warning(
            "post_acceptance_report.bkd_call_failed",
            req_id=req_id, intent_issue_id=intent_issue_id, error=str(e),
        )

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "acceptance_reported_at": datetime.now(UTC).isoformat(),
        "acceptance_reported_ok": bkd_ok,
    })

    log.info(
        "post_acceptance_report.done",
        req_id=req_id, intent_issue_id=intent_issue_id, bkd_ok=bkd_ok,
        pr_urls_count=len(pr_urls),
    )
    return {"acceptance_reported": True, "bkd_ok": bkd_ok}
