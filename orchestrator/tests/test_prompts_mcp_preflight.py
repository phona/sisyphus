"""REQ-feat-mcp-preflight-1777727213：MCP 依赖预检 + 可插拔 prompt hook 的回归测试。

GitHub issue #270 起源：analyze / challenger / accept 这几个 stage 在 BKD
workspace 没装 aissh-tao MCP 时会硬撞工具名空跑 7min 不报错，token 白烧。
方案是 prompt 层加一段"先验证 MCP 工具可用，缺则立刻 fail-fast"，把 provider 名 +
probe 工具名都从硬编码改成 config 驱动；进一步把"约束 / 外部依赖"类提示词收成
`_shared/hooks/<name>.md.j2` 里的可插拔 hook，operator 用 helm values 覆盖
`enabled_prompt_hooks` 就能改组合（开关 mcp_preflight、加自家约束 hook、…），
不用 fork prompt。

本文件覆盖：
- 配置项可读 + 默认值符合任务描述（含新加的 enabled_prompt_hooks /
  mcp_capability_probe_tools）
- 需要 ssh_exec 的 stage 渲出预检段（analyze / challenger / accept）
- 不需要的 stage 渲不出（intake，避免污染只 chat 的 brainstorm prompt）
- 共享 hook 模板里不再硬编码 `aissh-tao` 字面量（防回滚）
- helm 关掉 mcp_preflight 时 stage 渲不出预检段（pluggable invariant）
- helm 改 probe 工具名时 prompt 里的 `mcp__<provider>__<tool>` 跟着变
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.config import settings
from orchestrator.prompts import _env, render

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "src" / "orchestrator" / "prompts"


# ── Step 1: config schema ─────────────────────────────────────────────────


def test_stage_mcp_requirements_default_covers_three_ssh_dependent_stages() -> None:
    """analyze / challenger / accept 三个 stage 默认都需要 ssh_exec —— 这是
    issue #270 复盘后框定的最小集合。如果有人改默认值漏了任意一项，对应 prompt
    会渲不出 preflight 段，回到 7min 卡死的状态。
    """
    req = settings.stage_mcp_requirements
    assert req["execute"] == ["ssh_exec"], req
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


def test_mcp_capability_probe_tools_default_routes_ssh_exec_to_servers_list() -> None:
    """换 MCP 时除了 provider 名变，probe 用的工具名也可能变（aissh-tao 的
    `servers_list` → 别家 `list_servers`）。default 锁 servers_list，operator 走
    helm 覆盖即可，prompt 模板不动。"""
    probes = settings.mcp_capability_probe_tools
    assert probes["ssh_exec"] == "servers_list", probes


def test_enabled_prompt_hooks_default_includes_mcp_preflight_and_self_issue_constraint() -> None:
    """v2（REQ-feat-precheck-373）默认开三条 hook：mcp_preflight + precheck +
    self_issue_constraint。顺序锁死：preflight 必须先就位才能让 precheck exec_run
    进 pod；两段 fail-fast 都必须先于业务约束段。新加默认 hook 时务必同步本断言
    （让 operator 升级时看得到 diff）。"""
    hooks = settings.enabled_prompt_hooks
    assert hooks == ["mcp_preflight", "precheck", "self_issue_constraint"], hooks


# ── Step 2: prompts source 不再硬编码 ─────────────────────────────────────


def test_no_hardcoded_aissh_tao_literal_in_prompt_templates() -> None:
    """共享 hook 和 stage prompt 不能直接写 `aissh-tao` 字面量 —— 必须经
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
    ("execute.md.j2", "execute"),
    ("challenger.md.j2", "challenger"),
    ("accept.md.j2", "accept"),
])
def test_ssh_dependent_stage_prompt_renders_mcp_preflight_section(
    template: str, stage: str,
) -> None:
    """analyze / challenger / accept 渲染后必须包含 preflight 段标题 + capability
    名 + 期望 provider 名 + servers_list 自检指令。任一缺失都说明 hook 没接好或
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
    hook for-loop 在 stage 没 set 或 capability 列表为空时必须静默；否则 brainstorm
    也被迫做 preflight，在没装 aissh-tao 的 workspace 上 intake 也失败 = 框架太严打误。"""
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
    out = render("execute.md.j2", **_render_kwargs("execute"))
    expected_provider = settings.mcp_capability_providers["ssh_exec"]
    assert f"mcp__{expected_provider}__servers_list" in out
    # 工具白名单段也得跟着变（不能只有 preflight 段同步）
    assert f"mcp__{expected_provider}__*" in out


# ── Step 4: pluggable hook 框架本身 ───────────────────────────────────────


def test_disabling_mcp_preflight_via_enabled_prompt_hooks_drops_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """这是 hook pluggable 的核心 invariant：operator helm 改 enabled_prompt_hooks
    把 mcp_preflight 拿掉（如临时调试 / 不走 MCP 的部署），分支 stage 渲出来不能
    再有 preflight 段；同时其它 hook（self_issue_constraint）必须照旧。
    用 monkeypatch 直接覆盖 jinja global 模拟运行时切换 —— 比改 settings 更直接，
    避开 pydantic-settings 实例缓存 / 单例延迟。"""
    monkeypatch.setitem(_env.globals, "enabled_prompt_hooks", ["self_issue_constraint"])
    out = render("execute.md.j2", **_render_kwargs("execute"))
    assert "MCP 依赖预检" not in out, (
        "关掉 mcp_preflight 后 preflight 段还在 → for-loop 没读 enabled_prompt_hooks，"
        "或者 stage 模板里有残留的硬 include，pluggable invariant 破了。"
    )
    # 另一 hook 得保留，否则相当于把 self_issue_constraint 也连坐砍了。
    assert "只改本 issue" in out, "self_issue_constraint hook 被误连坐删掉"


def test_probe_tool_name_substitution_propagates_to_rendered_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """换 MCP 后工具名变了的场景：operator 把 mcp_capability_probe_tools[ssh_exec]
    改成 'list_servers'，渲出来的 preflight 段第 0 步指令必须跟着变成
    `mcp__<provider>__list_servers`，不能死写 `servers_list` 让 agent 调到不存在的工具。"""
    monkeypatch.setitem(
        _env.globals,
        "mcp_capability_probe_tools",
        {"ssh_exec": "list_servers"},
    )
    out = render("execute.md.j2", **_render_kwargs("execute"))
    expected_provider = settings.mcp_capability_providers["ssh_exec"]
    assert f"mcp__{expected_provider}__list_servers" in out, (
        "probe 工具名覆盖没传播到 prompt → hook body 还硬写着 servers_list，"
        "换 MCP 时 prompt 跟不上"
    )
    assert "mcp__aissh-tao__servers_list" not in out, (
        "覆盖 probe 工具名后 prompt 仍然出现 servers_list，hook body 模板没读 "
        "mcp_capability_probe_tools"
    )
