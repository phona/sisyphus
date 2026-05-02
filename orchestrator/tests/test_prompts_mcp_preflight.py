"""REQ-feat-mcp-preflight-1777727213：MCP 依赖预检框架的回归测试。

GitHub issue #270 起源：analyze / challenger / accept 这几个 stage 在 BKD
workspace 没装 aissh-tao MCP 时会硬撞工具名空跑 7min 不报错，token 白烧。
方案是 prompt 层加一段"先验证 MCP 工具可用，缺则立刻 fail-fast"，并且把
provider 名从硬编码改成 config 驱动，让 operator 能换 provider 不必改源码。

本文件覆盖：
- 配置项可读 + 默认值符合任务描述
- 需要 ssh_exec 的 stage 渲出预检段（analyze / challenger / accept）
- 不需要的 stage 渲不出（intake，避免污染只 chat 的 brainstorm prompt）
- 共享 partial 模板里不再硬编码 `aissh-tao` 字面量（防回滚）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.config import settings
from orchestrator.prompts import render

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "prompts"


# ── Step 1: config schema ─────────────────────────────────────────────────


def test_stage_mcp_requirements_default_covers_three_ssh_dependent_stages() -> None:
    """analyze / challenger / accept 三个 stage 默认都需要 ssh_exec —— 这是
    issue #270 复盘后框定的最小集合。如果有人改默认值漏了任意一项，对应 prompt
    会渲不出 preflight 段，回到 7min 卡死的状态。
    """
    req = settings.stage_mcp_requirements
    assert req["analyze"] == ["ssh_exec"], req
    assert req["challenger"] == ["ssh_exec"], req
    assert req["accept"] == ["ssh_exec"], req
    # intake 是 brainstorm chat，不该显式声明 ssh_exec（即便实际用，预检失败也只会
    # 让对话失败更早，但本框架最小落地版只覆盖 analyze/challenger/accept）。
    assert req["intake"] == [], req


def test_mcp_capability_providers_default_routes_ssh_exec_to_aissh_tao() -> None:
    """ssh_exec capability 的默认 provider 是 aissh-tao，与现部署一致。换 provider
    时改 config 即可，prompt 不必动；这条 assert 锁住默认值便于回滚检测。"""
    providers = settings.mcp_capability_providers
    assert providers["ssh_exec"] == "aissh-tao", providers
    # k8s_exec 共用同一个 provider（aissh-tao 通过 SSH 跑 kubectl），这里也
    # 显式列出，免得 future 改 ssh_exec 时漏改 k8s_exec。
    assert providers["k8s_exec"] == "aissh-tao", providers


# ── Step 2: prompts source 不再硬编码 ─────────────────────────────────────


def test_no_hardcoded_aissh_tao_literal_in_prompt_templates() -> None:
    """共享 partial 和 stage prompt 不能直接写 `aissh-tao` 字面量 —— 必须经
    `mcp_capability_providers['ssh_exec']` 才行。任何回退到字面量都让 helm values
    覆盖 provider 失效，本测试是 issue #270 框架 invariant 的硬边界。"""
    hits: list[tuple[Path, int, str]] = []
    for f in sorted(_PROMPTS_DIR.rglob("*.md.j2")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), start=1):
            if "aissh-tao" in line:
                hits.append((f.relative_to(_PROMPTS_DIR), n, line.rstrip()))
    assert not hits, (
        "Hard-coded `aissh-tao` literal re-appeared in shipped prompts. "
        "Use `{{ mcp_capability_providers['ssh_exec'] }}` so config changes "
        "(helm values override) propagate to the rendered prompt.\n"
        "Hits:\n  " + "\n  ".join(f"{p}:{n}: {ln}" for p, n, ln in hits)
    )


# ── Step 3: 渲染时 preflight 段表现 ───────────────────────────────────────


def _render_kwargs(stage: str) -> dict:
    """每个 stage 起码喂的最小 render context（其他字段走默认 / undefined）。"""
    common = {
        "req_id": "REQ-mcp-preflight-test",
        "aissh_server_id": "abc-123",
        "project_id": "p1",
        "project_alias": "p1",
        "issue_id": "i1",
    }
    if stage == "accept":
        return {
            **common,
            "endpoint": "http://lab.test",
            "namespace": "ns",
            "source_issue_id": "src",
        }
    return common


@pytest.mark.parametrize("template,stage", [
    ("analyze.md.j2", "analyze"),
    ("challenger.md.j2", "challenger"),
    ("accept.md.j2", "accept"),
])
def test_ssh_dependent_stage_prompt_renders_mcp_preflight_section(
    template: str, stage: str,
) -> None:
    """analyze / challenger / accept 渲染后必须包含 preflight 段标题 + capability
    名 + 期望 provider 名 + servers_list 自检指令。任一缺失都说明 partial 没接好或
    `stage` 变量丢了 → agent 会回到 7min 空跑老路。"""
    out = render(template, **_render_kwargs(stage))
    assert "MCP 依赖预检" in out, f"{template} 缺 preflight 段标题"
    assert f"`{stage}`" in out, f"{template} 没渲染本 stage 名"
    assert "ssh_exec" in out, f"{template} 没列 ssh_exec capability"
    assert "mcp__aissh-tao__servers_list" in out, (
        f"{template} 没渲染 servers_list 自检指令；agent 不会主动 preflight"
    )
    assert "result:fail" in out and "fail-reason:mcp-missing" in out, (
        f"{template} 缺 fail-fast 指令（缺依赖时 agent 必须挂 result:fail tag）"
    )


def test_intake_prompt_does_not_render_mcp_preflight_section() -> None:
    """intake 是 chat brainstorm，stage_mcp_requirements['intake'] = []。
    partial 在 list 为空时必须静默；否则 brainstorm 也被迫做 preflight，
    在没装 aissh-tao 的 workspace 上 intake 也失败 = 框架太严打误。"""
    out = render(
        "intake.md.j2",
        req_id="REQ-x",
        aissh_server_id="a",
        project_id="p",
        project_alias="p",
        issue_id="i",
    )
    assert "MCP 依赖预检" not in out, (
        "intake 不该渲染 preflight 段。如果你刚把 intake 也加到了 "
        "stage_mcp_requirements 里，请同步更新这条 assertion 的 rationale。"
    )


def test_mcp_capability_providers_substitution_propagates_to_rendered_prompt() -> None:
    """provider 是从 config 读的 —— 这条 assert 间接保证 helm values 覆盖
    `mcp_capability_providers.ssh_exec` 后，渲出来的 prompt 工具前缀会跟着变。"""
    out = render("analyze.md.j2", **_render_kwargs("analyze"))
    expected_provider = settings.mcp_capability_providers["ssh_exec"]
    assert f"mcp__{expected_provider}__servers_list" in out
    # 工具白名单段也得跟着变（不能只有 preflight 段同步）
    assert f"mcp__{expected_provider}__*" in out
