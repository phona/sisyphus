"""Jinja2 模板渲染 helper。

模板放本目录 *.md.j2；render() 接 dict 上下文返字符串。
"""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

_PROMPT_DIR = Path(__file__).parent
_env = Environment(
    loader=FileSystemLoader(str(_PROMPT_DIR)),
    autoescape=select_autoescape(disabled_extensions=("md", "j2", "txt"), default=False),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render(template_name: str, **context) -> str:
    return _env.get_template(template_name).render(**context)
