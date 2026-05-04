#!/usr/bin/env python3
"""state-machine silent-drop lint (REQ-feat-silent-lint-376-v2 / closes #376).

遍历 orchestrator.state.TRANSITIONS，把 (state, event) → no-progress transition
（next_state == src_state）静态挑出来，强制作者用 `progress` 字段显式标注 ——
"no"（候选死锁，需 telemetry）或 "explicit-noop"（intentional self-loop，acked）。

事故背景：5/4 v5 verifier emit `decision:pass` 字符串 tag → router 解析失败回
fallback `Event.VERIFY_ESCALATE` → ESCALATED state 收到走 self-loop（next=ESCALATED,
action=None）→ REQ 永久卡 ESCALATED 零反馈。所有"信号"都对，但语义死循环。

本 lint 让"显式 no-progress"在静态层面被人类 review 时一眼可见，禁止悄悄合入。

退码：
  0  全绿
  1  违反任意规则（详见 stderr 列表）
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让脚本可从 repo root 直接 `python3 scripts/lint-state-transitions.py` 跑
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ORCH_SRC = _REPO_ROOT / "orchestrator" / "src"
if str(_ORCH_SRC) not in sys.path:
    sys.path.insert(0, str(_ORCH_SRC))


ALLOWED_PROGRESS = ("yes", "no", "explicit-noop")


def validate(transitions: dict) -> list[str]:
    """返 violation 字符串列表。空 list 即全绿。"""
    violations: list[str] = []
    for (src_state, event), trans in transitions.items():
        progress = getattr(trans, "progress", None)
        is_self_loop = trans.next_state == src_state

        if progress is not None and progress not in ALLOWED_PROGRESS:
            violations.append(
                f"({src_state.value}, {event.value}): "
                f"unknown progress value {progress!r}; "
                f"allowed = {ALLOWED_PROGRESS}"
            )
            continue

        if is_self_loop:
            if progress is None:
                violations.append(
                    f"({src_state.value}, {event.value}): "
                    f"self-loop requires progress annotation "
                    f"(set progress='no' for deadlock candidate or "
                    f"progress='explicit-noop' for intentional self-loop)"
                )
            elif progress == "yes":
                violations.append(
                    f"({src_state.value}, {event.value}): "
                    f"progress=yes contradicts self-loop "
                    f"(next_state={trans.next_state.value} == src_state)"
                )
        else:
            if progress in ("no", "explicit-noop"):
                violations.append(
                    f"({src_state.value}, {event.value}): "
                    f"progress={progress!r} contradicts advancing transition "
                    f"(next_state={trans.next_state.value} != src_state)"
                )
    return violations


def render_report(transitions: dict) -> str:
    """人类可读报告：按 progress 桶分组列举所有 transition。"""
    buckets: dict[str, list[str]] = {"yes": [], "no": [], "explicit-noop": []}
    for (src_state, event), trans in sorted(
        transitions.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value)
    ):
        progress = getattr(trans, "progress", None)
        is_self_loop = trans.next_state == src_state
        # 派生 yes：non-self-loop 且 progress 未声明
        effective = progress or ("yes" if not is_self_loop else "?")
        if effective in buckets:
            buckets[effective].append(
                f"  ({src_state.value}, {event.value}) → {trans.next_state.value}"
                + (f"  [action={trans.action}]" if trans.action else "")
            )

    lines = ["=== State machine transition lint ==="]
    lines.append(f"Total transitions: {len(transitions)}")
    lines.append(f"  progress=yes (advances)             : {len(buckets['yes'])}")
    lines.append(f"  progress=explicit-noop (intentional): {len(buckets['explicit-noop'])}")
    lines.append(f"  progress=no (deadlock candidate)    : {len(buckets['no'])}")
    lines.append("")
    lines.append("--- explicit-noop (acknowledged self-loops) ---")
    lines.extend(buckets["explicit-noop"] or ["  (none)"])
    lines.append("")
    lines.append("--- no (deadlock candidates, may need telemetry) ---")
    lines.extend(buckets["no"] or ["  (none)"])
    return "\n".join(lines)


def main() -> int:
    from orchestrator.state import TRANSITIONS  # imported lazily so import errors stay scoped

    violations = validate(TRANSITIONS)
    print(render_report(TRANSITIONS))
    print("")
    if violations:
        print("=== VIOLATIONS ===", file=sys.stderr)
        for v in violations:
            print(v, file=sys.stderr)
        print(f"\n{len(violations)} violation(s); fix state.py and re-run.",
              file=sys.stderr)
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
