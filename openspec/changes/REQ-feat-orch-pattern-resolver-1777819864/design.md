# Design notes

## 数据模型

```python
@dataclass(frozen=True)
class EmitPattern:
    field: str               # e.g. "endpoint"
    pattern: str             # e.g. "svc.{NS}:{PORT}"
    vars: dict[str, str]     # {"NS": "${SISYPHUS_NAMESPACE}", "PORT": "8080"}
```

`Manifest.emits` 保持 `tuple[str, ...]`，列**全部** emit field name（pattern 与
bare-string 共存）—— 这样 `inputs[X] = repo.field` 校验跟 R4 bundle 派发的 surface 都
不变。新增 `Manifest.emit_patterns: dict[str, EmitPattern]` 只记录 pattern-form 条目。

判别公式：`field in manifest.emit_patterns` ⇒ pattern-form；否则 bare-string。

## YAML schema 决策

YAML：

```yaml
emits:
  - namespace                              # bare-string，老路
  - endpoint:                              # pattern-form，新路（单键 dict）
      pattern: "svc.{NS}:{PORT}"
      vars:
        NS: "${SISYPHUS_NAMESPACE}"
        PORT: "8080"
```

接受规则：

- entry 是 `str` ⇒ bare-string
- entry 是 `dict` 且只有 1 key ⇒ pattern-form；该 key 是 field 名，value 是 `{pattern, vars}` map
- 任何其他形态（多 key dict / null / list / 缺 pattern / 缺 vars）⇒ `ManifestError`

placeholder 规则：

- `re.findall(r"\{([A-Z_][A-Z0-9_]*)\}", pattern)` 收齐 placeholder
- 每一个必须在 `vars` 出现，缺的全部一起报（错误信息含 first 缺项 ⇒ 满足
  `match=r"\bPORT\b"` 类断言）
- `vars` value：字面量（任意 string）或 `^\$\{SISYPHUS_[A-Z0-9_]+\}$`。其他 `${...}` 引用
  在 R12 spec out-of-scope，本 REQ 也拒掉以防滥用 namespace。

## pre_resolve_endpoint_bundle 实现

```python
def pre_resolve_endpoint_bundle(
    topology: list[str],
    manifest_loader: Callable[[str], Manifest | None],
    req_context: dict[str, str],
) -> dict[str, dict[str, str]]:
    bundle: dict[str, dict[str, str]] = {}
    for repo in topology:
        try:
            manifest = manifest_loader(repo)
        except Exception as exc:
            raise PreResolveError(
                f"manifest fetch failed for {repo}: {exc}",
                failed_phase="pre_resolve", failed_layer=repo,
            ) from exc
        if manifest is None or not manifest.emit_patterns:
            continue
        repo_bundle: dict[str, str] = {}
        for field, ep in manifest.emit_patterns.items():
            try:
                repo_bundle[field] = _substitute(ep, req_context)
            except _UnresolvedSisyphusVar as exc:
                raise PreResolveError(
                    f"unresolved {exc.var} in {repo}.{field}",
                    failed_phase="pre_resolve", failed_layer=repo,
                ) from exc
        if repo_bundle:
            bundle[repo] = repo_bundle
    return bundle
```

- hermetic：参数只 `(topology, manifest_loader, req_context)`，无全局 / no I/O（loader 自己负责拉
  manifest）—— EPCA-S4 inspect.signature 强制
- `${SISYPHUS_*}` 展开点：`vars` value 命中 `${SISYPHUS_X}` ⇒ 必须 `req_context["SISYPHUS_X"]` 存在；
  否则 raise（不回 fallback、不空字符串）
- bare-string emits 完全跳过（不写 `repo_bundle`，bundle 里 repo entry 缺省即可）
- 失败立刻 raise（不收集多 layer 错误一起报）—— spec 说 fail-loud，简单清晰

## fail 分类

`PreResolveError` 三种触发：

| 触发 | failed_phase | failed_layer | 错误文本含 |
|---|---|---|---|
| manifest_loader 抛 | `pre_resolve` | offending OWNER/REPO | `fetch` 或 `manifest` |
| `${SISYPHUS_X}` 不在 ctx | `pre_resolve` | offending OWNER/REPO | `SISYPHUS_X` |
| pattern 引用 `{Y}` 但 vars 没 Y | （已被 parse 阶段拦） | n/a | ManifestError 路径 |

EPCA-S10 测试 `assert any(token in msg for token in ("fetch", "manifest"))`，所以
manifest fetch fail 文本必须含 `fetch` 或 `manifest`。EPCA-S8 测试要求 `SISYPHUS_NONEXISTENT_VAR` in
str(err) —— 错误文本带变量名。

## REQ context 装配（accept stage）

`_run_multi_layer_with_cache` 拓扑结束后：

```python
req_context = {
    "SISYPHUS_NAMESPACE": namespace,                           # accept-<reqid>
    "SISYPHUS_REQ_ID": req_id,
    "SISYPHUS_REQ_BRANCH": (ctx or {}).get("branch") or f"feat/{req_id}",
    "SISYPHUS_SOURCE_REPO_SHA": (ctx or {}).get("source_sha") or "",
}
try:
    pre_resolved = cross_repo_env.pre_resolve_endpoint_bundle(
        topo, lambda r: manifests.get(r), req_context,
    )
except cross_repo_env.PreResolveError as e:
    layers = _build_layers_skeleton(topo, source_repo, fail_index=topo.index(e.failed_layer))
    await _record_accept_attribution(
        req_id, failed_layer=e.failed_layer, failed_field=None, layers=layers,
    )
    pool = db.get_pool()
    await stage_runs.update_latest_stage_run_context(
        pool, req_id, "accept",
        {"failed_phase": "pre_resolve"},
    )
    return {
        "emit": Event.ACCEPT_ENV_UP_FAIL.value,
        "reason": str(e),
        "failed_phase": "pre_resolve",
        "failed_layer": e.failed_layer,
    }

await stage_runs.update_latest_stage_run_context(
    pool, req_id, "accept",
    {"endpoint_bundle_pre_resolved": pre_resolved},
)
bundle: dict[str, dict] = {repo: dict(fields) for repo, fields in pre_resolved.items()}
```

后面 layer 循环：

```python
manifest = manifests.get(repo) or Manifest()
bare_string_emits = [f for f in manifest.emits if f not in manifest.emit_patterns]
for fld in bare_string_emits:
    if fld not in parsed: ... fail
    emits_extracted[fld] = parsed[fld]
bundle.setdefault(repo, {}).update(emits_extracted)
```

—— 不动 pattern-form 路径，bare-string 走 R4 现路径，merge 而非 overwrite。

## known scope gap

R12 spec 说 pre-resolve 在 “before runner pod creation”。当前 impl 在 accept stage（pod
已起）调用，因为 manifest 当下从 runner pod 内文件读。完全契约对齐需要 GitHub REST 拉
manifest（绕开 pod），那是 follow-up REQ。EPCA-S1..S10 contract test 不依赖 pod 创建顺序，
本 REQ 不阻塞。

## migration

无新 migration —— `stage_runs.context` JSONB 列已存在（0016 加上的），新增 key
`endpoint_bundle_pre_resolved` / `failed_phase` 直接写。
