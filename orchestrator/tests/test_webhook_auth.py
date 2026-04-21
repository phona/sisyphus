"""webhook 端点的 Authorization: Bearer 校验。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from orchestrator.webhook import _verify_token


def test_missing_header_rejected():
    with pytest.raises(HTTPException) as ei:
        _verify_token(None)
    assert ei.value.status_code == 401
    assert ei.value.headers.get("WWW-Authenticate", "").startswith("Bearer")


def test_no_bearer_scheme_rejected():
    """没有 Bearer 前缀（如 Basic / 裸 token）也拒。"""
    with pytest.raises(HTTPException):
        _verify_token("test-webhook-token")          # 裸 token
    with pytest.raises(HTTPException):
        _verify_token("Basic dGVzdDp0ZXN0")          # 错误 scheme


def test_wrong_token_rejected():
    with pytest.raises(HTTPException) as ei:
        _verify_token("Bearer nope")
    assert ei.value.status_code == 401


def test_correct_bearer_token_passes():
    # conftest 设的 SISYPHUS_WEBHOOK_TOKEN=test-webhook-token
    _verify_token("Bearer test-webhook-token")        # 不抛 = 通过
    _verify_token("bearer test-webhook-token")        # scheme 大小写不敏感
    _verify_token("Bearer  test-webhook-token  ")     # 多余空白容忍


def test_partial_token_match_rejected():
    """前缀对但短一截 / 多一截都拒（hmac.compare_digest 防 length-mismatch 短路）。"""
    with pytest.raises(HTTPException):
        _verify_token("Bearer test-webhook-tok")
    with pytest.raises(HTTPException):
        _verify_token("Bearer test-webhook-tokenX")
