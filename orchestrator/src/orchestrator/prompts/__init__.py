"""Jinja2 模板渲染 helper。

模板放本目录 *.md.j2；render() 接 dict 上下文返字符串。
热更路径：设 SISYPHUS_PROMPTS_DIR 指向 ConfigMap 挂载目录（/etc/sisyphus/prompts），
          目录含 *.j2 时优先使用；否则回退 package dir（prod 默认路径）。
"""
from __future__ import annotations

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..config import settings

_PACKAGE_DIR = Path(__file__).parent


def _get_prompt_dir() -> Path:
    """返回 Jinja2 模板根目录。

    SISYPHUS_PROMPTS_DIR 存在且含 *.j2 文件时返回该目录（ConfigMap 热更路径）；
    否则回退到 package dir（prod 默认，image 内置模板）。
    """
    env_dir = os.environ.get("SISYPHUS_PROMPTS_DIR")
    if env_dir:
        candidate = Path(env_dir)
        if candidate.is_dir() and any(candidate.glob("*.j2")):
            return candidate
    return _PACKAGE_DIR


_PROMPT_DIR = _get_prompt_dir()
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
# REQ-feat-precheck-373-1777864856：precheck hook 读 stage_precheck_enabled 决定渲不渲段。
_env.globals["stage_precheck_enabled"] = settings.stage_precheck_enabled


def render(template_name: str, **context) -> str:
    return _env.get_template(template_name).render(**context)
