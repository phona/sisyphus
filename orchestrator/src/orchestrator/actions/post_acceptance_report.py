"""post_acceptance_report (REQ-bkd-acceptance-feedback-loop-1777278984).

teardown_accept_env emits TEARDOWN_DONE_PASS → state CAS'd to
PENDING_USER_REVIEW → 本 action 跑。

设计 = "0 黑话纯原语"：
- 不开新 BKD agent / 不解释自由文本评论
- 只通过 BKD intent issue 既有 primitives 通知用户：
  · PATCH description，写入以 ACCEPTANCE_MARKER 为边界的 managed block
    （用户在 BKD UI 里直接看到验收指引；不改 tags / statusId，用户自驱）
- 不动 statusId（用户驱动 statusId 表态：done=approve / review|blocked=fix）

幂等：第二次 invocation 时，managed block 已在 description 里 —— 替换不追加，
确保 description 里 ACCEPTANCE_MARKER 只出现一次（USER-S10）。
"""
from __future__ import annotations

from datetime import UTC, datetime

import structlog

from ..bkd import BKDClient
from ..config import settings
from ..store import db, req_state
from . import register

log = structlog.get_logger(__name__)

# managed block 边界标记；sisyphus 用此检测"是否已贴过"并做幂等替换。
ACCEPTANCE_MARKER = "<!-- sisyphus:acceptance-status -->"


def _render_acceptance_block(*, req_id: str, pr_urls: dict[str, str] | None) -> str:
    """构造以 ACCEPTANCE_MARKER 开头的 managed block。"""
    lines = [
        ACCEPTANCE_MARKER,
        f"## sisyphus 验收已通过（REQ {req_id}）— 等你拍板",
        "",
        "**PR**",
    ]
    if pr_urls:
        for repo, url in sorted(pr_urls.items()):
            lines.append(f"- {repo}: {url}")
    else:
        lines.append("- (no PR URLs recorded in ctx; check feat/<REQ> on involved repos)")
    lines.extend([
        "",
        "**拍板方式**：改本 BKD issue 的 **statusId** 即可（不解释自由文本）：",
        "",
        "- `done` → approve，sisyphus 立即合 PR 归档",
        "- `review` 或 `blocked` → 不满意，sisyphus 进 escalated（reason=user-requested-fix）",
        "  之后你可以在 chat 里 follow-up 描述具体要改啥",
        "- 其他 statusId（`working` / `todo` 等）→ sisyphus 继续等",
    ])
    return "\n".join(lines) + "\n"


def _inject_block(existing: str | None, block: str) -> str:
    """把 managed block 注入 description，幂等（第二次替换而非追加）。"""
    body = existing or ""
    if ACCEPTANCE_MARKER in body:
        # 截断到 marker 位置，用新 block 替换 marker 以后的全部内容
        before = body[: body.index(ACCEPTANCE_MARKER)]
        return before + block
    sep = "\n" if body and not body.endswith("\n") else ""
    return body + sep + block


@register("post_acceptance_report", idempotent=True)
async def post_acceptance_report(*, body, req_id, tags, ctx):
    """accept teardown 通过后：把验收报告注入 BKD intent issue 的 description。

    spec contract (USER-S9/S10/S11):
      - MUST call update_issue with description containing ACCEPTANCE_MARKER
      - description MUST include PR URL(s)
      - MUST NOT include tags or statusId in the PATCH
      - ctx.acceptance_reported_at MUST be set
      - missing intent_issue_id → graceful noop
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
    block = _render_acceptance_block(req_id=req_id, pr_urls=pr_urls)

    bkd_ok = False
    try:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            issue = await bkd.get_issue(proj, intent_issue_id)
            new_desc = _inject_block(issue.description, block)
            await bkd.update_issue(proj, intent_issue_id, description=new_desc)
        bkd_ok = True
    except Exception as e:
        # 失败不阻塞状态机：state 已经 CAS'd 到 PENDING_USER_REVIEW，用户可以
        # 直接看 BKD UI 状态判断（dashboard / 状态机层）。本 action 失败只 log。
        log.warning(
            "post_acceptance_report.bkd_call_failed",
            req_id=req_id, intent_issue_id=intent_issue_id, error=str(e),
        )

    reported_at = datetime.now(UTC).isoformat()
    ctx["acceptance_reported_at"] = reported_at
    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "acceptance_reported_at": reported_at,
        "acceptance_reported_ok": bkd_ok,
    })

    log.info(
        "post_acceptance_report.done",
        req_id=req_id, intent_issue_id=intent_issue_id, bkd_ok=bkd_ok,
        pr_urls_count=len(pr_urls),
    )
    return {"acceptance_reported": True, "bkd_ok": bkd_ok}
