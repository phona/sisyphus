"""webhook 端点的 X-Sisyphus-Token 校验。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from orchestrator.webhook import _verify_token


def test_missing_header_rejected():
    with pytest.raises(HTTPException) as ei:
        _verify_token(None)
    assert ei.value.status_code == 401


def test_wrong_token_rejected():
    with pytest.raises(HTTPException) as ei:
        _verify_token("nope")
    assert ei.value.status_code == 401


def test_correct_token_passes():
    # conftest 设的 SISYPHUS_WEBHOOK_TOKEN=test-webhook-token
    _verify_token("test-webhook-token")  # 不抛 = 通过


def test_constant_time_compare(monkeypatch):
    """前缀正确但短一截也得拒（hmac.compare_digest 不会因 length-mismatch 短路）。"""
    with pytest.raises(HTTPException):
        _verify_token("test-webhook-tok")
    with pytest.raises(HTTPException):
        _verify_token("test-webhook-tokenX")
