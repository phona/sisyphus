# Sisyphus 架构（v0.2 + M14 + M15）

> **AI-native CI 编排层**：薄薄一层调度 + 机械 checker + 度量，让 agent 干完整链路活。
>
> **不抢 AI 决定权**。内容质量、bug 该不该修、怎么改 —— 永远是 agent 的事。
> sisyphus 跑硬指标 / 路由 / 兜底 / 度量。

## 1. 哲学

| 原则 | 含义 | 体现在哪 |
|---|---|---|
| **薄编排，agent 决定** | 路由 / 状态机 / checker 是 sisyphus；判 PR 内容好不好、bug 该不该修是 agent | router.py 只翻译 webhook 不判内容；verifier-agent 主观决策 |
| **机械层 ≠ agent 层** | 跑测试 / 轮 GHA 不绕 agent，sisyphus 自己干 | M1 staging-test / M2 pr-ci-watch 都是 sisyphus checker |
| **失败先验，再试错** | stage fail 不直接 bugfix，先让 verifier-agent 看一眼是 fix / retry / escalate | M14b/c verifier 框架 |
| **指标驱动改进** | 每条决策入表，看板回答"哪条 prompt 该改" | stage_runs / verifier_decisions / 13 张 Metabase 卡 |
| **生产用最强模型** | 无"失败升级模型"自适应；haiku 只用于测试加速 | config.py 单模型字段 |
| **不要新 IDL** | M15 砍 manifest.yaml：业务 repo 描述走已有原语（Makefile target + git branch + BKD tag），sisyphus 不维护集中式 schema | docs/integration-contracts.md |

### 跟相邻系统的层级

```
┌────────────────────────────────────────────────────────┐
│  研发组织层： sisyphus (本仓库)                        │
│    - 串 analyze → spec-lint → dev-cross-check          │
│      → staging-test → pr-ci → accept → archive         │
│    - verifier-agent 主观决策替代固定 fail 分流          │
│    - watchdog / GC / 指标采集                           │
├────────────────────────────────────────────────────────┤
│  脚本 CI 层： GitHub Actions（互补不替代）              │
│    - lint / unit / integration / sonar / image-publish  │
│    - sisyphus pr-ci-watch checker 直接轮它的 check-runs │
├────────────────────────────────────────────────────────┤
│  agent 工具层： pure Claude Code skill / agent prompt   │
│    - IDE 内 turbo dev tool                              │
│    - 跟 sisyphus 不同层；sisyphus 在外面组织调度        │
└────────────────────────────────────────────────────────┘
```

## 2. 主流水线

happy path 九段，从 `intent:analyze` tag 一路自动到 `done`。

```mermaid
flowchart TD
    Human[人在 BKD 打<br/>intent:analyze tag]
    Analyze[analyze-agent<br/>写 proposal/design/tasks<br/>+ 决定多少 dev agent]
    SpecLint[spec-lint checker<br/>openspec validate<br/>+ check-scenario-refs.sh]
    DevCheck[dev-cross-check checker<br/>业务 repo 侧定制检查<br/>例:编译 / 开发框架检查]
    Staging[staging-test checker<br/>kubectl exec runner<br/>cd source/leader && make ci-test]
    PRCI[pr-ci-watch checker<br/>GitHub REST 轮 check-runs<br/>按 feat/REQ-x 查 PR]
    EnvUp[sisyphus pre-accept:<br/>make accept-up]
    Accept[accept-agent<br/>跑 FEATURE-A* scenarios]
    Teardown[teardown_accept_env<br/>make accept-down<br/>幂等必跑]
    Archive[done_archive<br/>合 PR + 关 issue]
    Done([done])

    Human --> Analyze --> SpecLint --> DevCheck --> Staging --> PRCI --> EnvUp --> Accept --> Teardown --> Archive --> Done

    classDef agent fill:#e1f5ff,stroke:#0288d1
    classDef checker fill:#fff3e0,stroke:#f57c00
    classDef terminal fill:#e8f5e9,stroke:#388e3c

    class Analyze,Accept agent
    class SpecLint,DevCheck,Staging,PRCI checker
    class Done terminal
```

## 3. 失败与迭代场景（verifier 子链）

