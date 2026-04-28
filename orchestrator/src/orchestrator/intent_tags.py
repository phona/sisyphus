"""Intent-tag propagation helper (REQ-ux-tags-injection-1777257283).

人在 BKD intent issue 上挂的 hint tag —— `repo:owner/name`、`spec_home_repo:`、
`ux:fast-track`、`priority:high` 等 —— 是给整条 REQ 流水线看的 "用户上下文"。
sisyphus 的 stage action 在创 / 改 sub-issue 时本来硬编码 tags 数组覆盖掉
所有用户 hint。本模块给一个统一的 "non-propagatable" 黑名单工具，让每个
callsite 可以无脑追加 `*filter_propagatable_intent_tags(body.tags)` 把 hint
转发下去。

设计为黑名单（"sisyphus 管的不传"）而非白名单：sisyphus 自己管的 tag 集合
稳定且文档化在 docs/api-tag-management-spec.md，用户 hint 是开放集合（团队
自定义、未来扩展），列白名单会随时漏。

不做任何 IO / 不抛异常 / 纯函数 —— callsite 可以无条件调用。
"""
from __future__ import annotations

import re
from collections.abc import Iterable

# Sisyphus 自己管的 exact tag —— flow-control + pipeline-identity + stage role。
# 跟 docs/api-tag-management-spec.md 表 §3 + §1（含 sisyphus 标识）一致。
SISYPHUS_MANAGED_EXACT: frozenset[str] = frozenset({
    "sisyphus",
    "intake",
    "analyze",
    "challenger",
    "verifier",
    "fixer",
    "accept",
    "staging-test",
    "pr-ci",
    "done-archive",
    "pr-ready",
})

# Sisyphus 自己管的 tag 前缀。覆盖 §1 入口 / §4 结果 / §5 verifier / §6 fixer /
# §7 多仓辅助里所有 sisyphus 程序写 / 读的 tag。
#
# `parent:` / `parent-id:` / `parent-stage:` 三个独立前缀都列出来：startswith
# 比较虽然天然吃掉长前缀，但显式列举让本契约可读 —— 任何 `parent...:` 形式
# 全归 sisyphus，避免后续误新增 `parent:foo:bar` 类自定义 tag 撞车。
SISYPHUS_MANAGED_PREFIXES: tuple[str, ...] = (
    "intent:",
    "result:",
    "pr-ci:",
    "verify:",
    "trigger:",
    "decision:",
    "fixer:",
    "parent:",
    "parent-id:",
    "parent-stage:",
    "target:",
    "round-",
    "pr:",
)

# REQ id 形如 `REQ-<slug>`，每个 callsite 显式注入；过滤防止 hint 转发时重复 /
# 错配到其他 REQ。同 router.REQ_ID_RE。
_REQ_ID_RE = re.compile(r"^REQ-[\w-]+$")


def is_sisyphus_managed_tag(tag: object) -> bool:
    """True 表示该 tag 归 sisyphus 管理，不应作为 user hint 转发。

    非字符串 / 空串 → True（顺手当作 "不是有效 hint" 一并屏蔽掉）。
    """
    if not isinstance(tag, str):
        return True
    s = tag.strip()
    if not s:
        return True
    if s in SISYPHUS_MANAGED_EXACT:
        return True
    if any(s.startswith(p) for p in SISYPHUS_MANAGED_PREFIXES):
        return True
    if _REQ_ID_RE.match(s):
        return True
    return False


def filter_propagatable_intent_tags(tags: Iterable[object] | None) -> list[str]:
    """从输入 tags 取出可向下游转发的 user hint 子集。

    规则：
    - 跳 sisyphus-managed（exact / 前缀 / REQ-id pattern）
    - 跳非字符串 / 空串 / 仅空白
    - 保留首次出现顺序
    - 去重
    """
    if not tags:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        if not isinstance(t, str):
            continue
        s = t.strip()
        if not s:
            continue
        if is_sisyphus_managed_tag(s):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out
