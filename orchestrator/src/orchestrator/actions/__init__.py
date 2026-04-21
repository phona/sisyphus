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


def register(name: str):
    def deco(fn: ActionHandler) -> ActionHandler:
        REGISTRY[name] = fn
        return fn
    return deco


# 触发各 handler 注册（导入即注册）
from . import (  # noqa: E402,F401
    comment_back_dev,
    create_accept,
    create_ci_runner,
    create_dev,
    create_reviewer,
    create_test_fix,
    done_archive,
    escalate,
    fanout_specs,
    mark_spec_reviewed_and_check,
    open_gh_and_bugfix,
    start_analyze,
)
