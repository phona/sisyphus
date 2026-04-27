# 用户反馈回路设计（accept → review → fix → done）

> 把"sisyphus 跑完 accept stage 之后"到"DONE"之间补一段事件驱动的用户验收 loop。
> 当前 sisyphus accept 完直接 archive→DONE，**用户没机会判 PR 满不满足要求**。
> 这一节定义补完后的状态、事件、契约。
>
> 背景讨论：2026-04-27 与 user 对齐。
> 状态机权威是 [state-machine.md](state-machine.md) + [orchestrator/src/orchestrator/state.py](../orchestrator/src/orchestrator/state.py)。
> 本文档 = 设计 spec，落地走 dogfood REQ-pr-review-feedback-loop / REQ-watchdog-stage-policy / REQ-user-interrupt-resume。

## 0. 当前缺口

| 维度 | 当前 | 缺啥 |
|---|---|---|
| 用户 PR review 反馈进 sisyphus | 0 通道 | 没 GH `pull_request_review` webhook handler |
| accept 完到 done 之间的"等用户" | 没（直接 archive） | 没 `PENDING_USER_PR_REVIEW` state |
| 用户主动 interrupt 跑中 REQ | 只能 admin /escalate（粗暴 kill） | 没"用户友好"interrupt（GH PR close / BKD issue close 应能触发） |
| 用户主动 resume escalated | 只能 admin /resume curl | 没"评论触发 resume" |
| watchdog 对 intake-loop 类 stage 的容忍 | 5min 一刀切（intake 也被杀） | 没 stage-type 维度的 watchdog policy |

## 1. Stage type taxonomy

把所有 in-flight state 分 4 类，**watchdog policy 按类决定**：

| stage type | 例 state | 特性 | watchdog policy |
|---|---|---|---|
| `human-loop-conversation` | `INTAKING`、`PENDING_USER_PR_REVIEW`（新） | 多轮 + 等人 + 不知何时 ready | **不 watchdog**；仅监听 BKD `session.failed` 事件做 crash escalate |
| `autonomous-bounded` | `ANALYZING`、`CHALLENGER_RUNNING`、`ACCEPT_RUNNING`、`ACCEPT_TEARING_DOWN`、`ARCHIVING`、`FIXER_RUNNING` | 无人参与，agent 跑应在 30min 内出结果 | timer 30min（默认）；超时 escalate |
| `deterministic-checker` | `SPEC_LINT_RUNNING`、`DEV_CROSS_CHECK_RUNNING`、`STAGING_TEST_RUNNING`、`ANALYZE_ARTIFACT_CHECKING` | shell exec，秒到分级 | timer 5min；超时 escalate |
| `external-poll` | `PR_CI_RUNNING` | 轮 GH check-runs，可能 4h+ | timer 4h；超时 escalate |

`REVIEW_RUNNING` / `FIXER_RUNNING` 是 verifier/fixer 子链，watchdog 视为 `autonomous-bounded`。

落地：`config.py` 加 `STAGE_WATCHDOG_POLICY: dict[ReqState, dict | None]`，`watchdog.py` scan req_state 时按 policy 应用 timeout。`None` policy 表示该 state 完全不 watchdog（仅事件触发）。

## 2. PENDING_USER_PR_REVIEW state（新）

### 2.1 入口

`ACCEPT_TEARING_DOWN + TEARDOWN_DONE_PASS` → 不再直接 ARCHIVING，先进 PENDING_USER_PR_REVIEW，让用户验收。

```
ACCEPT_TEARING_DOWN
  ↓ TEARDOWN_DONE_PASS
PENDING_USER_PR_REVIEW         ← 新 state
  ↓ （事件驱动出口）
  ↓
{ARCHIVING | FIX_DEV_RUNNING | ESCALATED}
```

### 2.2 进入时 sisyphus 必做

1. accept stage 已产 acceptance report（thanatos 跑 scenarios 输出）+ artifacts（截图 / a11y tree / network log）
2. accept stage 之前已把 artifacts push 到 source repo `.acceptance/<REQ_ID>/` 目录（feat 分支上）
3. PATCH 对应 GH PR 加一条 review comment：

```markdown
🤖 sisyphus acceptance report (REQ-X)

## Scenarios

✅ FEATURE-A login: pass
![](https://raw.githubusercontent.com/owner/repo/feat/REQ-X/.acceptance/REQ-X/login.png)

✅ FEATURE-B checkout: pass
![](https://raw.githubusercontent.com/owner/repo/feat/REQ-X/.acceptance/REQ-X/checkout.png)

⚠️ FEATURE-C profile: 偏离 spec
spec: "header 标题 = 用户名全称"
actual: "Welcome"
![](https://raw.githubusercontent.com/owner/repo/feat/REQ-X/.acceptance/REQ-X/profile.png)

## Action

请 review，根据情况 approve / request changes / close。
- ✅ approve = 满意 → sisyphus archive
- 🔁 request changes + 留具体评论 = 不满意 → sisyphus 起 fixer 一轮
- ❌ close PR = 撤需求 → sisyphus 终态 user_rejected
```

