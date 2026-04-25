# REQ-default-involved-repos-1777124541: feat(helm): default_involved_repos=[phona/sisyphus] for self-dogfood single-repo deploy

## 问题

REQ-clone-fallback-direct-analyze-1777119520 在
`orchestrator/src/orchestrator/config.py` 加了
`default_involved_repos: list[str] = Field(default_factory=list)`（env
`SISYPHUS_DEFAULT_INVOLVED_REPOS`）作为 4 层 fallback 的最后一层（L4），
但**只到了 Settings**：helm chart 既没在 `values.yaml` 暴露这个 key，也没
在 `configmap.yaml` 把 value 转成 env var 注入 orchestrator Pod。

后果：

- helm 装的 sisyphus 实际 `default_involved_repos == []`（pydantic 默认空），
  L4 永远 miss
- 直接 analyze 入口（`intent:analyze` 一步到位、ctx 无 involved_repos、tags
  也无 `repo:`）拿不到任何 fallback，回到 prompt 里"agent 自跑 helper"软约束 ——
  正是上一个 REQ 想消掉的痛点
- 上一个 REQ 的 proposal `## 取舍` 里写的 "单仓部署（sisyphus self-dogfood）
  通过 helm values `extraEnv: SISYPHUS_DEFAULT_INVOLVED_REPOS=phona/sisyphus`
  显式配" 是空头支票 —— 这个 chart 没有 `extraEnv` 机制，也没专门的字段

## 方案

把 helm chart 的 self-dogfood 默认配上 `default_involved_repos=[phona/sisyphus]`，
让 sisyphus 装一次就能跑 single-repo 自托管：

1. **`orchestrator/helm/values.yaml`** —— `env` 块新增
   `default_involved_repos: [phona/sisyphus]`。这个 chart 就是 sisyphus
   self-deployment chart（image `ghcr.io/phona/sisyphus-orchestrator`），
   ship 一个 opinionated default 比留 `[]` 让 ops 必填更省事
2. **`orchestrator/helm/templates/configmap.yaml`** —— 新增条件块，把
   `env.default_involved_repos` 列表用 helm `toJson` 编成 JSON array string
   写入 `SISYPHUS_DEFAULT_INVOLVED_REPOS`（list 空 → 不写 key，让 Settings 用
   `default_factory=list`）。**用 JSON 不用 csv**：pydantic-settings v2 对
   `list[str]` 复合字段默认走 JSON decoder，csv `phona/x,phona/y` 会让
   orchestrator 启动直接抛 `SettingsError`（实测 v2.6+，跟 spec
   `multi-layer-involved-repos-fallback` 早先的 csv 论断不符；本 REQ 选
   实际能跑的路径，spec/test 一并校正成 JSON 编码）
3. **测试** —— 新增 `test_contract_helm_default_involved_repos.py`：
   - 静态读 `orchestrator/helm/values.yaml`，断言
     `env.default_involved_repos == ["phona/sisyphus"]`
   - 静态读 `orchestrator/helm/templates/configmap.yaml`，断言含
     `SISYPHUS_DEFAULT_INVOLVED_REPOS` + `toJson` 写入逻辑
   - pydantic 行为测试：env 设 `["phona/sisyphus","phona/foo"]` → Settings
     解出长度 2 list

### 故意不做

- **不**改 Settings 默认 `default_factory=list`。chart-level 默认是部署意见，
  不是代码库意见；多仓部署 / 跨项目部署的 ops 想 override 仍然走 helm values。
  REQ-clone-fallback-direct-analyze-1777119520 的契约
  `test_default_involved_repos_setting_exists` 显式锁了 default 必须是 `[]`，
  改了就回归
- **不**引入通用 `extraEnv` 机制。当前 chart 用具名字段（`env.skip_*`、
  `env.snapshot_exclude_project_ids` 等）一一映射，跟现有风格一致；通用
  `extraEnv` 是另一个口子，留到真有 N+1 个临时 env 时再开
- **不**改 `values.dev.yaml`。dev override 只在需要跟 prod 区分时贴；
  self-dogfood 单仓默认对 dev 一样合适
- **不**动 `_clone.py`。L4 解析逻辑早在上个 REQ 写好了

## 取舍

- **为什么 ship `[phona/sisyphus]` 而不是留 `[]`** —— 这个 chart 是
  sisyphus self-deployment 专用（`image.repository: ghcr.io/phona/sisyphus-orchestrator`），
  装它就是装 sisyphus。ship `[phona/sisyphus]` 把"装 → 能跑"的距离从
  "ops 还得知道有这个 env" 缩到"helm install"。多仓 / 多项目部署的
  ops 本来就要写 my-values.yaml 覆盖一堆字段，多 override 一个 list 不算负担
- **为什么 list 空时省略 env var** —— 显式写 `SISYPHUS_DEFAULT_INVOLVED_REPOS=""`
  会让 pydantic csv parse 出 `[""]`（一个空字符串元素），跟 `[]` 行为不同；
  省略 env var 让 Settings 走 `default_factory=list` = `[]` 是干净路径
- **为什么用 `toJson` 而不是 csv `join`** —— 实测 pydantic-settings v2
  对 `list[str]` 字段只走 JSON decoder（csv 会启动失败）；既有
  `snapshot_exclude_project_ids` 的 `join "," .` 现状是潜在 bug（启用了
  也会让 orchestrator boot 不来），但**本 REQ 不动它**（out of scope；
  生产看上去没人真在 helm values 里设过它，所以未踩雷）。新键直接走
  正确的 JSON 编码就行
- **为什么测试静态 grep configmap.yaml 而不是 helm template render** —— helm
  CLI 可能不在 pytest 环境装，且 configmap.yaml 模板逻辑足够简单
  (`{{- with .Values.env.default_involved_repos }}{{ join "," . | quote }}{{- end }}`),
  grep 能锁住核心契约；render 测留给 helm dry-run / e2e

## 影响面

- `orchestrator/helm/values.yaml`：`env` 块新增 `default_involved_repos: [phona/sisyphus]`
- `orchestrator/helm/templates/configmap.yaml`：新增条件块写入 `SISYPHUS_DEFAULT_INVOLVED_REPOS`
- `orchestrator/tests/test_contract_helm_default_involved_repos.py`：新增。锁
  - values.yaml 默认含 `phona/sisyphus`
  - configmap.yaml 含 `SISYPHUS_DEFAULT_INVOLVED_REPOS` 写入逻辑
  - Settings 通过 `SISYPHUS_DEFAULT_INVOLVED_REPOS=a,b` 能解出 `["a","b"]`

不动 / 不影响：

- `orchestrator/src/orchestrator/config.py`：Settings field 已存在，default
  仍是 `[]`
- `orchestrator/src/orchestrator/actions/_clone.py`：L4 解析逻辑不变
- `orchestrator/helm/values.dev.yaml`：dev override 不需要再贴一次
- 任何 `start_*` action / state machine：调用形状不变
