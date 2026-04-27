# Thanatos —— Sisyphus 验收能力层

> AI-native acceptance layer。给 sisyphus accept stage 一套"读 spec → 操作真实环境 → 判 pass/fail → 沉淀产品知识"的能力。

## 1. 定位

**做什么**：accept-agent 通过 MCP 调 thanatos，自动跑 spec.md 里的 scenario block，输出结构化 pass/fail + 证据，并把每次跑出的产品知识写回业务仓 `.thanatos/`。

**不做什么**：
- 不是测试框架（不写断言 DSL，不跑 jest/pytest）
- 不是录制回放
- 不是通用浏览器 agent
- 不替 verifier-agent 判主观分类（业务 bug / spec 错 / env 起不来 / flaky 由 verifier 看证据自己判）
- 不做 LLM 适配层（不重复 mobilerun 干的事）

## 1b. 设计原则

1. **不抢 AI 决定权**（继承 sisyphus）—— pass/fail 是事实陈述，主观判断让 verifier-agent 做
2. **Semantic-first, runtime-identical** —— 验收的产物 = 用户拿到的产物。不搞 acceptance build / debug flavor / 测试钩子注入。唯一约束是产品代码该有的语义信息要有
3. **JIT instrumentation** —— 不预先全量改造产品，scenario 走到哪、覆盖到哪、语义 instrumentation 到哪。前期集中改核心流，长尾按需补
4. **截图是兜底，不是驱动** —— 语义层（a11y tree / view tree）是一等观察手段，截图只在语义观察失败时作为证据。不走"vision agent + 坐标点击"路线

## 2. 仓库布局

thanatos 代码在 sisyphus 仓内，与 orchestrator/runner 同级；产品知识在各业务仓 `.thanatos/`，无独立 KB 仓。

```
sisyphus/                                # 主代码仓
├── thanatos/                            # 新增模块
│   ├── src/thanatos/
│   │   ├── server.py                    # MCP server (stdio)
│   │   ├── runner.py                    # scenario 执行器
│   │   ├── skill.py                     # 加载 .thanatos/skill.yaml + 笔记
│   │   ├── scenario.py                  # 解析 spec.md (gherkin code-block + bullet 两种)
│   │   └── drivers/
│   │       ├── base.py                  # Driver Protocol
│   │       ├── playwright.py            # web
│   │       ├── adb.py                   # android via redroid
│   │       └── http.py                  # API
│   ├── docs/semantic-contracts/         # 每个 driver 的"产品方需要做的"清单
│   ├── Dockerfile                       # playwright + adb + python
│   └── pyproject.toml
├── orchestrator/src/orchestrator/prompts/
│   └── accept.md.j2                     # 改：调 thanatos.run_scenario，commit kb_updates
├── deploy/templates/
│   └── thanatos.yaml                    # per-REQ Deployment
└── docs/thanatos.md                     # 本文档

phona/<business-repo>/                   # 每个业务仓
├── src/...
├── openspec/changes/<REQ>/specs/.../spec.md
└── .thanatos/                           # 跟代码同进同退
    ├── skill.yaml                       # driver + entry + 登录 fixture
    ├── anchors.md                       # widget 语义名
    ├── flows.md                         # 已知流程
    └── pitfalls.md                      # 踩过的坑
```

## 3. 部署拓扑（driver-conditional sidecar）

业务仓 `accept-env-up` 起完后，per-REQ namespace 形态按 driver 不同：

| Driver | Pod 形态 | 容器 |
|---|---|---|
| `playwright` | 单容器 pod | `thanatos`（playwright 跑 chromium 进程内 subprocess） |
| `adb` | 双容器 pod | `redroid`（android-in-container, adb tcp:5555）+ `thanatos`（sidecar，连 localhost:5555） |
| `http` | 单容器 pod | `thanatos` |

```
namespace: req-<REQ_ID>            （accept-env-up 起完，accept-env-down 清完）
├── lab pod                        （integration repo helm chart 起的 backend stack）
└── env pod (driver-conditional)
    ├── [driver=adb]       redroid container + thanatos container (sidecar)
    └── [driver=playwright|http]  thanatos container 单独

sisyphus runner pod                 （只读 checker）
└── make accept-env-up / down       不调 thanatos

BKD Coder workspace                 （写权限那侧，gh auth）
└── accept-agent
    ├── kubectl exec thanatos-pod -- thanatos-mcp-server   ← stdio MCP
    ├── 拿 results → 报 BKD issue（既有契约）
    └── 拿 kb_updates → 写入 source repo checkout → commit + push 到 feature 分支
```

**为啥按 driver 拆**：mobile (adb) 模式 thanatos 跟 redroid 1:1 同生死，sidecar 共享 localhost 省掉 service 发现 + RBAC + 一个 pod；playwright 默认就在 driver 进程里 spawn chromium，根本没 peer container 可 sidecar；http 单容器最简。