任何 stage（含 staging-test、pr-ci、accept）失败 **不直接 bugfix**，先入 `REVIEW_RUNNING` 让 verifier-agent 主观判：

```mermaid
flowchart TD
    Stage[任意 stage_RUNNING]
    StageFail{stage 结果}
    Verifier[REVIEW_RUNNING<br/>verifier-agent 跑<br/>verifier/{stage}_{trigger}.md.j2]
    Decision{decision JSON<br/>action ?}
    NextStage[下一 stage]
    Fixer[FIXER_RUNNING<br/>start_fixer 起<br/>dev / spec fixer]
    Reverify[invoke_verifier_after_fix<br/>回 REVIEW_RUNNING]
    Retry[apply_verify_retry_checker<br/>回 stage_RUNNING<br/>重跑机械 checker]
    Escalated([ESCALATED])

    Stage --> StageFail
    StageFail -->|pass| Verifier
    StageFail -->|fail| Verifier
    Verifier --> Decision
    Decision -->|pass| NextStage
    Decision -->|fix + fixer={dev,spec}| Fixer --> Reverify --> Verifier
    Decision -->|retry_checker| Retry --> Stage
    Decision -->|escalate / schema invalid| Escalated

    classDef verifier fill:#f3e5f5,stroke:#7b1fa2
    classDef terminal fill:#ffebee,stroke:#c62828
    class Verifier,Reverify,Decision verifier
    class Escalated terminal
```

**为什么 success 也走 verifier**：M14b 让 verifier-agent 也对"机械 pass"做最后一道主观判（避免假阳性 / 偷工减料）。`trigger=success` 跟 `trigger=fail` 复用同一框架，prompt 模板分别在 `prompts/verifier/{stage}_success.md.j2` 和 `_fail.md.j2`。

**verifier decision 协议**（router.py:33 `validate_decision`）：

```json
{
  "action": "pass | fix | retry_checker | escalate",
  "fixer": "dev | spec | null",
  "confidence": "high | low",
  "reason": "..."
}
```

注：`action=fix` 时 `fixer` 必须非 null；其他 action `fixer` 必须 null。

decision 写在 BKD verifier issue 的：
1. `decision:<urlsafe-base64-json>` tag（首选，机器写最稳）
2. issue description 里的 ```` ```json ```` 块（兜底）

schema 不合规 → `VERIFY_ESCALATE` → 终态 ESCALATED。

## 4. 三类决定者职责

| | 决定什么 | 谁做 | 怎么做 |
|---|---|---|---|
| **机械事实** | 测试是否退 0、CI 是否绿、env 是否起来 | sisyphus checker | exec / REST |
| **主观判断** | 这次 fail 是 spec 错 / 代码错 / flaky / 该 escalate | verifier-agent | LLM + decision JSON |
| **写代码** | 实现 / 修 bug / 改 spec | stage agent + fixer agent | Claude Agent in BKD issue |

```mermaid
flowchart LR
    subgraph sisyphus["sisyphus 编排层 (Python)"]
        Router[router.py<br/>tag → Event]
        SM[state.py<br/>状态机]
        Engine[engine.py<br/>action 调度<br/>+ stage_runs 落点]
        Watchdog[watchdog.py<br/>卡死兜底]
        GC[runner_gc.py<br/>资源回收]
    end

    subgraph mechanical["机械层 checker (Python in sisyphus)"]
        Staging[staging_test<br/>kubectl exec 跑 make ci-test]
        PRCI[pr_ci_watch<br/>GitHub REST]
    end

    subgraph subjective["主观层 (BKD agent)"]
        VerifierA[verifier-agent<br/>12 个 prompt 模板<br/>输出 decision JSON]
    end

    subgraph stages["stage / fixer agent (BKD agent)"]
        Analyze[analyze]
        Spec[spec (1~N 并行)]
        Dev[dev (1~N 并行)]
        Accept[accept]
        Fixer[fixer:dev<br/>fixer:spec]
        Archive[done-archive]
    end

    subgraph metrics["指标层 (Postgres + Metabase)"]
        EventLog[event_log]
        StageRuns[stage_runs<br/>M14e]
        VDecision[verifier_decisions<br/>M14e]
        Dashboards[13 张 Metabase 看板]
    end

    sisyphus --> mechanical
    sisyphus --> subjective
    sisyphus --> stages
    sisyphus --> metrics
    mechanical -.写结果.-> sisyphus
    subjective -.decision JSON.-> sisyphus
    stages -.session.completed.-> sisyphus
    metrics --> Dashboards