4. PATCH BKD intent issue body 加 sisyphus-managed status block 同步状态（cross-link）

### 2.3 事件驱动出口（无 timer）

| 事件源 | 条件 | sisyphus emit | next state |
|---|---|---|---|
| GH `pull_request_review` | `state=approved` | `ACCEPT_USER_APPROVED` | `ARCHIVING` |
| GH `pull_request_review` | `state=changes_requested` | `VERIFY_FIX_NEEDED` + `ctx.user_pr_review_comments[]` += review.body | `FIX_DEV_RUNNING` |
| GH `pull_request_review_comment` | 普通评论（非 approve/reject） | 排队 `ctx.user_pr_review_comments[]`，不 trigger | （仍 PENDING） |
| GH `pull_request` | `action=closed`, `merged=false` | `USER_REJECTED` | `ESCALATED` reason=user_rejected |
| GH `pull_request` | `action=closed`, `merged=true` | `ACCEPT_USER_APPROVED` | `ARCHIVING` |
| BKD `comment.created` on intent issue | non-bot author + 非 sisyphus marker | 同 GH `pull_request_review_comment` 排队 | （仍 PENDING） |
| BKD `issue.updated` statusId→done by user | non-bot | `USER_REJECTED` | `ESCALATED` reason=user_closed |
| BKD `session.failed` (crashed) | 任何 BKD agent 崩 | `SESSION_FAILED` | `ESCALATED` reason=bkd-crash |

**没事件 = 静默等。可以等几小时几天。watchdog 不 kill。**

### 2.4 fixer 接力路径

```
PENDING_USER_PR_REVIEW
  ↓ user request_changes + 评论 "fix profile header"
  ↓ emit VERIFY_FIX_NEEDED + ctx.user_pr_review_comments[] += "fix profile header"
FIX_DEV_RUNNING
  ↓ fixer-agent prompt 自动带：
  ↓   - 当前 PR diff
  ↓   - acceptance report（最新一份）
  ↓   - user 反馈（ctx.user_pr_review_comments）
  ↓ fixer push 新 commit 到 feat/REQ-X
  ↓ fixer.done
REVIEW_RUNNING (verifier 看 fixer 改对没)
  ↓ verifier pass → 重跑 accept stage（thanatos 跑新 scenarios）→ 新 acceptance report → 回 PENDING_USER_PR_REVIEW
  ↓ verifier fix → 再 fixer 一轮（cap 5）
  ↓ verifier escalate → ESCALATED
```

**fixer cap 5 轮**沿用现有限制。第 6 轮 user request_changes 时强制 escalate（reason=fixer-cap-exceeded）让人介入。

## 3. interrupt / resume 设计

### 3.1 interrupt（用户中途想停）

任何 in-flight state（包括 ANALYZING / CHALLENGER / DEV_CROSS / etc）：

| 用户操作 | sisyphus emit | next state |
|---|---|---|
| GH PR `closed`（merged=false） | `USER_INTERRUPT` | `ESCALATED` reason=user_canceled |
| BKD intent issue statusId→done by user | `USER_INTERRUPT` | `ESCALATED` reason=user_canceled |

PVC 保留（默认 `pvc_retain_on_escalate_days=7`），让人 debug。

### 3.2 resume（用户改主意继续）

在 `ESCALATED` reason ∈ {user_canceled, user_rejected, …} 时：

| 用户操作 | sisyphus emit | next state |
|---|---|---|
| GH PR comment（带 `/sisyphus resume` 黑话或纯文字） | `RESUME_FROM_USER` + ctx.resume_message | 重入 escalate 时的 previous_state |
| BKD intent comment | 同上 | 同上 |
| BKD intent issue statusId→todo (重开) | 同上 | 同上 |

**不支持 stage 内 checkpoint resume**（即不能"接着上次跑到一半的地方继续"），整个 stage 重跑。原因：复杂度爆炸，stage 平均 5-30min 重跑成本可接受（v2 再做 checkpoint）。

context（ctx jsonb）保留 → 重跑 stage 的 agent 能看到先前 attempt 的产物（PR diff / acceptance report / verifier reasoning）+ 用户 resume_message。

### 3.3 不允许的

- **stage 内 graceful interrupt**（"等当前 step 跑完再停"）—— 复杂度高，没明显收益。当前所有 interrupt = 立即 force escalate
- **stage-level checkpoint**（思路 1 的 LangGraph 风）—— v2 工作。先看 stage_runs.duration_sec p99 数据决定值不值

## 4. acceptance evidence 存储 contract

### 4.1 路径约定

```
<source-repo>/
  .acceptance/
    <REQ_ID>/                           ← 每 REQ 一个目录
      manifest.json                     ← scenario list + result + asset 列表
      login-pass.png                    ← 截图按 scenario_id-result 命名
      checkout-pass.png
      profile-warn.png
      a11y-trees/
        login.json                      ← a11y snapshot
      network/
        login.har                       ← network log
```

### 4.2 commit 行为

accept stage 跑完后 sisyphus runner pod 执行：

