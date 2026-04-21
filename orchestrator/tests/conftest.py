"""Test config: 设默认 env，让 Settings 不需要外部 .env 也能 import。"""
from __future__ import annotations

import os

os.environ.setdefault("SISYPHUS_BKD_TOKEN", "test-token")
os.environ.setdefault("SISYPHUS_PG_DSN", "postgresql://test:test@localhost/test")
os.environ.setdefault("SISYPHUS_BKD_BASE_URL", "https://bkd.example.test/api")
os.environ.setdefault("SISYPHUS_WEBHOOK_TOKEN", "test-webhook-token")
