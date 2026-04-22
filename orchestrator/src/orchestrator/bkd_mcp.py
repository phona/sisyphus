"""BKD MCP 客户端（v1）：JSON-RPC over Streamable HTTP / SSE。

封装常用 tools/call：create-issue / update-issue / follow-up-issue /
list-issues / cancel-issue / get-issue。

适配 BKD <0.0.65 的版本（带 `/api/mcp` 端点）。新版本走 bkd_rest.BKDRestClient。
"""
from __future__ import annotations

import json
import re

import httpx
import structlog

from .bkd import Issue, _to_issue

log = structlog.get_logger(__name__)

# SSE event line: `data: {...}` — 抠出 JSON
_SSE_DATA_RE = re.compile(r"^data:\s*(.+)$", re.MULTILINE)


class BKDMcpClient:
    """One client per webhook handling. SID 在 init() 拿到，后续 tools/call 复用。"""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.session_id: str | None = None
        self._http = httpx.AsyncClient(timeout=30.0)
        self._req_id = 0

    async def __aenter__(self) -> BKDMcpClient:
        await self.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def initialize(self) -> None:
        """MCP initialize → 拿 mcp-session-id 头。"""
        r = await self._http.post(
            f"{self.base_url}/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Coder-Session-Token": self.token,
            },
            json={
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "sisyphus-orchestrator", "version": "0.1"},
                },
            },
        )
        r.raise_for_status()
        sid = r.headers.get("mcp-session-id")
        if not sid:
            raise RuntimeError("BKD MCP init returned no mcp-session-id")
        self.session_id = sid
        # 通知 initialized（必需，否则后续 tools/call 报 "not initialized"）
        await self._http.post(
            f"{self.base_url}/mcp",
            headers=self._headers(),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        log.debug("bkd.initialized", session_id=sid)

    async def call(self, tool: str, arguments: dict) -> dict:
        """tools/call 通用入口。返回 result.content[0].text 解 JSON 后的对象。"""
        r = await self._http.post(
            f"{self.base_url}/mcp",
            headers=self._headers(),
            json={
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
        )
        r.raise_for_status()
        return _parse_mcp_response(r.text)

    # ─── 高阶包装 ──────────────────────────────────────────────────────────
    async def list_issues(self, project_id: str, limit: int = 200) -> list[Issue]:
        data = await self.call("list-issues", {"projectId": project_id, "limit": limit})
        return [_to_issue(i) for i in data] if isinstance(data, list) else []

    async def get_issue(self, project_id: str, issue_id: str) -> Issue:
        data = await self.call("get-issue", {"projectId": project_id, "issueId": issue_id})
        return _to_issue(data)

    async def create_issue(
        self,
        project_id: str,
        title: str,
        tags: list[str],
        status_id: str = "todo",
        use_worktree: bool = False,
    ) -> Issue:
        data = await self.call("create-issue", {
            "projectId": project_id,
            "title": title,
            "statusId": status_id,
            "useWorktree": use_worktree,
            "tags": tags,
        })
        return _to_issue(data)

    async def update_issue(
        self,
        project_id: str,
        issue_id: str,
        *,
        status_id: str | None = None,
        tags: list[str] | None = None,
        title: str | None = None,
    ) -> Issue:
        args: dict = {"projectId": project_id, "issueId": issue_id}
        if status_id is not None:
            args["statusId"] = status_id
        if tags is not None:
            args["tags"] = tags
        if title is not None:
            args["title"] = title
        data = await self.call("update-issue", args)
        return _to_issue(data)

    async def follow_up_issue(self, project_id: str, issue_id: str, prompt: str) -> dict:
        return await self.call("follow-up-issue", {
            "projectId": project_id,
            "issueId": issue_id,
            "prompt": prompt,
        })

    async def cancel_issue(self, project_id: str, issue_id: str) -> dict:
        return await self.call("cancel-issue", {"projectId": project_id, "issueId": issue_id})

    async def merge_tags_and_update(
        self,
        project_id: str,
        issue_id: str,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        status_id: str | None = None,
    ) -> Issue:
        """get-issue → merge tags → update-issue。

        BKD update-issue 的 tags 是 *替换* 语义。要保留其他 tag 必须先取再合再写。
        """
        cur = await self.get_issue(project_id, issue_id)
        new_tags = list(cur.tags)
        for t in remove or []:
            while t in new_tags:
                new_tags.remove(t)
        for t in add or []:
            if t not in new_tags:
                new_tags.append(t)
        return await self.update_issue(
            project_id, issue_id, tags=new_tags, status_id=status_id,
        )

    # ─── 内部 ──────────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Coder-Session-Token": self.token,
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id


def _parse_mcp_response(body: str) -> dict | list:
    """SSE event-stream → 取最后一个 data 块的 JSON-RPC envelope → result.content[0].text 解 JSON。"""
    matches = _SSE_DATA_RE.findall(body)
    if not matches:
        raise ValueError(f"no SSE data line in response: {body[:200]!r}")
    envelope = json.loads(matches[-1])
    if "error" in envelope:
        raise RuntimeError(f"BKD MCP error: {envelope['error']}")
    result = envelope.get("result", {})
    content = result.get("content")
    if not content or not isinstance(content, list):
        return result  # 非 content 形式，直接返回 result
    text = content[0].get("text", "")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}  # 非 JSON 形式（少见）
