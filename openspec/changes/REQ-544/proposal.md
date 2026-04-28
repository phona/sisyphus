# REQ-544: Descope IMPROVER Daemon, Document Human-Driven Improvement Loop

## Problem

`docs/architecture.md` references an "IMPROVER agent" in the repository-role table and describes `config_version` + `improvement_log` as a mechanism for system improvement. This has been repeatedly misread by readers (including earlier design reviews) as describing an **automated IMPROVER daemon** that would:

- Continuously read Metabase dashboards
- Auto-generate improvement hypotheses
- Automatically rewrite prompts / thresholds
- Self-heal the system without human involvement

This misunderstanding is dangerous because it:
1. Sets false expectations about automation level
2. Distracts from the actual human-driven process already documented in `observability.md`
3. Causes readers to scan `config_version` and `improvement_log` schemas looking for daemon consumer code that does not (and should not) exist

## Reality

The sustainable improvement loop is **human-driven** (看板→诊断→假设→实施→验证), as explicitly described in `observability.md`:

1. Human observes anomaly on Metabase dashboard (Q1-Q18)
2. Human diagnoses root cause via SQL queries on `verifier_decisions`, `event_log`, `stage_runs`
3. Human forms hypothesis (e.g., "verifier pr_ci_fail prompt needs retry_checker hint")
4. Human implements change (modifies prompt, updates threshold), bumps `config_version`
5. Human records hypothesis in `improvement_log` with baseline/target metrics
6. After 2 weeks, human runs `metric_sql` to get `observed_value`, fills `verdict`

The 18 Metabase SQL queries are consumed by **people**, not machines.

## Solution

1. Remove "IMPROVER" branding from `architecture.md` §0.4 (rename to neutral description)
2. Add explicit clarification in `architecture.md` §10 that `config_version` + `improvement_log` serve human-driven improvement, not an automated daemon
3. Strengthen `observability.md` sustainable-improvement-loop section to explicitly state "人工驱动 / no automated daemon"
4. Fix `docs/IMPACT-REPORT.md` line that describes `improvement_log` as "系统自我改进建议（TODO：当前未启用）" — this phrasing directly feeds the daemon misinterpretation
5. Add contract tests preventing regression of this documentation drift

## Scope

- Modified: `docs/architecture.md` (§0.4, §10)
- Modified: `docs/observability.md` (sustainable improvement loop section)
- Modified: `docs/IMPACT-REPORT.md` (observability design section)
- New: `orchestrator/tests/test_contract_improver_descope.py`
