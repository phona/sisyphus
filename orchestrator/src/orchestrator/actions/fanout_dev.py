"""fanout_dev: SPG 通过后按 manifest.parallelism.dev fanout 多个 dev agent。

M14d：replaces old single-shot create_dev。

行为：
1. 读 /workspace/.sisyphus/manifest.yaml 的 parallelism.dev（analyze agent 可选写入）
2. 无配置 → 单 dev agent（兼容老行为）
3. 有配置 → 拓扑排序，第一波启动 depends_on 为空的任务（每个独立 BKD issue / worktree）

每波并行：同一 wave 里的任务 depends_on 全满足，一次性全 fanout。后续波由
mark_dev_reviewed_and_check 发现前置波都 ci-passed 后再触发（fanout_dev_wave）。
**本期先只做第一波 + 聚合；多波递归等后续 PR**。

设计要点：
- 每个 dev agent 用独立 BKD issue（useWorktree=True 由 BKD 默认给，避免 worktree 抢 checkout）
- issue tag 带 `dev` + `REQ-xxx` + `dev-task:<id>`，供 router / mark_dev_reviewed 聚合
- ctx.dev_tasks = 全量任务列表（gate 用来数 expected_count）
- ctx.dev_issue_ids = {task_id: issue_id}；单任务模式 dev_issue_id 仍兼容
- prompt 注入 DEV_TASK_ID / DEV_TASK_SCOPE，dev agent + pre-commit-acl 各自读
"""
from __future__ import annotations

import structlog

from ..bkd import BKDClient
from ..checkers import manifest_io
from ..config import settings
from ..prompts import render
from ..state import Event
from ..store import db, req_state
from . import register, short_title
from ._skip import skip_if_enabled

log = structlog.get_logger(__name__)


def _topo_first_wave(tasks: list[dict]) -> list[dict]:
    """返 depends_on 为空（或已完成）的第一波任务。

    **本期不做多波**：保证非空，调用方用这波 fanout。后续 PR 再递归 wave。
    """
    # 第一波 = depends_on 为空
    first = [t for t in tasks if not t.get("depends_on")]
    if not first:
        raise ValueError(
            "parallelism.dev 无可启动任务（所有任务都有 depends_on，形成环）"
        )
    return first


def _validate_tasks(tasks: list[dict]) -> None:
    """基础校验：id 唯一、depends_on 指向存在的 id、无环。

    schema 已验 shape；这里补跨字段语义（schema 表达不了）。
    """
    ids = [t["id"] for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"parallelism.dev id 重复：{ids}")
    id_set = set(ids)
    for t in tasks:
        for dep in t.get("depends_on", []):
            if dep not in id_set:
                raise ValueError(
                    f"dev task {t['id']!r} depends_on={dep!r} 未定义"
                )
    # 简易环检测：Kahn
    in_deg = {i: 0 for i in ids}
    for t in tasks:
        for _dep in t.get("depends_on", []):
            in_deg[t["id"]] += 1
    queue = [i for i, d in in_deg.items() if d == 0]
    visited = 0
    by_id = {t["id"]: t for t in tasks}
    while queue:
        cur = queue.pop(0)
        visited += 1
        for t in tasks:
            if cur in t.get("depends_on", []):
                in_deg[t["id"]] -= 1
                if in_deg[t["id"]] == 0:
                    queue.append(t["id"])
    if visited != len(tasks):
        raise ValueError("parallelism.dev 存在依赖环")
    _ = by_id  # 留给将来 wave 递归查引用


async def _read_parallelism(req_id: str) -> list[dict] | None:
    """读 manifest.parallelism.dev；缺 manifest / parallelism 段都返 None 走兼容路径。

    只吞已知的 ManifestReadError，其它异常冒泡（fanout_dev 非幂等 → engine 会 escalate）。
    """
    try:
        manifest = await manifest_io.read_manifest(req_id)
    except manifest_io.ManifestReadError as e:
        # M14d 向后兼容：老 REQ / test_mode 可能没 manifest，按单 dev 走
        log.info("fanout_dev.no_manifest_fallback", req_id=req_id, reason=str(e))
        return None

    parallelism = manifest.get("parallelism") or {}
    tasks = parallelism.get("dev")
    if not tasks:
        return None
    if not isinstance(tasks, list):
        raise ValueError(f"manifest.parallelism.dev 必须是 list，实际 {type(tasks).__name__}")
    return tasks


