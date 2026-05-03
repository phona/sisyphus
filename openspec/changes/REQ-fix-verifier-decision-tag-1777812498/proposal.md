# REQ-fix-verifier-decision-tag-1777812498 — verifier decision tag mandate + orch fallback

Refs phona/sisyphus#356.

## 现状

`session.completed` from a verifier sub-issue currently carries the decision in
exactly one place: a ` ```json {...} ``` ` block inside the agent's last
assistant-message. `webhook._derive_verifier_event` extracts that JSON via
`extract_decision_robust`, retries the agent up to 2 times when parsing fails
(`_VERIFIER_RETRY_PROMPT`), and otherwise routes `VERIFY_ESCALATE`.

Three real-world stuck verifier issues (BKD#753 / #754 / #760, all logged on
issue #356) show the failure mode: the JSON block never made it to the log.
Possible causes (any one is enough): watchdog killed the agent before the JSON
was emitted, BKD `PATCH /tags` failed, or the agent only wrote the human
summary. The retry loop then either also fails (same mechanism) or never fires
because the parser found "no decision-like text" (`retry_worthy=False`) and the
REQ lands in `ESCALATED` with `escalated_reason=verifier-decision`.

The current `_decision.md.j2` calls a `decision:<action>` BKD tag "纯 UX,
不影响 sisyphus 解析" (purely cosmetic). That single sentence is exactly what
this REQ flips.

## 修法（两条腿，互相兜底）

1. **Prompt mandate** — `_decision.md.j2` SHALL require the verifier-agent to
   `PATCH` a plain `decision:<action>[-<fixer>]` tag onto its own BKD issue
   *before* writing the JSON block. The tag is the redundant signal: it
   survives even if the assistant-message is truncated / never written.

2. **Orch fallback parse** — `verifier_parser.extract_decision_robust` SHALL
   recognize the plain `decision:<action>[-<fixer>]` tag as a third extraction
   source after (a) base64-encoded JSON tag and (b) text-embedded JSON. The
   synthesized decision is marked `confidence="low"` and
   `reason="orch-fallback: inferred from decision:<X> tag"`, so dashboards
   (Q15 / Q8) can flag it as imprecise but still let the state machine
   advance.

Together: the prompt produces a redundant signal; the parser learns to read
it. Either path alone fixes the stuck case; together they survive any
single-side regression.

## 不做的事

- 不在 verifier 主链上加新 stage / 新 event。复用现有 4 路 decision (pass /
  fix / escalate / retry) 和现有 retry 流程。
- 不引入文本 keyword grep 兜底（"Verdict: pass" 之类）—— 误判风险大；显式 tag
  足够。
- 不改 watchdog 行为（#352 是另一个 REQ）。本 REQ 只对 *已经 completed* 的
  verifier issue 多一条保险。
