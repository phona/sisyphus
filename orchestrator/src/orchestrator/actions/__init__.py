"""Action handlers registry.

每个 action handler 签名：
    async def handler(*, body: WebhookBody, req_id: str, tags: list[str], ctx: dict) -> dict

webhook.py 根据 transition.action 名查表派发。

`idempotent` 标记保留作为元数据（M14c 砍掉了 engine 自动重试，标记仅用于
观测和未来 dispatcher 决策）。非幂等 action（create_* 类：会重复建 BKD issue
/ GH PR）任何异常都直接走 escalate。
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

# Forward import 避免循环（webhook 依赖 actions，actions 不能依赖 webhook）
ActionHandler = Callable[..., Awaitable[dict[str, Any]]]


class ActionMeta(TypedDict):
    idempotent: bool


REGISTRY: dict[str, ActionHandler] = {}
ACTION_META: dict[str, ActionMeta] = {}


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


def register(name: str, *, idempotent: bool = False):
    """注册 action handler + 声明幂等性。

    idempotent=True 代表：重试该 handler 不会产生重复副作用
    （例如 ensure_runner 是 409-safe；mark_spec 只查询 + emit）。

    M14c 砍掉了 engine 的自动重试，meta 仅作为观测信息保留；任何 action
    抛异常都走 SESSION_FAILED → ESCALATED。
    """
    def deco(fn: ActionHandler) -> ActionHandler:
        REGISTRY[name] = fn
        ACTION_META[name] = {"idempotent": idempotent}
        return fn
    return deco


# 触发各 handler 注册（导入即注册）
from . import (  # noqa: E402,F401
    _verifier,
    create_accept,
    create_dev_cross_check,
    create_pr_ci_watch,
    create_spec_lint,
    create_staging_test,
    done_archive,
    escalate,
    start_analyze,
    start_challenger,
    teardown_accept_env,
)