@register("fanout_dev", idempotent=False)   # 创建 N 个 BKD dev issue
async def fanout_dev(*, body, req_id, tags, ctx):
    """入口：读 parallelism → 建第一波 dev issues。

    无 parallelism.dev 配置 → fallback 单 dev agent。
    """
    if rv := skip_if_enabled("dev", Event.DEV_ALL_PASSED, req_id=req_id):
        return rv

    proj = body.projectId
    branch = (ctx or {}).get("branch") or f"feat/{req_id}"
    workdir = (ctx or {}).get("workdir") or f"{settings.workdir_root}/feat-{req_id}"

    tasks = await _read_parallelism(req_id)

    # ─── 兼容路径：无 parallelism → 单 dev agent ─────────────────────────
    if not tasks:
        async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
            issue = await bkd.create_issue(
                project_id=proj,
                title=f"[{req_id}] [DEV]{short_title(ctx)}",
                tags=["dev", req_id],
                status_id="todo",
            )
            prompt = render(
                "dev.md.j2",
                req_id=req_id, branch=branch, workdir=workdir,
                task_id=None, task_scope=None, task_description=None,
            )
            await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
            await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")

        pool = db.get_pool()
        await req_state.update_context(pool, req_id, {
            "dev_issue_id": issue.id,
            "dev_issue_ids": {"_single": issue.id},
            "dev_tasks": [],             # 空 = 单 dev 模式
            "expected_dev_count": 1,
            "branch": branch,
            "workdir": workdir,
        })
        log.info("fanout_dev.single_mode", req_id=req_id, dev_issue=issue.id)
        return {"dev_issue_id": issue.id, "mode": "single", "count": 1}

    # ─── 并行路径 ────────────────────────────────────────────────────────
    _validate_tasks(tasks)
    first_wave = _topo_first_wave(tasks)

    dev_issue_ids: dict[str, str] = {}
    async with BKDClient(settings.bkd_base_url, settings.bkd_token) as bkd:
        for task in first_wave:
            tid = task["id"]
            scope = task["scope"]
            desc = task["description"]
            issue = await bkd.create_issue(
                project_id=proj,
                title=f"[{req_id}] [DEV:{tid}] {desc[:40]}{short_title(ctx)}",
                tags=["dev", req_id, f"dev-task:{tid}"],
                status_id="todo",
                use_worktree=True,   # 并行 dev 必须独立 worktree（记忆：不开 worktree 多 agent 共享 working tree 会互相 checkout 抹改动）
            )
            prompt = render(
                "dev.md.j2",
                req_id=req_id, branch=branch, workdir=workdir,
                task_id=tid,
                task_scope=scope,
                task_description=desc,
            )
            await bkd.follow_up_issue(project_id=proj, issue_id=issue.id, prompt=prompt)
            await bkd.update_issue(project_id=proj, issue_id=issue.id, status_id="working")
            dev_issue_ids[tid] = issue.id

    pool = db.get_pool()
    await req_state.update_context(pool, req_id, {
        "dev_issue_ids": dev_issue_ids,
        "dev_tasks": tasks,                          # 保全量，聚合 gate 用
        "expected_dev_count": len(tasks),
        "branch": branch,
        "workdir": workdir,
    })

    log.info(
        "fanout_dev.done",
        req_id=req_id,
        mode="parallel",
        first_wave=list(dev_issue_ids.keys()),
        total_tasks=len(tasks),
    )
    return {
        "mode": "parallel",
        "dev_issue_ids": dev_issue_ids,
        "first_wave_ids": list(dev_issue_ids.keys()),
        "total_tasks": len(tasks),
    }