```

## 5. 角色分工详表

| 角色 | 职责 | 实现 | LOCKED 边界 |
|---|---|---|---|
| **sisyphus orchestrator** | 状态机 + 路由 + watchdog + GC + 指标采集 | Python, K8s Deployment | 不写业务代码、不审 PR 内容 |
| **机械 checker** | 跑测试 / 轮 CI / 跑 accept-up/down | Python, runner pod 内 exec | 只看 exit code / API 返回 |
| **analyze-agent** | 写 `proposal.md` / `design.md` / `tasks.md`（在 leader source repo 的 `openspec/changes/REQ-x/` 下）+ **默认激进拆** spec / dev 子 issue 压 wall-clock | BKD agent + analyze.md.j2 | 不写业务代码 |
| **spec-agent (1~N)** | 写 `contract-tests` + `acceptance-tests` 两块 spec 文档（默认 1 个 agent 一次写完；需要时 analyze-agent 可开多个并行） | BKD agent + spec.md.j2 | 不写业务代码、不改测试产物之外文件 |
| **dev-agent (1~N)** | 实现业务代码 + push `feat/REQ-x` + **真开 PR** | BKD agent + dev.md.j2 | 测试 LOCKED 不可改 |
| **verifier-agent** | 主观判 stage 是否真过（pass / fix / retry / escalate） | BKD agent + verifier/{stage}_{trigger}.md.j2 | 不写代码，只输出 decision JSON |
| **fixer-agent** | 改一类东西：dev fixer 改业务码、spec fixer 改 spec | BKD agent + bugfix.md.j2（过渡） | scope 由 verifier 指定 |
| **accept-agent** | 跑 FEATURE-A* scenarios，写 result:pass/fail tag | BKD agent + accept.md.j2 | 不改业务代码 |
| **done-archive agent** | 合 PR + 关 issue | BKD agent + done_archive.md.j2 | — |

## 6. Stage 与产物

| # | Stage | 触发 | 产物 / 副作用 | 推进信号 |
|---|---|---|---|---|
| 1 | **analyze** | `intent:analyze` tag | `openspec/changes/REQ-x/{proposal,design,tasks}.md` 在 leader source repo | session.completed + analyze tag |
| 2 | **spec-lint** (机械) | analyze done | `openspec validate openspec/changes/REQ-x` + `check-scenario-refs.sh` —— 验证 spec 完整性和场景引用 | sisyphus 自己判，无 BKD agent |
| 3 | **dev-cross-check** (机械) | spec-lint pass | 业务 repo 自定义检查（例：编译、框架约束）；runner pod 执行 | sisyphus 自己判，无 BKD agent |
| 4 | **dev (1~N 并行)** | dev-cross-check pass | 业务代码 + push `feat/REQ-x` + 开 PR；按 tasks.md 拆任务可起 N 个 dev agent | 每个 dev session.completed → mark_dev_reviewed_and_check 聚合 → DEV_ALL_PASSED |
| 5 | **staging-test** (机械) | DEV_ALL_PASSED | `cd /workspace/source/<leader> && make ci-test` 退码 0 / 1 | sisyphus 自己判，无 BKD agent |
| 6 | **pr-ci-watch** (机械) | staging-test pass | GitHub REST 轮 PR check-runs（按 `feat/REQ-x` branch 查 PR）直至全绿 / 任一红 / 1800s 超时 | sisyphus 自己判 |
| 7a | **accept env-up** (机械) | pr-ci pass | runner pod 跑 `make accept-up`，stdout 尾行 JSON 取 `endpoint` | env-up 失败 → ESCALATED |
| 7b | **accept** | env-up 完 | 跑 FEATURE-A* scenarios → result:pass / fail tag | session.completed + accept tag |
| 8 | **teardown** (机械, 必跑) | accept 完（pass 或 fail） | `make accept-down`，best-effort 失败只 warning | TEARDOWN_DONE_PASS / FAIL |
| 9 | **archive** | teardown_done_pass | 合 PR + 关 issue | ARCHIVE_DONE → DONE |

完整状态转移见 [state-machine.md](./state-machine.md)。

## 7. Stage 间数据流：靠 git branch + BKD tag + Makefile，不靠 IDL

M15 起 sisyphus 不再维护 `manifest.yaml` 这种集中式 IDL。stage 间靠下面几个原语传递信息：

| 原语 | 谁写 | 谁读 | 内容 |
|---|---|---|---|
| **BKD intent issue description** | 人 / analyze-agent | analyze / spec / dev | 涉及哪些 source / integration repo、leader 是哪个 |
| **`openspec/changes/REQ-x/tasks.md`** | analyze | sisyphus fanout_dev、dev | 拆几个并行 dev 任务、每个任务 scope |
| **git branch `feat/REQ-x`** | dev | pr-ci-watch、accept | 业务代码所在；pr-ci 按 branch 找 PR |
| **`make ci-test` target** | 业务 repo（一次性接入时定） | staging-test checker | 跑啥测试由业务自己聚合 |
| **`make accept-up` / `accept-down`** | integration repo | sisyphus accept stage | 起 / 拆 lab |
| **BKD issue tags** | 各 agent | router.py | stage 完成信号、result:pass/fail、verifier decision |

详见 [integration-contracts.md](./integration-contracts.md)。

## 7b. Objective Checker 框架（M15）

**spec-lint**（analyze 完 → dev-cross-check 前）

目的：规范 spec 的完整性和引用一致性。

```bash
openspec validate openspec/changes/{REQ}   # 检查 spec 文件格式和结构
check-scenario-refs.sh {leader_repo_root}  # 检查 task.md / reports 引用的场景都在 specs 中定义
```

- 两个检查都通过才算 pass
- 失败 → verifier-agent 决策
- 实现：`checkers/spec_lint.py`

**dev-cross-check**（spec-lint 完 → staging-test 前）

目的：业务 repo 定制检查（编译、框架约束等）。

- 由业务 repo 集成脚本（runner pod 内执行）
- 退码 0 = pass，非 0 = fail
- 失败 → verifier-agent 决策

## 8. Runner（K8s Pod + PVC，per-REQ）

每个 REQ 在 `sisyphus-runners` namespace 起一个：
- **Pod** `runner-<REQ>` —— privileged + DinD + fuse-overlayfs
- **PVC** `workspace-<REQ>` —— 挂 `/workspace`，存 clone 的 repos + 中间产物

生命周期由 `k8s_runner.py` 管：

```mermaid
stateDiagram-v2
    [*] --> Created: ensure_runner (analyze 起)
    Created --> Running: K8s 调度 + entrypoint.sh
    Running --> Restarting: Pod crash (restartPolicy=Always)
    Restarting --> Running: 自动拉起 (PVC 不变)
    Running --> Paused: pause (delete Pod, 留 PVC)
    Paused --> Running: resume (重建 Pod)
    Running --> Cleaned: M10 即时 cleanup\n(state ∈ {done})
    Running --> Retained: state=escalated\n(PVC 留 N 天给人查)
    Retained --> Cleaned: runner_gc 超过 retention
    Cleaned --> [*]
