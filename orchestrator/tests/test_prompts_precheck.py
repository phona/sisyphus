"""REQ-feat-precheck-373-1777864856：stage-agent step-0 unified precheck hook 回归测试。

GitHub issue #373 起源：5/4 dogfood v3/v4/v5 REQ 派出去后跑到 dev_cross_check 才
发现 fvm/flutter_sdk symlink 缺，watchdog 7min kill 整 stage 重派，前面 ANALYZE +
CHALLENGER 几千 token 全白烧。同 pattern 之前出过 token 缺 (#365) / KUBECONFIG 错
(#292)。

方案：复用 #270 的 pluggable hook 框架（`enabled_prompt_hooks` filename + config-list），
新增 `_shared/hooks/precheck.md.j2`，stage agent 第 0.5 步统一跑：
- pod env 必填 (SISYPHUS_REQ_ID / GH_TOKEN / KUBECONFIG)
- 工具必装 (gh / kubectl / make)
- 业务仓自报 (`make ci-precheck`，仓侧**可选**契约 target)
任一硬失败 → result:fail + fail-reason:precheck:<item>，verifier 直接 escalate（不重试）。

本文件覆盖 PRECHECK-S1..S6（spec/feat-stage-precheck/spec.md）。
"""
from __future__ import annotations

import pytest

from orchestrator.config import settings
from orchestrator.prompts import _env, render

# ── PRECHECK-S6 / config schema ────────────────────────────────────────────


def test_enabled_prompt_hooks_default_includes_precheck_in_canonical_order() -> None:
    """PRECHECK-S6: 默认顺序锁死：mcp_preflight → precheck → self_issue_constraint。

    顺序不是装饰：
    - mcp_preflight 必须先就位 —— precheck 要 `mcp__<provider>__exec_run` 进 pod
    - 两段 fail-fast 必须排在 self_issue_constraint 之前（业务约束段）
    新加 hook 务必同步本断言（让 operator 升级时看得到 diff）。
    """
    assert settings.enabled_prompt_hooks == [
        "mcp_preflight",
        "precheck",
        "self_issue_constraint",
    ], settings.enabled_prompt_hooks


def test_stage_precheck_enabled_default_covers_runner_pod_stages() -> None:
    """precheck 默认在所有「跑 runner pod」的 stage 都开；intake / done_archive 关掉。

    intake 是 chat brainstorm，done_archive 是 orchestrator 后台动作（不派 agent），
    都不该被 precheck 框约束。
    """
    enabled = settings.stage_precheck_enabled
    for stage in ("execute", "challenger", "accept", "staging_test", "pr_ci_watch", "bugfix"):
        assert enabled[stage] is True, (stage, enabled)
    for stage in ("intake", "done_archive"):
        assert enabled[stage] is False, (stage, enabled)


# ── PRECHECK-S1 / hook 渲段 ────────────────────────────────────────────────


