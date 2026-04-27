# Android (native) semantic contract（adb driver）

adb driver 用 `uiautomator dump` 抓 view tree。下面是产品方需要保证的最小
语义。

## 基线

| 项 | 要求 | 反例 |
|---|---|---|
| `android:contentDescription` | 任何无文字 / icon-only 按钮都有 `contentDescription` 描述用途 | `ImageButton` 不带 contentDescription |
| `importantForAccessibility="yes"` | 关键交互 view 显式开 `yes`（默认 auto 在某些 ROM 上会被跳过） | 默认 auto |
| 稳定 `resource-id` | 关键交互 view（按钮 / 输入框 / 列表项）有可读、稳定、不被 ProGuard 改的 resource-id | 用动态生成 id |
| 非交互区域 mark `no` | 装饰性 view 用 `importantForAccessibility="no"` 减少噪音 | 全开 yes |
| Toast / Snackbar | 提示文案能进 view tree（不是纯绘制层） | 自绘浮层无 contentDescription |

## preflight 阈值（M1 上线后）

- uiautomator dump 节点数 ≥ 5
- 至少一个 view 有非空 `text` 或 `content-desc`
- 关键交互按钮命中 `resource-id` 命名规范（业务仓 anchors.md 决定）

## 失败时 dev 应该怎么改

1. 在对应 layout xml / Compose 里加 `android:contentDescription` /
   `Modifier.semantics { contentDescription = "..." }`
2. 关键交互 view 加 `android:id="@+id/btn_submit"` 这样的稳定 id
3. ProGuard 配置保留 `resource-id` 命名（res-id 默认不被混淆，但确认下规则）
