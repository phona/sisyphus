"""Jinja2 模板渲染 helper。

模板放本目录 *.md.j2；render() 接 dict 上下文返字符串。
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings

_PROMPT_DIR = Path(__file__).parent
_env = Environment(
    loader=FileSystemLoader(str(_PROMPT_DIR)),
    autoescape=select_autoescape(disabled_extensions=("md", "j2", "txt"), default=False),
    trim_blocks=True,
    lstrip_blocks=True,
)

# REQ-feat-mcp-preflight-1777727213：MCP 依赖预检框架。
# 把 capability → provider 映射 + 每 stage 的 capability 需求 + probe 工具名 +
# enabled hook 列表暴露成 Jinja2 globals，让 _shared/hooks/<name>.md.j2 这些
# pluggable partial 可以直接引用，不必每个 render() call site 都显式 forward。
_env.globals["mcp_capability_providers"] = settings.mcp_capability_providers
_env.globals["mcp_capability_probe_tools"] = settings.mcp_capability_probe_tools
_env.globals["stage_mcp_requirements"] = settings.stage_mcp_requirements
_env.globals["enabled_prompt_hooks"] = settings.enabled_prompt_hooks


def render(template_name: str, **context) -> str:
    return _env.get_template(template_name).render(**context)
