# Web semantic contract（playwright driver）

playwright driver 用 `page.accessibility.snapshot()` 抓 a11y tree。下面是产
品方需要保证的最小语义。

## 基线

| 项 | 要求 | 反例 |
|---|---|---|
| 用 HTML 语义元素 | `<button>` / `<a>` / `<nav>` / `<main>` / `<form>` 而不是裸 `<div onclick>` | `<div class="btn-primary">提交</div>` 不算 button |
| icon-only 按钮加 `aria-label` | 任何只有 icon 的可点击元素都有 `aria-label` 描述用途 | `<button><i class="trash"/></button>` 缺 `aria-label="删除"` |
| form 字段绑 `<label>` | input/select 都跟一个 `<label for>` 关联，或被 `<label>` 包住，或带 `aria-label` | 裸 `<input placeholder="邮箱">` 不达标 |
| heading 层级 | 页面顶级使用 h1/h2/h3 形成层级，不跳级 | 全用 `<div class="title-lg">` |
| 表格用 `<table>` | 数据表用真 table + `<th scope>` | 用 `<div class="row">` 拼出表格 |
| modal / popover | 用 `role="dialog"` + `aria-labelledby` | div 浮层无 role |

## preflight 阈值（M1 上线后）

- a11y snapshot 节点数 ≥ 5（页面加载到首屏后）
- 至少一个 `role: heading` 出现
- 表单页：每个 input 都有可关联 label

## 失败时 dev 应该怎么改

1. 找 `failure_hint` 提到的节点（路径或 selector）
2. 把对应组件加 `aria-label` / `<label>` / 换语义元素
3. 起 1-2 个文件级别小 PR，跟主 feature PR 平行 review
