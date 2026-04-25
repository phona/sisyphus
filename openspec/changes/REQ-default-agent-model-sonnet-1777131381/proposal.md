# REQ-default-agent-model-sonnet-1777131381: feat(config): default agent_model = claude-sonnet-4-6

## 问题

`orchestrator/src/orchestrator/config.py` 中 `agent_model: str | None = None`，
`None` 表示用 BKD per-engine 默认，实际走 claude-opus-4 / opus-4-5（最贵模型）。

sisyphus 起的所有子 agent（verifier / fixer / accept / pr_ci_watch /
done_archive / staging_test）都走这个默认，**每次 REQ 流转都是 opus 成本**。
而这些 agent 做的是有固定结构的、可预期的任务（输出 JSON decision、跑 CI 命令、
归档 spec），不需要 opus 的推理深度。

## 方案

把 `agent_model` 的 Python 默认值从 `None` 改为 `"claude-sonnet-4-6"`：

```python
# before
agent_model: str | None = None

# after
agent_model: str | None = "claude-sonnet-4-6"
```

helm values 已经有 `SISYPHUS_AGENT_MODEL` 的 env 覆盖路径（pydantic-settings
env_prefix = `SISYPHUS_`），生产可用 helm 覆盖；测试依旧用 `claude-haiku-4-5`。

### 故意不做

- **不**在 `values.yaml` 重复写死 model 字符串 —— Settings 层已有默认，
  helm 只在需要覆盖时才写，保持"只有一个真相"
- **不**改 `values.dev.yaml` —— dev 环境也受益于 sonnet；如需 haiku 测试
  可在 CI job 的 helm 参数里临时覆盖
- **不**改 analyze agent 的 model —— analyze agent 由 user 创 intent issue
  时决定，sisyphus 不控（参见代码注释）

## 取舍

- **为什么 sonnet-4-6 而非 haiku** —— 生产 sisyphus 子 agent（verifier / fixer）
  需要理解 stage 日志、判断 pass/fix/escalate，haiku 在复杂 failure case
  准确率不稳定；sonnet 在成本和准确率之间取得平衡
- **为什么改 Python 默认而非 helm 默认** —— Settings 层的 `None` 默认有误导性
  （暗示"省钱"但实际是最贵模型）；显式写出默认让代码意图清晰，helm 覆盖
  机制保留弹性

## 影响面

- `orchestrator/src/orchestrator/config.py`：`agent_model` 默认 `None` → `"claude-sonnet-4-6"`
- `orchestrator/tests/test_contract_default_agent_model_sonnet.py`：新增合约测试
