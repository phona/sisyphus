#!/usr/bin/env python3
"""sisyphus-trace —— 单 REQ 全生命周期 timeline CLI (REQ-feat-req-trace-view-381-v2-1777866643)。

读取 sisyphus 主库 4 张表 (req_state.history, stage_runs, verifier_decisions,
artifact_checks), 把所有事件按时间戳合到一条时间线, 渲染成可读 ASCII 或 NDJSON。

调试 "REQ 卡住不动" 不再 grep 源码：30 min → 30 s。

用法
====

::

    # ASCII 时间线 (默认)
    sisyphus-trace REQ-feat-req-trace-view-381-v2-1777866643

    # NDJSON, 给 jq pipe 用
    sisyphus-trace REQ-XXXX --json | jq 'select(.kind=="verify")'

    # 自定义 namespace / pod (调试用, 默认与 sisyphus-admin.py 同款)
    sisyphus-trace REQ-XXXX --namespace sisyphus --pg-pod sisyphus-postgresql-0

依赖
====

- 本机 ``kubectl`` 且上下文指向 sisyphus 集群
- PG pod 用 ``psql -U sisyphus -d sisyphus``, 密码从 pod env ``POSTGRES_PASSWORD_FILE`` 取

退出码
======

- 0  正常输出 (即使 REQ 没行)
- 1  PG 出错 / kubectl 不可用 / SQL 失败
- 2  argparse 用法错 (REQ-id 漏传等)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

DEFAULT_NAMESPACE = "sisyphus"
DEFAULT_PG_POD = "sisyphus-postgresql-0"

# Q24 SQL 路径 (相对仓根)。CLI 直接读这个文件, Metabase 也用同一份 -- single source of truth。
_REPO_ROOT = Path(__file__).resolve().parent.parent
_Q24_PATH = _REPO_ROOT / "observability" / "queries" / "sisyphus" / "24-req-trace.sql"


# ─── kubectl helpers (与 sisyphus-admin.py 同款风格, 不抽公共模块, 避免给一个 CLI 引一个 lib) ──


def _have_kubectl() -> bool:
    try:
        return subprocess.run(
            ["kubectl", "version", "--client=true"],
            capture_output=True,
        ).returncode == 0
    except FileNotFoundError:
        return False


def _pg_query(sql: str, *, namespace: str, pod: str) -> str:
    """通过 kubectl exec PG pod 跑只读 SQL; 密码从 pod env 取, 不外泄。"""
    if not _have_kubectl():
        raise RuntimeError("kubectl 不可用; 本机无集群上下文")
    inner = (
        'PGPASSWORD="$(cat $POSTGRES_PASSWORD_FILE)" '
        "psql -U sisyphus -d sisyphus -t -A -F '|'"
    )
    cmd = ["kubectl", "-n", namespace, "exec", "-i", pod, "--", "bash", "-c", inner]
    proc = subprocess.run(cmd, input=sql, text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr or proc.stdout}")
    return proc.stdout


# ─── SQL loading + binding ─────────────────────────────────────────────────


def _load_q24_sql() -> str:
    if not _Q24_PATH.exists():
        raise RuntimeError(f"Q24 SQL not found at {_Q24_PATH}")
    return _Q24_PATH.read_text(encoding="utf-8")


def _bind_req_id(sql: str, req_id: str) -> str:
    """把 Metabase ``{{req_id}}`` 占位替换成 quoted literal。

    REQ-id 是受控字符 (`[A-Za-z0-9_-]+`), 但仍走 `quote_literal` 保险:
    用 psql 的双单引号转义 (与 PostgreSQL 字符串字面量规则一致)。
    """
    safe = req_id.replace("'", "''")
    return sql.replace("{{req_id}}", f"'{safe}'")


# ─── parse psql output ────────────────────────────────────────────────────


def _parse_psql_rows(raw: str) -> list[tuple[datetime, str, str]]:
    """psql -t -A -F '|' 输出: 一行一记录, 字段 `|` 分隔。

    rstrip 单行末空白; 跳过空行。detail 内本身可能含 `|` (cmd 字段) → 用
    maxsplit=2 限制只切前 2 刀, 余下整体进 detail。
    """
    out: list[tuple[datetime, str, str]] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        ts_s, kind, detail = parts
        try:
            ts = datetime.fromisoformat(ts_s.replace(" ", "T"))
        except ValueError:
            continue
        out.append((ts, kind, detail))
    return out


# ─── renderers ────────────────────────────────────────────────────────────


def render_ascii(req_id: str, rows: Iterable[tuple[datetime, str, str]]) -> str:
    lines = [f"sisyphus-trace {req_id}", "─" * 60]
    for ts, kind, detail in rows:
        lines.append(f"{ts.strftime('%H:%M:%S')} [{kind}] {detail}")
    return "\n".join(lines) + "\n"


def render_ndjson(rows: Iterable[tuple[datetime, str, str]]) -> str:
    out_lines: list[str] = []
    for ts, kind, detail in rows:
        out_lines.append(json.dumps(
            {"ts": ts.isoformat(), "kind": kind, "detail": detail},
            ensure_ascii=False,
        ))
    return ("\n".join(out_lines) + "\n") if out_lines else ""


# ─── parser ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sisyphus-trace",
        description="REQ 全生命周期 timeline (req_state history + stage_runs + "
                    "verifier_decisions + artifact_checks)。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("req_id", help="REQ-id (e.g. REQ-feat-foo-1777xxx)")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="NDJSON 模式 (一行一 JSON 对象), 适合 jq pipe")
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE,
                   help=f"K8s namespace (default: {DEFAULT_NAMESPACE})")
    p.add_argument("--pg-pod", default=DEFAULT_PG_POD,
                   help=f"PG pod 名 (default: {DEFAULT_PG_POD})")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sql = _bind_req_id(_load_q24_sql(), args.req_id)
        raw = _pg_query(sql, namespace=args.namespace, pod=args.pg_pod)
    except RuntimeError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return 1
    rows = _parse_psql_rows(raw)
    if args.as_json:
        sys.stdout.write(render_ndjson(rows))
    else:
        sys.stdout.write(render_ascii(args.req_id, rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
