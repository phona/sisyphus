"""start_challenger (M18)：spec lint pass → 起 challenger-agent 写 contract test。

理念：
- challenger 是黑盒挑战者：**只**读 openspec/changes/<REQ>/specs/*/contract.spec.yaml + spec.md，
  **不**看 cmd/ internal/ handlers/ 等业务代码（white-box 是 dev 的活）。
- 输出：tests/contract/*_contract_test.go（或对应语言的 contract test）—— 黑盒断言契约。
- challenger 写完 push 同一 feat/<REQ> 分支，set tag result:pass。
- 后续 staging_test 跑 dev 的 unit + challenger 的 contract，任一红 → fixer 修
  （fix 域路径白名单：dev_fixer 不能改 tests/contract/，spec_fixer 不能改 cmd/）。

跟 dev 的对抗约束：
- challenger 不见 dev 代码（path scope 排除 cmd/internal/handlers/migrations/...）
- dev 不能改 challenger 写的 tests/contract/ —— 不能"偷偷改测试让它绿"
- 真冲突时 verifier 4-路判：code bug / contract test bug / spec 模糊 / spec 漏写

行为：
1. update-issue 自己 sub-issue（spec lint 上游）—— 不复用 intent issue（让 challenger 干净）
   实际：sisyphus 创新 BKD issue，prompt 里说明 REQ context
2. follow-up-issue 发 challenger prompt
3. update-issue statusId=working 触发
"""
from __future__ import annotations

import structlog

from .. import pr_links
from ..bkd import BKDClient
from ..config import settings
from ..prompts import render
from ..state import Event
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


@register("start_challenger", idempotent=False)
async def start_challenger(*, body, req_id, tags, ctx):
    if rv := skip_if_enabled("challenger", Event.CHALLENGER_PASS, req_id=req_id):
        return rv

    proj = body.projectId
    source_issue_id = body.issueId   # spec_lint 没 BKD agent issue，用上游 intent issue

    # PR-link tag 注入（REQ-issue-link-pr-quality-base-1777218242）
    branch_for_links = (ctx or {}).get("branch") or f"feat/{req_id}"
    links = await pr_links.ensure_pr_links_in_ctx(
        req_id=req_id, branch=branch_for_links, ctx=ctx, project_id=proj,
    )
    extra_tags = pr_links.pr_link_tags(links)

    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        issue = await bkd.create_issue(
            project_id=proj,
            title=f"[{req_id}] [CHALLENGER]{short_title(ctx)}",
            tags=["challenger", req_id, f"parent-id:{source_issue_id}", *extra_tags],
            status_id="todo",
            use_worktree=True,   # 黑盒，不污染主 worktree
            model=settings.agent_model,
        )
        prompt = render(
            "challenger.md.j2",
            req_id=req_id,
            aissh_server_id=settings.aissh_server_id,
            project_id=proj,
            project_alias=proj,
            issue_id=issue.id,
        )
        await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
        await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

    log.info("start_challenger.done", req_id=req_id, issue_id=issue.id)
    return {"challenger_issue_id": issue.id, "req_id": req_id}