```bash
cd /workspace/source/<repo>
mkdir -p .acceptance/${REQ_ID}/
cp /thanatos/output/* .acceptance/${REQ_ID}/
git add .acceptance/${REQ_ID}/
git commit -m "test(accept): ${REQ_ID} evidence (${SCENARIO_COUNT} scenarios)"
git push origin feat/${REQ_ID}
```

### 4.3 PR 评论引用

```markdown
![](https://raw.githubusercontent.com/<owner>/<repo>/feat/${REQ_ID}/.acceptance/${REQ_ID}/login-pass.png)
```

raw URL 永久有效（commit 不会被 GC）。

### 4.4 .gitignore 跟 cleanup

source repo 的 `.gitignore` **不应** ignore `.acceptance/`（这些 commit 要保留）。
**sisyphus 不主动清** `.acceptance/<REQ>/`，让历史完整保留。

如未来 repo 体积过大，运维侧定期跑 sweep 脚本：
```bash
# 删 archive>90 天 REQ 的 .acceptance/<REQ>/
```

但**M0 不做 cleanup**，先看真实膨胀速率（10 REQ/day × 几 MB → 月增 ~1GB），一年后再考虑。

## 5. 落地路线图

### M0（本设计 doc 落地后立刻可派）

1. **`REQ-watchdog-stage-policy`** ⏳ 已派 (#361 c6ik4kw2)
   - `config.py` 加 `STAGE_WATCHDOG_POLICY`
   - `watchdog.py` 按 stage type 应用 timeout
   - `INTAKING` 不 watchdog（事件驱动）

2. **`REQ-pr-review-feedback-loop`**（M0 重头戏）
   - `state.py` 加 `PENDING_USER_PR_REVIEW` state + 4 个新 event
   - `webhook.py` 加 GH `pull_request_review` event handler
   - `actions/post_acceptance_report.py` 把 acceptance report PATCH 进 GH PR
   - `actions/route_user_feedback.py` parse review.state + comment routing
   - state machine transition 6 条新增

   **2026-04-27 状态**：BKD-native 子集（"Case 2 — 0 黑话纯原语"）已落地为
   `REQ-bkd-acceptance-feedback-loop-1777278984`（feat 分支
   `feat/REQ-bkd-acceptance-feedback-loop-1777278984`，PR phona/sisyphus#TBD）：

   - state `pending-user-review` + 2 events `user-review.pass` / `user-review.fix`
   - `actions/post_acceptance_report.py` 给 BKD intent issue 加 `acceptance-pending`
     tag + follow_up acceptance 通知（含 PR 列表 + statusId 操作说明）
   - webhook `_maybe_derive_user_review_event` helper 在 PENDING_USER_REVIEW state
     收 issue.updated 时按 BKD intent issue 当前 statusId 派事件（`done`/`review`/`blocked`
     才识别；其他 statusId 一律 skip）
   - watchdog `_NO_WATCHDOG_STATES` 加 PENDING_USER_REVIEW
   - **不在该 REQ 范围**：GH `pull_request_review` webhook handler / 自由文本评论
     解析 / fixer 自动接力（用户 statusId=`review` 直接走 ESCALATED + reason
     `user-requested-fix`，回头通过 §3 resume 通道恢复）

3. **`REQ-user-interrupt-resume`**
   - `webhook.py` 加 GH `pull_request` closed handler
   - `webhook.py` 加 BKD `comment.created` 在 ESCALATED REQ 上的 resume 触发
   - state.py 加 `USER_INTERRUPT` / `RESUME_FROM_USER` event + transition

4. **`REQ-acceptance-evidence-commit`**
   - accept stage / done_archive stage 把 thanatos 输出 push 到 `.acceptance/<REQ>/`
   - 跟 `REQ-pr-review-feedback-loop` 协作（前者是数据准备，后者是消费）

### M1+（不在本 doc scope）

- `stage_checkpoints` 表 + checkpoint 协议（思路 1）
- stage 内 graceful interrupt
- daily digest（"REQs sitting in PENDING_USER_PR_REVIEW > 24h"）
- 多人 review 协调（跨 reviewer state aggregation）

## 6. 不引入 LangGraph 等 framework 的理由

讨论中提及 LangGraph 这类 checkpoint workflow framework 是否值得引入：

- LangGraph checkpoint 本质 = 加一张 state store 表 + node 执行时落 checkpoint
- sisyphus 已有 Postgres + req_state(jsonb context) + stage_runs + verifier_decisions + artifact_checks **五张表**，存储基础充分
- LangGraph 强绑 LangChain 生态，BKD agent dispatch 不是 LangChain 风，需要适配层
- LangGraph 的 graph DSL（decorator + dynamic dispatch）比 sisyphus 现有声明式 transition table 复杂度反而高

**结论**：借 idea（checkpoint pattern）不引 framework。如未来真要做 stage 内 checkpoint，加一张 `stage_checkpoints(req_id, stage, checkpoint_id, state jsonb)` 表 + agent 自报 checkpoint，~100 LoC 内自己实现。

参考但不引入。