```

镜像两种：
- `runner/Dockerfile` —— Flutter 全家桶（~5GB），跑 ttpos-flutter
- `runner/go.Dockerfile` —— 精简 Go 镜像（~1GB）

镜像内 `/opt/sisyphus/scripts/` 挂着合约脚本（M15 objective checkers 用）：
- `check-scenario-refs.sh` —— spec-lint checker 用，验证 spec 中引用的场景名必须在 specs 中定义
- `check-tasks-section-ownership.sh` —— 用于 dev-cross-check 或业务定制检查
- `pre-commit-acl.sh` —— 用于 dev-cross-check 或业务定制检查
- `validate-manifest.py` —— 用于 dev-cross-check 或业务定制检查

orchestrator 注入 env：

| env | 何时注入 | 用途 |
|---|---|---|
| `SISYPHUS_REQ_ID` | 所有 stage | 业务 Makefile 拼 namespace / 标签 |
| `SISYPHUS_NAMESPACE=accept-<req-id>` | accept 阶段 | `helm install -n $SISYPHUS_NAMESPACE` |
| `SISYPHUS_STAGE` | accept-up / accept-down | 给业务 Makefile 区分阶段 |
| `SISYPHUS_RUNNER=1` | 镜像内置 | 让脚本判断"在 sisyphus runner 里" |

详见 [integration-contracts.md](./integration-contracts.md)。

## 9. BKD 客户端（REST 默认）

PR #1 起 sisyphus 调 BKD 走 REST（BKD ≥ 0.0.65 已废 `/api/mcp`）：

- **transport**: `bkd_transport=rest`（默认）/ `mcp`（兜底）
- **入口**: `BKDClient(base_url, token)` factory（`orchestrator/src/orchestrator/bkd.py`）
- **方法**: `create_issue` / `follow_up_issue` / `update_issue` / `get_issue` / `list_issues`
- **PR #18 起 `create_issue` 默认 `useWorktree=True`** —— 强制 agent 隔离 working tree，并行多 agent 不互抢

webhook 反向：BKD `session.completed` / `session.failed` / `issue.updated` → orchestrator `webhook.py` → router 翻译 → engine.step 推状态机。

## 10. 观测系统

```
┌─────────────────────────┐
│ orchestrator 写表       │
│  - event_log (kind)     │   ← 任何决策、check 结果都写
│  - stage_runs (M14e)    │   ← stage 起止 / agent / token / model（M15 接入 caller）
│  - verifier_decisions   │   ← 每条 verifier JSON + 后续 actual_outcome
│  - bkd_snapshot         │   ← BKD issue 状态镜像（5 min sync）
└─────────────────────────┘
            │
            ▼ Postgres (sisyphus 库)
            │