约束：
- thanatos pod 只读连接外部环境（lab/redroid）
- thanatos pod 不做 GH 写（坚守 sisyphus "K8s 内只读"原则）
- 所有 GH 写发生在 Coder workspace

## 4. MCP 接口（最小集）

```python
thanatos.run_scenario(
  skill_path: str,          # 业务仓 .thanatos/ 在 runner pod 里的绝对路径
  spec_path: str,           # spec.md 绝对路径
  scenario_id: str,         # "REQ-1004-S1" / "Desktop collapse/expand" / spec 作者自定义
  endpoint: str,            # accept-env-up 吐的
) -> {
  scenario_id: str,
  pass: bool,
  steps: [{step, ok, evidence: {dom?, network?, screenshot?}}],
  kb_updates: [             # ← agent 必须 commit 到 feature 分支
    {path: ".thanatos/anchors.md", action: "patch"|"append", content: str}
  ],
  failure_hint: str | null  # 给 verifier 参考，不强加分类
}

thanatos.run_all(skill_path, spec_path, endpoint) -> list[run_scenario result]

thanatos.recall(skill_path, intent: str) -> [{kind, snippet, freshness}]
```

第一版只暴露 scenario 粒度，不暴露 observe/act/assert 原语。等真有需要 agent 介入控制再加。

## 4b. Driver 三层观察策略

每个 driver 必须实现"语义观察 → 截图"两段降级：

| Driver | 语义层（一等） | 失败 → 截图（二等） |
|---|---|---|
| playwright | `page.accessibility.snapshot()` | `page.screenshot()` |
| adb | `uiautomator dump` → XML view tree | `adb exec-out screencap -p` |
| http | response body + headers | n/a |

**触发降级**（命中任一）：
- 语义观察返回空 / 节点 < 5 / 超时
- 按 anchor 定位失败（role+name / resource-id+text 都没 match）
- 动作发出后预期变更没出现（点了按钮但视图树没变）

截图只是 evidence，不用截图驱动动作。坐标兜底默认关闭。

## 4c. 产品语义契约（最小集）

每个 driver 配一份"产品需要满足的最小语义"清单（`thanatos/docs/semantic-contracts/`）：

| Driver | 产品方要做 |
|---|---|
| playwright (web) | 用 HTML 语义元素；icon-only 按钮加 `aria-label`；form 字段绑 `<label>`；heading 层级 |
| adb (android native) | `android:contentDescription`；`importantForAccessibility="yes"`；resource-id 不混淆 |
| adb (flutter) | 关键交互 widget 加 `Semantics(label, button:true,...)` 或 `semanticsLabel` |
| http (API) | 无 |

执行机制：每个 scenario 跑前 driver 跑 preflight（最简版："a11y/view tree 节点 ≥ N"），不达标直接 fail，failure_hint 指向对应契约文档。preflight 失败由 verifier escalate，dev 起小 PR 加 Semantics。

**不**做：业务仓 GHA lint 强制全量 instrumentation（违反 JIT 原则）。

## 5. Skill 格式

`<business-repo>/.thanatos/skill.yaml`：

```yaml
name: pytoya-web
driver: playwright              # playwright | adb | http
entry: $ENDPOINT                # accept-env-up 吐的 endpoint，thanatos 注入
fixtures:
  admin_login:
    user: admin
    pass: admin
preflight:                      # 可选，第一版用默认
  - assert: "a11y_node_count > 5"
```

`anchors.md` / `flows.md` / `pitfalls.md`：自由 markdown，agent 读 + thanatos 写，无 schema 约束。

## 6. Scenario 来源（thanatos 必须吃两种格式）

**A. Gherkin code-block（API/后端 spec）**

````markdown
#### Scenario: REQ-1004-S1 — desc
```gherkin
Given ...
When ...
Then ...
```
````

**B. Markdown bullet（UI spec）**

```markdown
#### Scenario: Desktop collapse/expand
- **GIVEN** ...
- **WHEN** ...
- **THEN** ...
```

`scenario.py` 兼容两种，输出统一的 `{scenario_id, given[], when[], then[]}` 给 driver 用。

## 7. 数据流（per-REQ accept 一次）

```
sisyphus engine 进 accept stage
  ↓ make accept-env-up                        起 lab + (redroid?) + thanatos
sisyphus 派 accept-agent
  ↓
accept-agent (Coder)
  ↓ 1. clone source repo（含 .thanatos/）
  ↓ 2. 读 spec.md 找所有 #### Scenario:
  ↓ 3. for scenario:
        kubectl exec thanatos-pod -- thanatos run_scenario ...
        ↓ thanatos: load skill → pick driver → preflight → drive 环境
        ↓ thanatos: 跑 GIVEN/WHEN/THEN，低置信截图
        ↓ thanatos: 返回 {pass, evidence, kb_updates}
  ↓ 4. 汇总 results → BKD follow-up "Accept Result"
  ↓ 5. 把所有 kb_updates 应用到 source repo working tree
  ↓ 6. git add .thanatos/ && git commit && git push origin feat/REQ-x
  ↓ 7. tags=[accept,REQ,result:pass|fail], statusId=review
sisyphus engine
  ↓ make accept-env-down                       清 namespace
  ↓ archive 或 REVIEW_RUNNING
```

