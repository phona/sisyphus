# Proposal: orchestrator pattern resolver + pre-resolve bundle

Refs #359 (Phase B driver), #342 (origin spec), #354 (impl-1 baseline),
#360 (Phase A spec amendment).

## 背景

Phase A（spec amendment, PR #360 / commit `ab6177d`）已落 `feat-cross-repo-env-orchestration`
增量：

- R1 manifest schema 加了 pattern-form `emits` 子字段（`pattern` + `vars`）
- R4 修订：pattern-form emit 值来自 R12 pre-resolve bundle，不再走 layer accept-env-up JSON
- R5 改名 “endpoint value resolution and passthrough”，明确 2 路 source（pattern resolver / runtime JSON）
- R12 新增 pre-resolve phase + `${SISYPHUS_*}` REQ-context 变量空间 + fail-loud

Phase A 是 spec-only PR，配套 challenger contract test（`orchestrator/tests/test_contract_endpoint_pattern_amendment_challenger.py`）
当前 RED：测试 import `orchestrator.cross_repo_env.pre_resolve_endpoint_bundle` /
`PreResolveError`，两个符号都尚未实现。

本 REQ 是 Phase B：在 orchestrator 落地 pattern resolver + pre-resolve bundle，
让 EPCA-S1..S10 全绿。

## 范围

只动 `phona/sisyphus`：

1. **`orchestrator/src/orchestrator/cross_repo_env.py`**：
   - `parse_manifest` 接收 pattern-form `emits` entry：单键 dict，value 是 `{pattern, vars}` map。
     placeholder `[A-Z_][A-Z0-9_]*` 必须在 `vars` 声明，未声明立 fail（R1 EPCA-S3）。
     `vars` value 允许字面量或 `${SISYPHUS_*}` 引用。bare-string 与 pattern-form 同列共存
     合法（EPCA-S2）。
   - 新增 `EmitPattern` dataclass + `Manifest.emit_patterns: dict[str, EmitPattern]`。
     `Manifest.emits` 仍列全部 emit field name（含 pattern 形 + bare-string），
     `inputs` 引用校验复用现路径不动。
   - 新增 `pre_resolve_endpoint_bundle(topology, manifest_loader, req_context) -> dict[str, dict[str, str]]`：
     纯函数，hermetic（无 I/O）。逐 layer 拿 manifest，对每个 pattern-form emit 做 `{VAR}` 替换 +
     `${SISYPHUS_*}` 展开，把结果写进 bundle。bare-string emit 不出现在 pre-resolve bundle（留给 R4）。
   - 新增 `PreResolveError(failed_phase: str, failed_layer: str)`：覆盖 fetch fail / 未声明
     placeholder / 未注册 `${SISYPHUS_*}` 三类失败，attribute 信息写在 exception 上。

2. **`orchestrator/src/orchestrator/actions/create_accept.py`**：
   - 拓扑解析后、per-layer `make accept-env-up` 之前调用 `pre_resolve_endpoint_bundle`，
     bundle 用 `{SISYPHUS_NAMESPACE / SISYPHUS_REQ_ID / SISYPHUS_REQ_BRANCH /
     SISYPHUS_SOURCE_REPO_SHA}` REQ context 解析。
   - bundle 持久化到 `stage_runs.context.endpoint_bundle_pre_resolved`（observability）。
   - 把 pre-resolved bundle 当作 in-memory `bundle` 起点 —— 后面层 inputs 注入直接读它。
   - per-layer JSON parse 跳过 pattern-form emits（它们已在 bundle，再去 JSON 找会误报 missing）。
   - `PreResolveError` 直接 `ACCEPT_ENV_UP_FAIL`，attribution 写 `failed_phase=pre_resolve`
     + `failed_layer=<offending repo>`，跟 R10 runtime 失败区分开。

3. **测试**：
   - 让 challenger `test_contract_endpoint_pattern_amendment_challenger.py` 全绿（RED → GREEN）
   - `orchestrator/tests/test_cross_repo_env.py` 加 `pre_resolve_*` 单测覆盖
     EPCA-S2 跳 bare、EPCA-S5 字面量替换、EPCA-S6 byte-identical、EPCA-S8/S10 fail 路径
   - 不写 integration test —— 全部用纯函数 + 内存 manifest_loader 即可

## 不在范围内

- runner pod 创建顺序重构（R12 spec 说 “before runner pod creation”，当前 impl 是 “before
  per-layer accept-env-up”）。manifest 当下从 runner pod 读，pod 已起；要前移得加
  GitHub REST manifest fetch path —— 留给后续 REQ。**不影响 EPCA-S1..S10 contract**（test 不
  assert pod 创建顺序），写在 design.md 的 “known scope gap”。
- APK build dispatch / mobile-env-up 并行触发：消费方还没落，`endpoint_bundle_pre_resolved`
  现在只有 accept-stage 自己消费 + 落 stage_runs（observability + parity 用）。Phase C
  业务仓改造里再接消费方。
- 模式语法扩展（默认值、表达式、条件等）—— 已被 Phase A spec 显式 out-of-scope。

## Roll-out

1. 合本 PR（impl-only，无 spec 改动）
2. challenger contract test EPCA-S1..S10 转绿，pr_ci_watch 通过
3. Phase C 业务仓改造（ttpos-server-go / ttpos-flutter manifest 加 pattern form）独立 REQ 派发

## 影响

| 依赖 | 验证方式 |
|---|---|
| 现有单层路径 | R8 backward-compat，无 manifest / 无 needs 仍走 `_run_legacy_single_layer`，未触及 |
| 现有多层 bare-string emits | R4 bare-string 路径不动，pattern fields 加在前面 seed bundle |
| stage_runs schema | 复用现有 `context JSONB` 列（migration `0016_stage_runs_context`），加 key 不改 schema |
| `_walk_and_load_manifests` | parse_manifest 扩展兼容旧 manifest（pattern-form 是新增 admissible form），不 break 旧 manifest |
