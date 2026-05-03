"""BKD REST 客户端（v2）：纯 HTTP，配套 BKD ≥0.0.65。

新版 BKD 不再暴露 `/api/mcp` MCP HTTP 端点（仍然有本地 stdio MCP，给本地工具用）。
所有操作改走 `/api/projects/:projectId/issues/...` REST 端点，response 走
`{success, data} | {success: false, error}` 信封。

接口签名与 BKDMcpClient 对齐，调用方零修改即可切换。
"""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog

from .bkd import Issue, Turn, _to_issue

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
        # BKD 限制 title ≤ 50 char (#306) — REQ-id 43 char + `[stage-name]` prefix
        # 直接撑爆。dispatch 层 callers 只关心可读性，不该全部去算字节。在 client
        # 这层一刀切截断给 BKD：超 50 留 49 char + `…`，BKD UI 上仍能辨识 REQ。
        # 同样防御 tags（虽然单个 tag 一般 < 50，但 PR-link tag 历史撞过）。
        if len(title) > 50:
            title = title[:49] + "…"
        sanitized_tags = [t if len(t) <= 50 else (t[:49] + "…") for t in tags]
        body: dict = {
            "title": title,
            "statusId": status_id,
            "useWorktree": use_worktree,
            "tags": _ensure_sisyphus_tag(sanitized_tags),
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
        description: str | None = None,
    ) -> Issue:
        body: dict = {}
        if status_id is not None:
            body["statusId"] = status_id
        if tags is not None:
            body["tags"] = tags
        if title is not None:
            body["title"] = title
        if description is not None:
            body["description"] = description
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

    async def last_log_activity_at(
        self,
        project_id: str,
        issue_id: str,
    ) -> datetime | None:
        """最近一条 BKD log entry 的 createdAt（UTC）；watchdog 用作活体探针。

        实现：GET /logs?limit=10 取所有 entry 的 createdAt 最大值。
        失败 / 空 / 无 createdAt 一律返回 None；调用方按"未知活动"处理。
        """
        try:
            data = await self._get(
                f"/projects/{project_id}/issues/{issue_id}/logs?limit=10"
            )
        except Exception as e:
            log.warning(
                "bkd.last_log_activity_at.failed",
                issue_id=issue_id, error=str(e),
            )
            return None
        if not isinstance(data, dict):
            return None
        logs = data.get("logs") or []
        latest: datetime | None = None
        for entry in logs:
            if not isinstance(entry, dict):
                continue
            ts_raw = entry.get("createdAt")
            if not ts_raw:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            else:
                ts = ts.astimezone(UTC)
            if latest is None or ts > latest:
                latest = ts
        return latest

    async def fetch_turns(self, project_id: str, issue_id: str) -> list[Turn]:
        """拉 BKD issue logs，折叠成 turn 维度（role × token × duration × tool_calls）。

        每条 BKD log entry 对应一行 Turn（assistant-message / user-message /
        tool-result）；未知 entryType 跳过。字段防御式读，BKD 没下发则 None。
        """
        try:
            data = await self._get(
                f"/projects/{project_id}/issues/{issue_id}/logs?limit=500"
            )
        except Exception as e:
            log.warning("bkd.fetch_turns.failed", issue_id=issue_id, error=str(e))
            return []
        if not isinstance(data, dict):
            return []
        logs = data.get("logs") or []

        _ROLE_MAP = {
            "assistant-message": "assistant",
            "user-message": "user",
            "tool-result": "tool_result",
        }

        def _int(v: object) -> int | None:
            return int(v) if v is not None else None

        turns: list[Turn] = []
        for idx, entry in enumerate(logs):
            if not isinstance(entry, dict):
                continue
            role = _ROLE_MAP.get(entry.get("entryType", ""))
            if not role:
                continue

            # Timestamp — try createdAt first, fall back to now()
            started_at: datetime
            ts_raw = entry.get("createdAt")
            if ts_raw:
                try:
                    started_at = datetime.fromisoformat(
                        str(ts_raw).replace("Z", "+00:00")
                    )
                except Exception:
                    started_at = datetime.now(UTC)
            else:
                started_at = datetime.now(UTC)

            # Token counts — BKD uses camelCase; try both naming conventions
            token_in = _int(entry.get("tokenIn") or entry.get("inputTokens"))
            token_out = _int(entry.get("tokenOut") or entry.get("outputTokens"))
            token_cache_read = _int(
                entry.get("tokenCacheRead") or entry.get("cacheReadInputTokens")
            )
            token_cache_create = _int(
                entry.get("tokenCacheCreate") or entry.get("cacheCreationInputTokens")
            )
            duration_ms = _int(entry.get("durationMs"))

            # Tool calls — only present on assistant turns
            tool_calls: list[dict] | None = None
            if role == "assistant":
                raw = entry.get("toolCalls") or entry.get("tool_calls") or []
                if isinstance(raw, list) and raw:
                    tool_calls = [
                        {
                            "name": t.get("name") or t.get("toolName", ""),
                            "input_summary": str(
                                t.get("inputSummary") or t.get("input") or ""
                            )[:200],
                            "duration_ms": t.get("durationMs"),
                            "error": t.get("error"),
                        }
                        for t in raw
                        if isinstance(t, dict)
                    ]

            turns.append(Turn(
                turn_idx=idx,
                role=role,
                tool_calls=tool_calls,
                token_in=token_in,
                token_out=token_out,
                token_cache_read=token_cache_read,
                token_cache_create=token_cache_create,
                duration_ms=duration_ms,
                started_at=started_at,
            ))

        return turns

    async def merge_tags_and_update(
        self,
        project_id: str,
        issue_id: str,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        status_id: str | None = None,
    ) -> Issue:
        """get_issue → 合 tags → update_issue → 验证。

        BKD update tags 是替换语义，必须先取再合。为消除并发读-改-写的 race，
        update 后再 get 一次验证；若 tags 和预期不一致（说明并发修改），最多重试 3 次。
        """
        for attempt in range(3):
            cur = await self.get_issue(project_id, issue_id)
            new_tags = list(cur.tags)
            for t in remove or []:
                while t in new_tags:
                    new_tags.remove(t)
            for t in add or []:
                if t not in new_tags:
                    new_tags.append(t)
            result = await self.update_issue(
                project_id, issue_id, tags=new_tags, status_id=status_id,
            )
            # 乐观锁验证：update 返回的 tags 已和预期一致 → 成功
            if set(result.tags) == set(new_tags):
                return result
            # 返回不一致（可能服务端有其他并发写），再 get 一次确认
            verify = await self.get_issue(project_id, issue_id)
            if set(verify.tags) == set(new_tags):
                return result
            log.info(
                "bkd.tag_race_detected",
                issue_id=issue_id,
                attempt=attempt,
                expected=sorted(new_tags),
                actual=sorted(verify.tags),
            )
        log.warning(
            "bkd.tag_race_exhausted",
            issue_id=issue_id,
            max_attempts=3,
        )
        return result

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
