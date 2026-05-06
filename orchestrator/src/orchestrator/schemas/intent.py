"""FinalizedIntent —— sisyphus dispatch 契约的核心 typed payload。

权威设计：[docs/dispatch-contract.md](../../../../docs/dispatch-contract.md)

两条入口共同的 schema：
- `intent:intake` 入口  → intake-agent 在 BKD chat 末尾贴 ```json``` block 输出
- `intent:analyze` 入口 → 用户在 issue 描述里贴 ```intent``` block 提供

下游消费方：
- runner pod entrypoint / `_clone.py`        → repos / base_branches
- analyze / challenger / verifier prompts    → business_behavior 等业务字段
- accept stage / verifier                    → acceptance

**本 schema 当前阶段定义只读 / 不强制接进 orch**——doc-first 落地节奏：
schema 先固化，待 dispatch-contract.md 通过后再让 router / start_analyze /
intake parser 走这个 pydantic 校验。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FinalizedIntent(BaseModel):
    """统一的 dispatch payload；intake 输出与 analyze 直入贴入同款形状。

    字段语义详见 docs/dispatch-contract.md §3。
    """

    model_config = ConfigDict(
        extra="forbid",  # 防 schema 漂移；新字段必须先进 dispatch-contract.md
        populate_by_name=True,  # 接受 alias（兼容旧 `involved_repos`）
        str_strip_whitespace=True,
    )

    # ─── 环境初始化要素（runner / stage 直接消费）─────────────────
    repos: list[str] = Field(
        ...,
        alias="involved_repos",  # 兼容现状 intake 输出（intake.md.j2 仍叫 involved_repos）
        min_length=1,
        description=(
            "runner pod 启动按这个 list git clone。"
            "格式 'owner/repo'，至少 1 项。"
            "alias `involved_repos` 兼容期保留，新写一律用 `repos`。"
        ),
    )

    base_branches: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "per-repo 基线 branch；key 是 repo basename（不带 owner/），"
            "缺则各仓走 origin/HEAD。BKD `base:*` tag 优先级更高。"
        ),
    )

    # ─── 业务理解 / 验收（agent 消费）─────────────────────────
    business_behavior: str = Field(
        ..., min_length=1, description="用户视角的行为描述，一两句话"
    )
    data_constraints: str = Field(
        ..., min_length=1, description="字段 / endpoint / 错误格式 / 命名约定"
    )
    edge_cases: str = Field(
        ..., min_length=1, description="边界 / 错误 / 不能"
    )
    do_not_touch: str = Field(
        ..., min_length=1, description="防止 agent 顺手重构撞坏的范围"
    )
    acceptance: str = Field(
        ..., min_length=1, description="怎么算实现完，验收命令"
    )

    @model_validator(mode="after")
    def _check_repo_format(self) -> FinalizedIntent:
        bad = [r for r in self.repos if "/" not in r or r.count("/") != 1]
        if bad:
            raise ValueError(
                f"intent.repos 必须 'owner/repo' 格式（owner 与 repo 各不含 '/'）；"
                f"非法项: {bad}"
            )

        # base_branches key 应是 repo basename（不带 owner/）
        repo_basenames = {r.split("/", 1)[1] for r in self.repos}
        unknown = sorted(set(self.base_branches) - repo_basenames)
        if unknown:
            raise ValueError(
                f"intent.base_branches 含未知 repo basename: {unknown}；"
                f"已声明 repos basenames: {sorted(repo_basenames)}"
            )
        return self
