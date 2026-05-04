"""Lightweight result types shared across thanatos drivers.

Atomic-MCP era: thanatos no longer parses spec scenarios; accept-agent drives
single-step actions through MCP and judges per-AC verdict itself. Only
``Evidence`` survives — it is what every atomic tool can attach to its return
payload (screenshot / dom / network).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Evidence:
    """Driver-captured evidence — screenshot / dom / network log."""

    dom: str | None = None
    network: list[dict[str, Any]] = field(default_factory=list)
    screenshot: str | None = None  # base64-png
