"""BKD REST 客户端（v2）：纯 HTTP，配套 BKD ≥0.0.65。

新版 BKD 不再暴露 `/api/mcp` MCP HTTP 端点（仍然有本地 stdio MCP，给本地工具用）。
所有操作改走 `/api/projects/:projectId/issues/...` REST 端点，response 走
`{success, data} | {success: false, error}` 信封。

接口签名与 BKDMcpClient 对齐，调用方零修改即可切换。
"""
from __future__ import annotations

import httpx
import structlog

from .bkd import Issue, _to_issue

log = structlog.get_logger(__name__)

# Pipeline-identity tag — auto-injected into every BKD issue created by sisyphus
# orchestrator code. Lets dashboards / humans filter `WHERE 'sisyphus' = ANY(tags)`.
SISYPHUS_TAG = "sisyphus"


class BKDRestClient:
    """REST 客户端。无 session，每次请求带 Coder-Session-Token header。"""

    def __init__(self, base_url: str, token: str):
        # base_url 形如 https://.../api（与 MCP 客户端共用 config）
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._http = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self) -> BKDRestClient:
        return self

    async def __aexit__(self, *_exc) -> None:
        await self._http.aclose()

    async def initialize(self) -> None:
        """No-op — REST 没有 init 握手。保留方法供与 MCP 客户端互换调用。"""
        return

    # ─── 高阶包装（与 BKDMcpClient 同签名）────────────────────────────────
    async def list_issues(self, project_id: str, limit: int = 200) -> list[Issue]:
        data = await self._get(f"/projects/{project_id}/issues")
        if not isinstance(data, list):
            return []
        return [_to_issue(i) for i in data[:limit]]

    async def get_issue(self, project_id: str, issue_id: str) -> Issue:
        data = await self._get(f"/projects/{project_id}/issues/{issue_id}")
        return _to_issue(data)

    async def create_issue(
        self,
        project_id: str,
        title: str,
        tags: list[str],
        status_id: str = "todo",
        use_worktree: bool = True,
        engine_type: str | None = None,
        model: str | None = None,
    ) -> Issue:
        body: dict = {
            "title": title,
            "statusId": status_id,
            "useWorktree": use_worktree,
            "tags": _ensure_sisyphus_tag(tags),
        }
        if engine_type:
            body["engineType"] = engine_type
        if model:
            body["model"] = model
        data = await self._post(f"/projects/{project_id}/issues", body)
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
        body: dict = {}
        if status_id is not None:
            body["statusId"] = status_id
        if tags is not None:
            body["tags"] = tags
        if title is not None:
            body["title"] = title
        if not body:
            # 没东西要改，直接 get 返回当前
            return await self.get_issue(project_id, issue_id)
        data = await self._patch(f"/projects/{project_id}/issues/{issue_id}", body)
        return _to_issue(data)

    async def follow_up_issue(
        self,
        project_id: str,
        issue_id: str,
        prompt: str,
    ) -> dict:
        return await self._post(
            f"/projects/{project_id}/issues/{issue_id}/follow-up",
            {"prompt": prompt},
        )

    async def cancel_issue(self, project_id: str, issue_id: str) -> dict:
        return await self._post(
            f"/projects/{project_id}/issues/{issue_id}/cancel",
            {},
        )

    async def get_last_assistant_message(
        self,
        project_id: str,
        issue_id: str,
    ) -> str | None:
        """从 BKD session logs 取最后一条 assistant-message 文本。

        verifier-agent 的 decision JSON 通常写在最后一条 assistant-message 里
        （BKD issue 对象没有 description 字段，所以没法从那读）。
        """
        try:
            data = await self._get(
                f"/projects/{project_id}/issues/{issue_id}/logs?limit=200"
            )
        except Exception as e:
            log.warning("bkd.get_logs_failed", issue_id=issue_id, error=str(e))
            return None
        if not isinstance(data, dict):
            return None
        logs = data.get("logs") or []
        # 从尾部向前找第一条 assistant-message
        for e in reversed(logs):
            if e.get("entryType") == "assistant-message":
                c = e.get("content")
                if isinstance(c, str):
                    return c
        return None

    async def get_last_user_message(
        self,
        project_id: str,
        issue_id: str,
    ) -> str | None:
        """从 BKD session logs 取最后一条 user-authored 消息。

        BAFL Case 2：用户在 BKD intent issue 上 acceptance:request-changes 时
        把最近一条用户消息塞进 verifier_reason 给 fixer prompt。BKD logs
        entry 的 entryType 用 'user-message' 标识用户输入。
        """
        try:
            data = await self._get(
                f"/projects/{project_id}/issues/{issue_id}/logs?limit=200"
            )
        except Exception as e:
            log.warning("bkd.get_logs_failed", issue_id=issue_id, error=str(e))
            return None
        if not isinstance(data, dict):
            return None
        logs = data.get("logs") or []
        for e in reversed(logs):
            if e.get("entryType") == "user-message":
                c = e.get("content")
                if isinstance(c, str):
                    return c
        return None

    async def get_all_assistant_messages_concat(
        self,
        project_id: str,
        issue_id: str,
    ) -> str | None:
        """拼所有 assistant-messages 文本，给 extractor 在大字符串里找 JSON 用。

        intake-agent 不像 verifier 那样严格："finalized JSON 必须放最后一条"。
        它常先贴 JSON 再发短消息（"PATCH 加 result:pass"）再 PATCH 触发 webhook，
        此刻 last assistant-message 没 JSON。get_last_assistant_message 漏掉。
        """
        try:
            data = await self._get(
                f"/projects/{project_id}/issues/{issue_id}/logs?limit=200"
            )
        except Exception as e:
            log.warning("bkd.get_logs_failed", issue_id=issue_id, error=str(e))
            return None
        if not isinstance(data, dict):
            return None
        logs = data.get("logs") or []
        msgs = [
            e.get("content") for e in logs
            if e.get("entryType") == "assistant-message"
            and isinstance(e.get("content"), str)
        ]
        return "\n\n---\n\n".join(msgs) if msgs else None

    async def merge_tags_and_update(
        self,
        project_id: str,
        issue_id: str,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        status_id: str | None = None,
    ) -> Issue:
        """get_issue → 合 tags → update_issue。BKD update tags 是替换语义，必须先取再合。"""
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
        return {
            "Content-Type": "application/json",
            "Coder-Session-Token": self.token,
        }

    async def _get(self, path: str) -> dict | list:
        r = await self._http.get(f"{self.base_url}{path}", headers=self._headers())
        return _unwrap(r)

    async def _post(self, path: str, body: dict) -> dict | list:
        r = await self._http.post(f"{self.base_url}{path}", headers=self._headers(), json=body)
        return _unwrap(r)

    async def _patch(self, path: str, body: dict) -> dict | list:
        r = await self._http.patch(f"{self.base_url}{path}", headers=self._headers(), json=body)
        return _unwrap(r)


def _ensure_sisyphus_tag(tags: list[str]) -> list[str]:
    """Prepend `sisyphus` to tags unless caller already includes it. Idempotent."""
    if SISYPHUS_TAG in tags:
        return list(tags)
    return [SISYPHUS_TAG, *tags]


def _unwrap(r: httpx.Response) -> dict | list:
    """剥 BKD REST 信封：{success: true, data} | {success: false, error}。"""
    try:
        payload = r.json()
    except Exception as e:
        r.raise_for_status()  # 先看是不是 HTTP 错
        raise RuntimeError(f"BKD REST: response not JSON: {r.text[:200]!r}") from e
    if not isinstance(payload, dict):
        raise RuntimeError(f"BKD REST: unexpected response type {type(payload).__name__}")
    if not payload.get("success"):
        raise RuntimeError(f"BKD REST error ({r.status_code}): {payload.get('error', 'unknown')}")
    return payload.get("data")
