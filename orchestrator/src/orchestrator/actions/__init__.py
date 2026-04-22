"""Action handlers registry.

每个 action handler 签名：
    async def handler(*, body: WebhookBody, req_id: str, tags: list[str], ctx: dict) -> dict

webhook.py 根据 transition.action 名查表派发。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

# Forward import 避免循环（webhook 依赖 actions，actions 不能依赖 webhook）
ActionHandler = Callable[..., Awaitable[dict[str, Any]]]

REGISTRY: dict[str, ActionHandler] = {}


def short_title(ctx: dict | None, max_len: int = 50) -> str:
    """从 ctx.intent_title 取需求标题做成短后缀（` — <title>`）方便 BKD 看板辨识。

    没设 / 太长截断。返空字符串则上层不该 append。
    """
    if not ctx:
        return ""
    t = (ctx.get("intent_title") or "").strip()
    if not t:
        return ""
    if len(t) > max_len:
        t = t[:max_len].rstrip() + "…"
    return f" — {t}"


def register(name: str):
    def deco(fn: ActionHandler) -> ActionHandler:
        REGISTRY[name] = fn
        return fn
    return deco


# 触发各 handler 注册（导入即注册）
# v0.2：真实 stage handler 替代 _v02_stubs（_v02_stubs.py 变空 placeholder）
from . import (  # noqa: E402,F401
    comment_back_dev,  # deprecated since v0.2（ci-unit fail retry 不再用）
    create_accept,
    create_ci_runner,  # deprecated since v0.2（ci-unit/ci-int stage 合并进 staging）
    create_dev,
    create_pr_ci_watch,  # v0.2 新
    create_reviewer,
    create_staging_test,  # v0.2 新
    create_test_fix,
    done_archive,
    escalate,
    fanout_specs,
    mark_spec_reviewed_and_check,
    open_gh_and_bugfix,
    start_analyze,
    teardown_accept_env,  # v0.2 新
)
