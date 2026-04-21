"""skip flag 通用 helper 测试。"""
from __future__ import annotations

from orchestrator.actions._skip import skip_if_enabled
from orchestrator.state import Event


def test_skip_disabled_returns_none(monkeypatch):
    monkeypatch.setattr("orchestrator.actions._skip.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions._skip.settings.skip_dev", False)
    assert skip_if_enabled("dev", Event.DEV_DONE) is None


def test_skip_specific_stage(monkeypatch):
    monkeypatch.setattr("orchestrator.actions._skip.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions._skip.settings.skip_dev", True)
    out = skip_if_enabled("dev", Event.DEV_DONE, req_id="REQ-1")
    assert out == {"skipped": True, "stage": "dev", "emit": "dev.done"}


def test_test_mode_skips_all(monkeypatch):
    monkeypatch.setattr("orchestrator.actions._skip.settings.test_mode", True)
    # 无需 skip_<stage>=True
    out = skip_if_enabled("ci_int", Event.CI_INT_PASS)
    assert out["emit"] == "ci-int.pass"


def test_dash_underscore_normalization(monkeypatch):
    """stage 名带破折号要能映射到 settings 字段（带下划线）。"""
    monkeypatch.setattr("orchestrator.actions._skip.settings.test_mode", False)
    monkeypatch.setattr("orchestrator.actions._skip.settings.skip_ci_int", True)
    # 调用方传 "ci_int"（settings 字段名形式）
    assert skip_if_enabled("ci_int", Event.CI_INT_PASS) is not None
