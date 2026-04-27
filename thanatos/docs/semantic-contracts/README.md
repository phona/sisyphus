# Thanatos semantic contracts

每个 driver 跑 scenario 前都做 **preflight**：抽业务前端 / API 的语义层快照，
节点数 / 关键签名不到阈值就直接 fail —— `failure_hint` 指向对应 driver 的契约
（这个目录下的子文档），让 verifier-agent 转发给 dev 起小 PR 加 Semantics。

## 设计原则（来自 docs/thanatos.md §1b §4c）

1. **JIT instrumentation** —— 不预先全量改造产品；scenario 触及的节点必须有，
   长尾按需补。
2. **不卡 GHA** —— 不在业务仓 GitHub Actions 加 "thanatos lint" 强制全量
   a11y。preflight 失败的反馈环（accept fail → verifier escalate → dev PR）
   就够了。
3. **Semantic-first，截图兜底** —— 三个 driver 都先抓语义层（a11y tree / view
   tree / response body），抓不到才退到截图，截图只当 evidence。

## 文档

- [`web.md`](./web.md) —— playwright driver
- [`android.md`](./android.md) —— adb driver（android native）
- [`flutter.md`](./flutter.md) —— adb driver（flutter android）

API driver（http）没有产品方契约（response body 自然就是语义层）。

## M0 ≠ 强制项

M0 只交付 driver Protocol 骨架 + 这些契约 markdown。`preflight` 真实判断节点
数会在 M1 接 driver 时才生效。这些文档现在先入仓，让 dev 提前知道 M1+ 上线后
产品代码的 baseline 是什么。