人参与点：只看 BKD follow-up + feature PR diff。无第二个审 KB 的 PR。

## 8. KB 写入策略

只一种模式：thanatos 算 delta，agent 顺手 commit 到当前 feature 分支。

- 无 propose/auto-refresh 二分
- 无 PR review gate
- 无 sisyphus checker 卡未提交

漂移自愈：KB 没更新 → 下次 recall miss → thanatos 重新探索 → 重新吐 update。坏不了大事。

## 9. Sisyphus 改动点

### 9a. 责任三分（accept-env-up 拆 helm install）

| 层 | 谁的 | 内容 |
|---|---|---|
| Lab stack（被验产品本体） | integration 仓（如 ttpos-arch-lab） | helm chart：业务自己的 backend / DB / cache / mq |
| Acceptance harness（验收工具人） | sisyphus 仓 | helm chart：thanatos 容器（+ adb 模式时 redroid sidecar） |
| Glue / 入口契约 | 业务仓（如 ttpos-flutter） | `make accept-env-up` / `down` —— 顺序 `helm install lab` + `helm install thanatos`；选 driver；产 endpoint |

owner 边界跟 review 边界对齐：sisyphus team review thanatos chart，lab team review lab chart，业务仓只 review 自己的 values.yaml + Makefile。两 chart 不做 sub-chart 关系，各发各的版本号，业务仓自己钉。

### 9b. 文件改动表

| 文件 | 改动 |
|---|---|
| `sisyphus/thanatos/` | 新建模块（代码） |
| `sisyphus/thanatos/docs/semantic-contracts/` | 三份契约清单（web / android / flutter） |
| `sisyphus/deploy/templates/thanatos.yaml` | per-REQ Deployment |
| `orchestrator/src/orchestrator/prompts/accept.md.j2` | 调 thanatos MCP，commit kb_updates |
| `docs/integration-contracts.md` | 加 `.thanatos/` 目录约定 |
| `docs/cookbook/ttpos-arch-lab-accept-env.md` | accept-env-up 多起一个 thanatos |
| `docs/thanatos.md` | 本文档 |

无新机械 checker、无新 verifier 模板、无新 stage、无新 state。

## 10. v1 不做（明确砍掉）

- 跨 product 共性 pitfall 抽取
- KB schema 校验 / lint
- 自动主动 explore（产品大遍历）
- failure_class 自动分类
- 视觉 baseline / phash diff
- iOS / desktop driver
- 通用 LLM 适配层
- 业务仓 GHA "thanatos lint" 强制全量 a11y instrumentation
- 暴露 observe/act/assert 细颗粒原语

## 11. v1 happy path

REQ-XXXX（pytoya web 加新表单字段）：

1. dev 推 `feat/REQ-XXXX`，含 code + spec.md（3 个 `#### Scenario`）
2. sisyphus 跑 staging-test、pr-ci-watch，过
3. accept stage：
   - accept-env-up 起 pytoya lab + thanatos pod
   - accept-agent 派下来
   - thanatos.run_all 跑 3 个 scenario，2 pass 1 fail（按钮文案对不上）
   - 失败 scenario evidence 含截图 + 网络日志
   - kb_updates: anchors.md 修正按钮真实文案 + pitfalls.md 加表单字段顺序坑
4. accept-agent commit kb_updates 推到 feat/REQ-XXXX，BKD 标 result:fail
5. verifier-agent 看 evidence 判 fix（fixer=dev 改文案 / fixer=spec 改 spec）
6. fix 后下一轮 accept，3 个全 pass，archive

## 12. 决策记录

| # | 问题 | 决定 |
|---|---|---|
| 1 | thanatos 代码住哪 | sisyphus 仓内 `thanatos/` 模块 |
| 2 | KB 住哪 | 各业务仓 `.thanatos/`，跟代码同 PR |
| 3 | thanatos pod 镜像 | 独立（playwright + adb 体积大） |
| 4 | MCP transport | stdio over `kubectl exec`（白嫖 K8s 鉴权） |
| 5 | thanatos pod 生命周期 | per-REQ，跟 lab/redroid 同生死 |
| 6 | 一个 pod 多 driver | 三合一（playwright + adb + http） |
| 7 | MCP 暴露粒度 | scenario 级（v1） |
| 8 | 失败截图存哪 | sisyphus runner PVC，URL 写进 evidence |
| 9 | flutter 无 Semantics | preflight 卡死，要求产品方先开 Semantics |
| 10 | 是否加业务仓 a11y lint | 不加。preflight 驱动 JIT 即可 |
| 11 | mobilerun 定位 | 退役，能力并入 thanatos |
| 12 | 第一个落地 product | pytoya-web（UI scenario 多） |
