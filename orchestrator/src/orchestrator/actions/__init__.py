"""Action handlers registry.

每个 action handler 签名：
    async def handler(*, body: WebhookBody, req_id: str, tags: list[str], ctx: dict) -> dict

webhook.py 根据 transition.action 名查表派发。

M9：register 带 idempotent kwarg，engine.step 捕到 action 异常后用
ACTION_META[name]["idempotent"] 决定是否重试（见 retry.policy.decide_action_fail）。
非幂等 action（create_* 类：会重复建 BKD issue / GH PR）默认直接 escalate，
不自动重试。
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
    M9 engine.step 异常路径只重试 idempotent=True 的 action。

    默认 False（保守：创建新 BKD issue / GH PR 的 action 重试会重复建）。
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
    create_dev,
    create_pr_ci_watch,
    create_staging_test,
    done_archive,
    escalate,
    fanout_specs,
    mark_spec_reviewed_and_check,
    open_gh_and_bugfix,
    spawn_diagnose,
    start_analyze,
    teardown_accept_env,
)