┌─────────────────────────┐
│ Metabase                │
│  Q1-Q5  (M7)  artifact_checks 钻牛角尖、慢异常、通过率、失败分桶 │
│  Q6-Q13 (M14e) duration P95 / verifier 准确率 / fixer 命中率   │
│           / token 成本 / 并行加速比 / bugfix loop 异常        │
│           / watchdog escalate 频率                            │
└─────────────────────────┘
```

详细：[observability.md](./observability.md) + [observability/sisyphus-dashboard.md](../observability/sisyphus-dashboard.md)。

**核心准则**：观测不是"看好看的图"，是**让每次改 prompt / 阈值能用数据验证效果**。`config_version` + `improvement_log` 两张表锁住"改动 → 度量"循环。

## 11. 兜底机制

| 机制 | 触发 | 行为 | 文件 |
|---|---|---|---|
| **verifier-agent** | 任意 stage 完成 | LLM 主观判 pass/fix/retry/escalate；无效 JSON → escalate | actions/_verifier.py |
| **watchdog** (M8) | 后台轮询 | REQ 卡 in-flight 超 N 秒 + BKD session 不在跑 → SESSION_FAILED → ESCALATED | watchdog.py |
| **runner GC** (M10) | 后台轮询 | done 立删；escalated 留 N 天再删；孤儿 runner 也删 | runner_gc.py |
| **CAS state transition** | 每条 transition | Postgres 行级 CAS 防并发抢同 REQ | store/req_state.py |
| **idempotent action** | webhook 重试 | 大部分 action 标 `idempotent=True`；create_* 例外 | actions/__init__.py |

## 12. 已知约束 / 不支持

- **跨 repo 协调**：当前一个 REQ 主改一个 leader source repo，integration 用 single repo 块。多 leader 暂不支持。
- **回归归档**：accept 通过的 spec 不会自动并入更大 regression suite，需要新 REQ 显式补。
- **Hotfix 入口**：紧急修复目前还是走完整流水线 + skip flag，没有专门的 hotfix mode。
- **Token 成本告警**：Q10 已出图但未自动告警。
- **真正的 root-cause fixer prompt**：当前 fixer 复用 `bugfix.md.j2` 过渡，后续 PR 才会做 dev/spec 两类专用 prompt。

## 13. 演进路线（in-flight）

- **真正的 dev 并行 fanout** —— M14d 框架已有，M15 退化成单 dev；下一步按 tasks.md 真拆多 dev agent
- **专用 fixer prompts** —— `verifier-fix-dev.md.j2` / `verifier-fix-spec.md.j2`
- **接 ttpos-arch-lab 真 e2e** —— accept-up / accept-down 落到生产 lab
- **Token 自动告警** —— Q10 + 阈值 → notification
