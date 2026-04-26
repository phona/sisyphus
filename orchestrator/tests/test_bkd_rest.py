"""BKDRestClient 单测：URL 拼接 + payload 形状 + envelope 解析。"""
from __future__ import annotations

import json

import httpx
import pytest

from orchestrator.bkd_rest import BKDRestClient, _unwrap


def _resp(status: int, body: dict | list) -> httpx.Response:
    return httpx.Response(status_code=status, text=json.dumps({"success": status < 400, "data": body}) if status < 400 else json.dumps({"success": False, "error": body}))


def test_unwrap_success():
    r = httpx.Response(200, text='{"success":true,"data":{"id":"x"}}')
    assert _unwrap(r) == {"id": "x"}


def test_unwrap_failure_raises():
    r = httpx.Response(400, text='{"success":false,"error":"boom"}')
    with pytest.raises(RuntimeError, match="boom"):
        _unwrap(r)


def test_unwrap_non_json_raises():
    # 500 + 非 JSON：_unwrap 先 try .json() 失败，再 raise_for_status() → HTTPStatusError
    req = httpx.Request("GET", "https://example/api/x")
    r = httpx.Response(500, text="<html>oops</html>", request=req)
    with pytest.raises(httpx.HTTPStatusError):
        _unwrap(r)


@pytest.mark.asyncio
async def test_create_issue_payload_shape(monkeypatch):
    """create_issue 应该 POST 到 /projects/{pid}/issues 带 title/statusId/tags/useWorktree。"""
    captured = {}

    class FakeHttp:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(
                201,
                text='{"success":true,"data":{"id":"new-1","projectId":"p","issueNumber":1,"title":"t","statusId":"todo","tags":["a"]}}',
            )

        async def get(self, url, headers=None):
            return httpx.Response(404, text='{"success":false,"error":"nope"}')

        async def patch(self, url, headers=None, json=None):
            return httpx.Response(404, text='{"success":false,"error":"nope"}')

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok123")
    client._http = FakeHttp()  # type: ignore[assignment]

    issue = await client.create_issue(
        "myproj", "Add /version", ["intent:analyze", "REQ-1"],
    )

    assert captured["url"] == "https://bkd.example/api/projects/myproj/issues"
    assert captured["headers"]["Coder-Session-Token"] == "tok123"
    # `sisyphus` is auto-prepended (pipeline-identity tag); caller-supplied
    # tags follow in original order.
    assert captured["json"] == {
        "title": "Add /version",
        "statusId": "todo",
        "useWorktree": True,
        "tags": ["sisyphus", "intent:analyze", "REQ-1"],
    }
    assert issue.id == "new-1"


@pytest.mark.asyncio
async def test_create_issue_with_engine_and_model():
    """显式 engine_type + model 透传到 BKD。"""
    captured = {}

    class FakeHttp:
        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return httpx.Response(
                201,
                text='{"success":true,"data":{"id":"new-1","projectId":"p","issueNumber":1,"title":"t","statusId":"todo","tags":[]}}',
            )

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]
    await client.create_issue(
        "p", "T", [], engine_type="claude-code", model="claude-haiku-4-5",
    )
    assert captured["json"]["engineType"] == "claude-code"
    assert captured["json"]["model"] == "claude-haiku-4-5"


# ─── sisyphus auto-label (REQ-pr-label-sisyphus-auto-opened-1777217850) ─────

@pytest.mark.asyncio
async def test_create_issue_auto_injects_sisyphus_tag():
    """SAL-S1: caller passes tags w/o `sisyphus` → client prepends `sisyphus`."""
    captured = {}

    class FakeHttp:
        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return httpx.Response(
                201,
                text='{"success":true,"data":{"id":"x","projectId":"p","issueNumber":1,"title":"t","statusId":"todo","tags":[]}}',
            )

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]
    await client.create_issue("p", "T", ["analyze", "REQ-1"])
    assert captured["json"]["tags"] == ["sisyphus", "analyze", "REQ-1"]


@pytest.mark.asyncio
async def test_create_issue_does_not_duplicate_sisyphus_tag():
    """SAL-S2: caller already includes `sisyphus` → no duplicate, order preserved."""
    captured = {}

    class FakeHttp:
        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return httpx.Response(
                201,
                text='{"success":true,"data":{"id":"x","projectId":"p","issueNumber":1,"title":"t","statusId":"todo","tags":[]}}',
            )

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]
    await client.create_issue("p", "T", ["sisyphus", "analyze", "REQ-1"])
    assert captured["json"]["tags"] == ["sisyphus", "analyze", "REQ-1"]


def test_ensure_sisyphus_tag_helper_idempotent():
    """Pure helper: prepend if missing, no-op if present."""
    from orchestrator.bkd_rest import _ensure_sisyphus_tag

    assert _ensure_sisyphus_tag([]) == ["sisyphus"]
    assert _ensure_sisyphus_tag(["a", "b"]) == ["sisyphus", "a", "b"]
    assert _ensure_sisyphus_tag(["sisyphus", "a"]) == ["sisyphus", "a"]
    # Doesn't mutate caller's list
    src = ["a"]
    _ensure_sisyphus_tag(src)
    assert src == ["a"]


