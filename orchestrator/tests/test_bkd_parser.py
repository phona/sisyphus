"""BKD 响应解析单元测试（不打网络）。"""
from __future__ import annotations

import json

import pytest

from orchestrator.bkd import _parse_mcp_response, _to_issue


def _sse(envelope: dict) -> str:
    return "event: message\ndata: " + json.dumps(envelope) + "\n\n"


def test_parse_content_text_json():
    env = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps({"id": "i1", "projectId": "p"})}]},
    }
    out = _parse_mcp_response(_sse(env))
    assert out == {"id": "i1", "projectId": "p"}


def test_parse_list_form():
    arr = [{"id": "a"}, {"id": "b"}]
    env = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps(arr)}]},
    }
    assert _parse_mcp_response(_sse(env)) == arr


def test_parse_error_envelope_raises():
    env = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "boom"}}
    with pytest.raises(RuntimeError, match="BKD MCP error"):
        _parse_mcp_response(_sse(env))


def test_parse_no_data_line_raises():
    with pytest.raises(ValueError, match="no SSE data line"):
        _parse_mcp_response("event: message\n\n")


def test_to_issue_defaults_safe():
    i = _to_issue({"id": "x", "projectId": "p"})
    assert i.id == "x" and i.tags == [] and i.session_status is None