def _render_kwargs(stage: str) -> dict:
    common = {
        "req_id": "REQ-precheck-test",
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
    if stage in ("staging_test", "pr_ci_watch", "bugfix"):
        return {**common, "source_issue_id": "src"}
    return common


@pytest.mark.parametrize(
    "template,stage",
    [
        ("execute.md.j2", "execute"),
        ("challenger.md.j2", "challenger"),
        ("accept.md.j2", "accept"),
        ("staging_test.md.j2", "staging_test"),
        ("pr_ci_watch.md.j2", "pr_ci_watch"),
        ("bugfix.md.j2", "bugfix"),
    ],
)
def test_precheck_section_renders_for_runner_pod_bound_stages(
    template: str, stage: str,
) -> None:
    """PRECHECK-S1: 所有跑 runner pod 的 stage prompt 必须渲出 precheck 段，
    含三类 check（env / tool / ci-precheck）+ stage 名 + fail tag scheme。

    任一缺失说明 hook 没接好 / `stage` 变量没 set / for-loop 没读
    `enabled_prompt_hooks` → agent 跳过 precheck，回到 7 min 卡死老路。
    """
    out = render(template, **_render_kwargs(stage))
    assert "Stage Precheck" in out, f"{template} 缺 Stage Precheck 段标题"
    assert f"`{stage}`" in out, f"{template} precheck 段没渲染本 stage 名"
    # 三类 check 都得在
    assert "SISYPHUS_REQ_ID" in out, f"{template} precheck 段缺 env 检查"
    assert "gh auth status" in out, f"{template} precheck 段缺 gh 工具检查"
    assert "kubectl version --client" in out, f"{template} precheck 段缺 kubectl 工具检查"
    assert "make ci-precheck" in out, f"{template} precheck 段缺业务仓 ci-precheck 调用"
    # PRECHECK-S4: fail tag scheme
    assert "result:fail" in out and "fail-reason:precheck:" in out, (
        f"{template} 缺 fail tag scheme（agent 不会按规约 emit fail）"
    )


# ── PRECHECK-S2 / 静默条件 ────────────────────────────────────────────────


def test_intake_does_not_render_precheck_section() -> None:
    """PRECHECK-S2: intake 是 chat brainstorm，stage_precheck_enabled[intake] = False。

    hook 必须在 stage 没 set 或 stage_precheck_enabled[stage] 为 False 时静默 ——
    否则 brainstorm 也被迫做 pod-level precheck，没装 aissh-tao 的 workspace 上
    intake 也直接红，测试场景里 chat 通道完全没法跑。
    """
    out = render(
        "intake.md.j2",
        req_id="REQ-x",
        aissh_server_id="a",
        project_id="p",
        project_alias="p",
        issue_id="i",
    )
    assert "Stage Precheck" not in out, (
        "intake 渲出了 precheck 段。如果你刚把 intake 加到 stage_precheck_enabled=True，"
        "请同步更新本 assertion 的 rationale。"
    )


# ── PRECHECK-S3 / pluggable invariant ─────────────────────────────────────


def test_disabling_precheck_via_enabled_prompt_hooks_drops_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRECHECK-S3: pluggable invariant —— operator 把 precheck 从
    enabled_prompt_hooks 拿掉（如临时调试 / 不走 runner pod 的部署），
    分支 stage 渲出来不能再有 precheck 段；其它 hook 必须照旧。
    用 monkeypatch 直接覆盖 jinja global 模拟运行时切换 —— 比改 settings 更直接，
    避开 pydantic-settings 实例缓存 / 单例延迟。
    """
    monkeypatch.setitem(
        _env.globals,
        "enabled_prompt_hooks",
        ["mcp_preflight", "self_issue_constraint"],
    )
    out = render("execute.md.j2", **_render_kwargs("execute"))
    assert "Stage Precheck" not in out, (
        "关掉 precheck 后段还在 → for-loop 没读 enabled_prompt_hooks，pluggable invariant 破了"
    )
    # 其它 hook 不能连坐被砍
    assert "MCP 依赖预检" in out, "mcp_preflight 被误连坐"
    assert "只改本 issue" in out, "self_issue_constraint 被误连坐"


def test_disabling_stage_precheck_enabled_drops_section_for_that_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """补充 invariant：operator 关掉单 stage 的 precheck（如分阶段灰度），
    其它 stage 不受影响 —— stage_precheck_enabled 是 dict 级开关。
    """
    monkeypatch.setitem(
        _env.globals,
        "stage_precheck_enabled",
        {**settings.stage_precheck_enabled, "execute": False},
    )
    out_off = render("execute.md.j2", **_render_kwargs("execute"))
    out_on = render("challenger.md.j2", **_render_kwargs("challenger"))
    assert "Stage Precheck" not in out_off, "execute 关掉了 precheck，但段还在"
    assert "Stage Precheck" in out_on, "关 execute 误连坐 challenger"


# ── PRECHECK-S5 / provider 不硬编 ─────────────────────────────────────────


def test_precheck_hook_does_not_hardcode_aissh_tao_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRECHECK-S5: 换 MCP provider 时 precheck 段必须跟着变 —— 不能硬编 aissh-tao。

    本 invariant 跟 mcp_preflight 测试里的 `test_no_hardcoded_aissh_tao_literal`
    类似但更针对运行时：覆盖 mcp_capability_providers['ssh_exec']，渲出来必须
    含新 provider，绝不出现 aissh-tao。
    """
    monkeypatch.setitem(
        _env.globals,
        "mcp_capability_providers",
        {**settings.mcp_capability_providers, "ssh_exec": "test-provider"},
    )
    out = render("execute.md.j2", **_render_kwargs("execute"))
    # 段头到 step 内的命令都得切到新 provider
    assert "mcp__test-provider__exec_run" in out, (
        "precheck 段没用 mcp_capability_providers['ssh_exec'] 渲 exec_run 调用"
    )
    # 找 precheck 段 slice，避免 mcp_preflight 段内合规出现 aissh-tao 干扰
    start = out.index("Stage Precheck")
    # precheck 段以分隔线 `─────────` 结束（hook 末尾）
    end = out.index("─────────", start)
    precheck_block = out[start:end]
    assert "aissh-tao" not in precheck_block, (
        "precheck 段硬编了 aissh-tao 字面量，operator 换 provider 时跟不上"
    )