@pytest.mark.asyncio
async def test_mcp_create_issue_auto_injects_sisyphus_tag():
    """SAL-S3: BKDMcpClient.create_issue mirrors REST client auto-inject."""
    from unittest.mock import AsyncMock

    from orchestrator.bkd_mcp import BKDMcpClient

    client = BKDMcpClient("https://bkd.example/api", "tok")
    captured = {}

    async def _fake_call(tool, arguments):
        captured["tool"] = tool
        captured["arguments"] = arguments
        return {"id": "x", "projectId": "p", "issueNumber": 1, "title": "t",
                "statusId": "todo", "tags": []}

    client.call = _fake_call  # type: ignore[assignment,method-assign]
    client._http.aclose = AsyncMock()  # avoid real httpx close

    await client.create_issue("p", "T", ["analyze"])
    assert captured["tool"] == "create-issue"
    assert captured["arguments"]["tags"] == ["sisyphus", "analyze"]

    # idempotent
    await client.create_issue("p", "T", ["sisyphus", "x"])
    assert captured["arguments"]["tags"] == ["sisyphus", "x"]


@pytest.mark.asyncio
async def test_update_issue_only_sends_provided_fields():
    captured = {}

    class FakeHttp:
        async def patch(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return httpx.Response(
                200,
                text='{"success":true,"data":{"id":"i1","projectId":"p","issueNumber":1,"title":"x","statusId":"working","tags":["a"]}}',
            )

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]
    await client.update_issue("p", "i1", status_id="working")

    assert captured["url"] == "https://bkd.example/api/projects/p/issues/i1"
    assert captured["json"] == {"statusId": "working"}


@pytest.mark.asyncio
async def test_follow_up_issue_url_and_body():
    captured = {}

    class FakeHttp:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["json"] = json
            return httpx.Response(200, text='{"success":true,"data":{"executionId":"e1"}}')

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]
    out = await client.follow_up_issue("p", "i1", "more details")

    assert captured["url"] == "https://bkd.example/api/projects/p/issues/i1/follow-up"
    assert captured["json"] == {"prompt": "more details"}
    assert out == {"executionId": "e1"}


@pytest.mark.asyncio
async def test_cancel_issue_posts_to_cancel_endpoint():
    captured = {}

    class FakeHttp:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            return httpx.Response(200, text='{"success":true,"data":{"issueId":"i1","status":"cancelled"}}')

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]
    out = await client.cancel_issue("p", "i1")
    assert captured["url"] == "https://bkd.example/api/projects/p/issues/i1/cancel"
    assert out["status"] == "cancelled"


@pytest.mark.asyncio
async def test_get_all_assistant_messages_concat_finds_json_in_earlier_message():
    """Race bug 回归：intake-agent 把 finalized JSON 放早期 assistant-message，
    后面又发短消息 → 旧 get_last_assistant_message 漏；新 helper 拼全部找得到。
    """
    captured_url = {}

    class FakeHttp:
        async def get(self, url, headers=None):
            captured_url["url"] = url
            payload = {
                "success": True,
                "data": {
                    "logs": [
                        {"entryType": "user-message", "content": "hi"},
                        {"entryType": "assistant-message",
                         "content": '完整 finalized intent:\n```json\n{"involved_repos":["x"],"business_behavior":"b","data_constraints":"c","edge_cases":"e","do_not_touch":"d","acceptance":"a"}\n```'},
                        {"entryType": "tool-use", "content": "curl GET ..."},
                        {"entryType": "tool-result", "content": "{...}"},
                        {"entryType": "assistant-message",
                         "content": "现在 PATCH 加 result:pass"},
                        {"entryType": "tool-use", "content": "curl PATCH ..."},
                    ]
                },
            }
            return httpx.Response(200, text=json.dumps(payload))

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]

    text = await client.get_all_assistant_messages_concat("p", "issue-1")
    assert text is not None
    # JSON 必须出现在拼接结果里（race bug 触发场景）
    assert "involved_repos" in text
    assert "business_behavior" in text
    # 同时尾部短消息也要在
    assert "PATCH 加 result:pass" in text
    # URL 用了 logs?limit=200
    assert "/projects/p/issues/issue-1/logs?limit=200" in captured_url["url"]


@pytest.mark.asyncio
async def test_get_all_assistant_messages_concat_returns_none_when_empty():
    class FakeHttp:
        async def get(self, url, headers=None):
            return httpx.Response(200, text='{"success":true,"data":{"logs":[]}}')

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]
    assert await client.get_all_assistant_messages_concat("p", "i") is None


@pytest.mark.asyncio
async def test_get_last_assistant_message_unchanged_only_returns_last():
    """旧 helper 行为不变（verifier 路径还在用 it）—— regression"""
    class FakeHttp:
        async def get(self, url, headers=None):
            payload = {
                "success": True,
                "data": {
                    "logs": [
                        {"entryType": "assistant-message", "content": "first"},
                        {"entryType": "tool-use", "content": "..."},
                        {"entryType": "assistant-message", "content": "last"},
                    ]
                },
            }
            return httpx.Response(200, text=json.dumps(payload))

        async def aclose(self):
            pass

    client = BKDRestClient("https://bkd.example/api", "tok")
    client._http = FakeHttp()  # type: ignore[assignment]
    assert await client.get_last_assistant_message("p", "i") == "last"


def test_factory_picks_rest_by_default(monkeypatch):
    """BKDClient(...) 默认应返回 REST 实例（settings.bkd_transport 默认 'rest'）。"""
    from orchestrator.bkd import BKDClient
    from orchestrator.bkd_rest import BKDRestClient

    c = BKDClient("https://bkd.example/api", "tok")
    assert isinstance(c, BKDRestClient)


def test_factory_explicit_mcp():
    """显式 transport='mcp' 应返回 MCP 客户端。"""
    from orchestrator.bkd import BKDClient
    from orchestrator.bkd_mcp import BKDMcpClient

    c = BKDClient("https://bkd.example/api", "tok", transport="mcp")
    assert isinstance(c, BKDMcpClient)


def test_factory_unknown_transport_raises():
    from orchestrator.bkd import BKDClient
    with pytest.raises(ValueError, match="Unknown BKD transport"):
        BKDClient("https://bkd.example/api", "tok", transport="grpc")
