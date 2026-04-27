# Flutter semantic contract（adb driver, flutter android target）

flutter 默认产 platform view 不直接进 uiautomator view tree —— 必须显式
`Semantics(...)` 或 `semanticsLabel` 才能被 adb driver 看到。

## 基线

| 项 | 要求 | 反例 |
|---|---|---|
| `Semantics(label, button: true, ...)` | 关键交互 widget（按钮、卡片、tap 区）都有 `Semantics` 包裹 | 裸 `GestureDetector` 没 Semantics |
| `semanticsLabel:` 简写 | `Image(semanticsLabel: "头像")` / `Text` 自带 semanticsLabel | icon Widget 不传 label |
| `MergeSemantics` | 一个父节点描述多 child 时用 `MergeSemantics` 防止 tree 爆炸 | 每个 icon + label 都自成 node |
| `excludeSemantics` | 装饰元素 mark `excludeSemantics: true` 减噪 | 装饰图也进 tree |
| 表单字段 | `TextField` 带 `decoration: InputDecoration(labelText: ...)` | 占位文字不算 label |

## preflight 阈值（M1 上线后）

- uiautomator dump 节点数 ≥ 5（注意 flutter app 默认可能只有一个 root view）
- 至少一个 view 有非空 `content-desc`（说明有 Semantics 进了 a11y tree）

## 失败时 dev 应该怎么改

1. 找到对应 widget tree 节点
2. 包一层 `Semantics(label: "…", button: true, child: …)` 或在 `IconButton` /
   `Image` 直接传 `semanticsLabel`
3. flutter widget inspector 跑一遍，确认对应 a11y label 显示
4. 同 PR commit 进业务仓，sisyphus 下一轮 accept 重跑
