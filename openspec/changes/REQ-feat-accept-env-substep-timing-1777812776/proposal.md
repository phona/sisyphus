# accept-env-up sub-step timing + idempotent reuse plumbing

> closes #329 (sisyphus-side; business-repo Makefile parts are tracked in the
> issue body sections A/B and stay as follow-up PRs in the lab repo)

## 问题

当前 `make accept-env-up` 是一个不透明的黑盒：

```
20:13:01 create_accept.env_up_started
20:18:42 create_accept.done duration=341.2s
```

整 5-15 分钟一坨花在 (1) helm install lab、(2) helm install thanatos、(3) APK
build/download、(4) adb install。任何一步慢都只能盯人 `kubectl logs`、人肉断点定
位 —— Metabase 看板看不到子步分布，不知道"哪一步占了 80% 时间，下次该 cache 哪段"。

第二个问题：dogfood retry 时 lab + thanatos 的 helm release 配置基本没动，每次
`accept-env-down` 都把 namespace 整个删掉，下一轮 `accept-env-up` 从头重 install
再花 1-3 min。**没有"保留 lab/thanatos、只重装 APK"的 contract**。

## 方案（两件最小可行）

### A. accept-env-up 子步耗时 → stage_runs 持久化（observability）

**契约扩展**：`accept-env-up` 末行 endpoint JSON **可选** 加 `sub_steps` 字段：

```json
{
  "endpoint": "http://lab.accept-req-29.svc.cluster.local:8080",
  "namespace": "accept-req-29",
  "sub_steps": [
    {"name": "lab-helm",      "duration_sec": 45.2},
    {"name": "thanatos-helm", "duration_sec": 78.5},
    {"name": "redroid-boot",  "duration_sec": 92.1},
    {"name": "apk-install",   "duration_sec": 31.7}
  ]
}
```

**orchestrator 改动**：`create_accept.py` 解析 `sub_steps` 后，每条插一行
`stage_runs`（`stage = "accept-env-up.<name>"`、`outcome = "pass"`、`duration_sec`
直接来自 JSON、`started_at = now() - duration_sec`、`ended_at = now()`），不需要
schema 变更（`stage` 已是 TEXT）。Metabase 直接 `WHERE stage LIKE 'accept-env-up.%'`
聚合做子步分布看板。

`sub_steps` 缺失或非 list 时静默跳过 —— 业务仓没接也不退化，只是没有子步度量。

### B. accept-env-down KEEP_ENV 复用契约 + 配置开关

**契约扩展**：`accept-env-down` **SHALL** 识别 `KEEP_ENV=1` 环境变量；该 env 为
`1` 时 target 必须立即 `exit 0` 跳过任何 helm uninstall / kubectl delete，留 lab
+ thanatos 给下一轮复用。文档化进 `docs/integration-contracts.md` §2.3。

**orchestrator 改动**：新增 `settings.accept_keep_env: bool = False`。当
`teardown_accept_env.py` 跑时若该 flag 为 true 则注入 `KEEP_ENV=1` 到 env，
business Makefile 据此跳过卸载。默认关闭——dogfood 启用时人 set
`SISYPHUS_ACCEPT_KEEP_ENV=true` ConfigMap，rollout 即生效。

> `accept-apk-only` 仅重装 APK 复用 lab/thanatos release —— **本 REQ 不做**。
> 它要求"sisyphus 知道当前是不是 retry"+ 状态机分流，复杂度跟 #329 §C "拆 sub-stage"
> 等价，应另起 REQ。本 REQ 只把 KEEP_ENV 这一半的契约和配置打通，让 dogfood 用户
> 现在就能手动复用 lab。

## 影响

- 子步度量落进 `stage_runs`，Q 系列看板可加一条 `accept-env-up.<sub_step>` 时长
  分布，回答"哪步该上 cache"
- KEEP_ENV 打通后 dogfood retry 可省 1-3 min/次（dogfood 期间业务仓 lab/thanatos
  values 极少变）
- 业务仓（ttpos-arch-lab）侧不强制改 —— 不接 sub_steps 不影响，不接 KEEP_ENV 退化为
  原行为（每次拆装）。
- 不动状态机、不加 stage、不加 event。

## 不在范围

- `accept-apk-only` 仅重装 APK 的快路径（需要 retry 检测 + 状态机分流）
- sisyphus 主动判断"当前是 retry，自动 set KEEP_ENV=1"（先让用户手动开关，攒数据
  再决定要不要自动化）
- ttpos-arch-lab Makefile 实际 emit `sub_steps` / 实现 KEEP_ENV 跳过 —— 跨仓改动
  按现行约定走另一 REQ
