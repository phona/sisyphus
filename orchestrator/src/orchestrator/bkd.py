"""BKD client facade — 按 transport 选 REST 或 MCP 实现。

新版 BKD（≥0.0.65）废弃了 `/api/mcp` HTTP 端点，全部改走 REST
（`/api/projects/:projectId/issues/...`）。老版本 MCP 客户端保留，
通过 `SISYPHUS_BKD_TRANSPORT=mcp` 切回（用于回退或对接老 BKD 实例）。

调用方继续 `from .bkd import BKDClient, Issue` — 接口签名不变。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bkd_mcp import BKDMcpClient
    from .bkd_rest import BKDRestClient


@dataclass
class Issue:
    id: str
    project_id: str
    issue_number: int
    title: str
    status_id: str
    tags: list[str]
    session_status: str | None
    description: str | None = None
    created_at: str | None = None     # BKD ISO timestamp
    updated_at: str | None = None
    # BKD agent token = Claude Code externalSessionId. 仅 agent issue 有；
    # session 起来后才被 BKD 填，create_issue 返回时常为 None，需要在
    # session.completed/failed 时再 get_issue 拿到真值（写进 stage_runs）。
    external_session_id: str | None = None


def _to_issue(d: dict) -> Issue:
    """两个 transport 共用的反序列化（BKD issue dict → Issue dataclass）。"""
    return Issue(
        id=d["id"],
        project_id=d["projectId"],
        issue_number=d.get("issueNumber", 0),
        title=d.get("title", ""),
        status_id=d.get("statusId", ""),
        tags=d.get("tags", []) or [],
        session_status=d.get("sessionStatus"),
        description=d.get("description"),
        created_at=d.get("createdAt"),
        updated_at=d.get("updatedAt") or d.get("statusUpdatedAt"),
        external_session_id=d.get("externalSessionId"),
    )


def BKDClient(base_url: str, token: str, transport: str | None = None) -> BKDRestClient | BKDMcpClient:
    """Factory：按 settings.bkd_transport 选 REST（默认）或 MCP。

    显式 transport 参数优先（测试可覆盖）；否则查 config。
    """
    from .config import settings
    t = transport or settings.bkd_transport
    if t == "mcp":
        from .bkd_mcp import BKDMcpClient
        return BKDMcpClient(base_url, token)
    if t == "rest":
        from .bkd_rest import BKDRestClient
        return BKDRestClient(base_url, token)
    raise ValueError(f"Unknown BKD transport: {t!r} (use 'rest' or 'mcp')")
