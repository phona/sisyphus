#!/usr/bin/env python3
"""sisyphus-trace — 单 REQ 全生命周期事件时间线

从四张表（req_state.history + stage_runs + verifier_decisions + artifact_checks）
聚合同一 req_id 的所有事件，按时间戳升序输出 ASCII 时间线。

子命令：无（单用途 script）

依赖
====

模式 A（默认）：
- 本地有 ``kubectl`` 且上下文指向 sisyphus 集群
- PG pod 名 ``sisyphus-postgresql-0``，密码从 pod env ``POSTGRES_PASSWORD_FILE`` 取

模式 B（DATABASE_URL）：
- 设置环境变量 ``DATABASE_URL=postgresql://...``，直接调本地 ``psql`` 命令

Examples
========

::

    # kubectl 模式（集群内 debug，最常用）
    python3 scripts/sisyphus-trace.py REQ-feat-xxx-381

    # 只看 verifier 和 checker 事件
    python3 scripts/sisyphus-trace.py REQ-feat-xxx-381 --types verifier,checker

    # 直连 PG
    DATABASE_URL=postgresql://sisyphus:pass@localhost:5432/sisyphus \\
        python3 scripts/sisyphus-trace.py REQ-feat-xxx-381

    # 不显示颜色（管道友好）
    python3 scripts/sisyphus-trace.py REQ-feat-xxx-381 --no-color | less

退出码
======

- 0  正常（含零事件）
- 1  参数错 / kubectl 不可用 / psql 报错
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

DEFAULT_NAMESPACE = "sisyphus"
DEFAULT_PG_POD = "sisyphus-postgresql-0"

_SQL = """
WITH state_transitions AS (
    SELECT
        (h->>'ts')::timestamptz                                          AS ts,
        'state_transition'::text                                         AS event_type,
        NULL::text                                                       AS stage,
        NULL::text                                                       AS outcome,
        (h->>'from_state') || ' → ' || (h->>'to_state')           AS summary,
        COALESCE(h->>'event', '')                                        AS detail
    FROM req_state r,
         jsonb_array_elements(r.history) AS h
    WHERE r.req_id = '{req_id}'
),
stage_events AS (
    SELECT
        started_at                                                       AS ts,
        'stage_start'::text                                              AS event_type,
        stage,
        NULL::text                                                       AS outcome,
        stage || ' started'
            || COALESCE(' [' || agent_type || ']', '')                  AS summary,
        COALESCE(bkd_issue_id, '')                                       AS detail
    FROM stage_runs
    WHERE req_id = '{req_id}'
    UNION ALL
    SELECT
        ended_at                                                         AS ts,
        'stage_end'::text                                                AS event_type,
        stage,
        outcome,
        stage || ' ended: ' || COALESCE(outcome, '?')                   AS summary,
        COALESCE(fail_reason, '')                                        AS detail
    FROM stage_runs
    WHERE req_id = '{req_id}'
      AND ended_at IS NOT NULL
),
verifier_events AS (
    SELECT
        made_at                                                          AS ts,
        'verifier'::text                                                 AS event_type,
        stage,
        decision_action                                                  AS outcome,
        'verifier/' || stage || ': '
            || COALESCE(decision_action, '?')
            || COALESCE(' (fixer=' || decision_fixer || ')', '')        AS summary,
        COALESCE(decision_reason, '')                                    AS detail
    FROM verifier_decisions
    WHERE req_id = '{req_id}'
),
checker_events AS (
    SELECT
        checked_at                                                       AS ts,
        'checker'::text                                                  AS event_type,
        stage,
        CASE WHEN passed THEN 'pass' ELSE 'fail' END                    AS outcome,
        stage || ' checker: '
            || CASE WHEN passed
               THEN 'PASS'
               ELSE 'FAIL (exit=' || COALESCE(exit_code::text, '?') || ')'
               END                                                      AS summary,
        COALESCE(
            LEFT(stderr_tail, 160),
            LEFT(stdout_tail, 160),
            cmd,
            ''
        )                                                               AS detail
    FROM artifact_checks
    WHERE req_id = '{req_id}'
)
SELECT
    TO_CHAR(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') AS ts_str,
    event_type,
    COALESCE(stage, '-')   AS stage,
    COALESCE(outcome, '-') AS outcome,
    summary,
    detail
FROM (
    SELECT * FROM state_transitions
    UNION ALL
    SELECT * FROM stage_events
    UNION ALL
    SELECT * FROM verifier_events
    UNION ALL
    SELECT * FROM checker_events
) combined
ORDER BY ts NULLS LAST;
"""

_OUTCOME_COLOR = {
    "pass":     "\033[32m",   # green
    "fail":     "\033[31m",   # red
    "escalate": "\033[33m",   # yellow
    "fix":      "\033[33m",   # yellow
}
_RESET = "\033[0m"
_BOLD  = "\033[1m"


# ─── kubectl / psql helpers ────────────────────────────────────────────────


def _have_kubectl() -> bool:
    try:
        return subprocess.run(
            ["kubectl", "version", "--client=true"],
            capture_output=True,
        ).returncode == 0
    except FileNotFoundError:
        return False


def _run_sql_kubectl(sql: str, *, namespace: str, pod: str) -> str:
    """kubectl exec into the postgresql pod and run SQL via psql."""
    inner = (
        'PGPASSWORD="$(cat $POSTGRES_PASSWORD_FILE)" '
        "psql -U sisyphus -d sisyphus -t -A -F'|'"
    )
    cmd = ["kubectl", "-n", namespace, "exec", "-i", pod, "--", "bash", "-c", inner]
    proc = subprocess.run(cmd, input=sql, text=True, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr or proc.stdout}")
    return proc.stdout


def _run_sql_url(sql: str, database_url: str) -> str:
    """Run SQL via local psql binary using DATABASE_URL."""
    proc = subprocess.run(
        ["psql", database_url, "-t", "-A", "-F|", "-c", sql],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr or proc.stdout}")
    return proc.stdout


# ─── rendering ─────────────────────────────────────────────────────────────


def _color(text: str, code: str, enabled: bool) -> str:
    return f"{code}{text}{_RESET}" if enabled else text


def _render(rows: list[list[str]], *, use_color: bool, filter_types: set[str] | None) -> int:
    header = f"{'TIME (UTC)':<20}  {'TYPE':<18}  {'STAGE':<24}  {'OUT':<10}  SUMMARY"
    if use_color:
        header = _color(header, _BOLD, True)
    print(header)
    print("-" * 110)

    shown = 0
    for row in rows:
        ts_str, event_type, stage, outcome, summary, detail = row[:6]
        if filter_types and event_type not in filter_types:
            continue

        color = _OUTCOME_COLOR.get(outcome, "")
        outcome_cell = _color(f"{outcome:<10}", color, use_color and bool(color))
        line = f"{ts_str:<20}  {event_type:<18}  {stage:<24}  {outcome_cell}  {summary}"
        if use_color and color:
            print(_color(line, color, True))
        else:
            print(line)
        if detail.strip():
            print(f"    {detail[:120]}")
        shown += 1

    return shown


# ─── main ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Print a single REQ lifecycle timeline from the orchestrator DB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("req_id", help="REQ ID（例如 REQ-feat-xxx-381）")
    p.add_argument(
        "--namespace", default=DEFAULT_NAMESPACE,
        help=f"K8s namespace（kubectl 模式，默认 {DEFAULT_NAMESPACE}）",
    )
    p.add_argument(
        "--pg-pod", default=DEFAULT_PG_POD,
        help=f"PostgreSQL pod 名（默认 {DEFAULT_PG_POD}）",
    )
    p.add_argument(
        "--types",
        help="只显示指定类型（逗号分隔）：state_transition,stage_start,stage_end,verifier,checker",
    )
    p.add_argument("--no-color", action="store_true", help="关闭 ANSI 颜色（管道输出时自动关）")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    req_id: str = args.req_id
    if not all(c.isalnum() or c in "-_" for c in req_id):
        print(f"ERR: invalid req_id {req_id!r}（only alphanumerics, hyphens, underscores）", file=sys.stderr)
        return 1

    filter_types: set[str] | None = None
    if args.types:
        filter_types = {t.strip() for t in args.types.split(",")}

    sql = _SQL.format(req_id=req_id)
    database_url = os.environ.get("DATABASE_URL", "")

    try:
        if database_url:
            raw = _run_sql_url(sql, database_url)
        elif _have_kubectl():
            raw = _run_sql_kubectl(sql, namespace=args.namespace, pod=args.pg_pod)
        else:
            print(
                "ERR: kubectl not available and DATABASE_URL not set.\n"
                "  Set DATABASE_URL=postgresql://... or ensure kubectl is on PATH.",
                file=sys.stderr,
            )
            return 1
    except RuntimeError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return 1

    rows: list[list[str]] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 5)
        while len(parts) < 6:
            parts.append("")
        rows.append(parts)

    use_color = (not args.no_color) and sys.stdout.isatty()
    title = f"\n=== REQ Trace: {req_id} ===\n"
    print(_color(title, _BOLD, use_color))
    shown = _render(rows, use_color=use_color, filter_types=filter_types)
    print(f"\nTotal: {shown} event(s)" + (f" (filtered from {len(rows)})" if filter_types and shown != len(rows) else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
