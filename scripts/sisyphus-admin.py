#!/usr/bin/env python3
"""sisyphus 集群管理 CLI（PG 查询 + orch admin endpoint）。

只盖**sisyphus-specific**操作：依赖 K8s 集群上下文（kubectl）+ sisyphus PG schema +
orch admin endpoint。通用 BKD REST 操作请见同目录 ``bkd-cli.py``。

子命令：

  req-status   查 PG ``req_state`` 表（in-flight / 单 REQ history / state 计数）
  admin        调 orch admin endpoint：``escalate`` / ``complete`` / ``pr-merged``

依赖
====

模式 A（默认）：
- 本地有 ``kubectl`` 且上下文指向 sisyphus 集群（K3s on vm-node04）
- PG pod 名 ``sisyphus-postgresql-0``，从 pod env ``POSTGRES_PASSWORD_FILE`` 取密码
- orch admin endpoint 通过 ``kubectl exec deploy/orch-sisyphus-orchestrator`` curl localhost:8000
  调用，token 从 secret ``orch-sisyphus-orchestrator`` ``.data.webhook_token`` 取

模式 B（直接 HTTP）：
- ``--base-url`` 指向外部可访问的 orch 地址（如 http://sisyphus.43.239.84.24.nip.io）
- ``--token`` 或环境变量 ``SISYPHUS_ADMIN_TOKEN`` 提供 Bearer token
- PG 仍走 kubectl exec（除非未来加 PG 外部端口）

token / 密码全程不打到 transcript（在 pod 内部用，结果只回 admin endpoint 响应）。

Examples
========

::

    # 看 in-flight REQ
    sisyphus-admin.py req-status

    # 单 REQ 完整 history
    sisyphus-admin.py req-status REQ-441

    # state 计数
    sisyphus-admin.py req-status --terminal

    # admin escalate（强制把 REQ 推到 escalated 终态）
    sisyphus-admin.py admin escalate REQ-XXX --reason "duplicate-of-REQ-YYY" --kind sandbox-cleanup

    # admin complete（escalated → done）
    sisyphus-admin.py admin complete REQ-XXX --reason "manual-reviewed"

    # admin pr-merged（手动触发 PR-merge hook）
    sisyphus-admin.py admin pr-merged REQ-XXX \\
        --pr-url https://github.com/phona/sisyphus/pull/123 \\
        --merged-sha abc1234

    # 直接走 HTTP API（不走 kubectl）
    sisyphus-admin.py --base-url http://sisyphus.43.239.84.24.nip.io --token $TOKEN admin complete REQ-XXX

退出码
======

- 0  全 OK
- 1  参数错 / kubectl 不可用 / endpoint 拒
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_NAMESPACE = "sisyphus"
DEFAULT_PG_POD = "sisyphus-postgresql-0"
DEFAULT_ORCH_DEPLOY = "deploy/orch-sisyphus-orchestrator"
DEFAULT_ORCH_SECRET = "orch-sisyphus-orchestrator"


# ─── kubectl helpers ──────────────────────────────────────────────────────


def _have_kubectl() -> bool:
    try:
        return subprocess.run(
            ["kubectl", "version", "--client=true"],
            capture_output=True,
        ).returncode == 0
    except FileNotFoundError:
        return False


def _pg_query(sql: str, *, namespace: str, pod: str) -> str:
    """通过 kubectl exec PG pod 跑只读 SQL；密码从 pod env 取，不外泄。"""
    if not _have_kubectl():
        raise RuntimeError("kubectl 不可用；本机无集群上下文")
    inner = (
        'PGPASSWORD="$(cat $POSTGRES_PASSWORD_FILE)" '
        "psql -U sisyphus -d sisyphus -t -A"
    )
    cmd = ["kubectl", "-n", namespace, "exec", "-i", pod, "--", "bash", "-c", inner]
    proc = subprocess.run(cmd, input=sql, text=True, capture_output=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr or proc.stdout}")
    return proc.stdout


def _admin_token(namespace: str, secret: str) -> str:
    out = subprocess.check_output([
        "kubectl", "-n", namespace, "get", "secret", secret,
        "-o", "jsonpath={.data.webhook_token}",
    ], text=True)
    return base64.b64decode(out).decode().strip()


def _resolve_token(
    token: str | None,
    *,
    namespace: str,
    secret: str,
    base_url: str | None = None,
) -> str:
    """按优先级：显式传入 > 环境变量 > kubectl secret（仅非 HTTP 模式）。"""
    if token:
        return token
    env_token = os.getenv("SISYPHUS_ADMIN_TOKEN")
    if env_token:
        return env_token
    if base_url:
        raise RuntimeError(
            "HTTP 模式需要提供 token：--token <token> 或环境变量 SISYPHUS_ADMIN_TOKEN"
        )
    return _admin_token(namespace, secret)


def _admin_post(
    path: str,
    body: dict[str, Any] | None,
    *,
    namespace: str,
    deploy: str,
    secret: str,
    base_url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """调 orch admin endpoint。

    模式 A（base_url=None）：从 orch pod 内 curl localhost:8000（避免外网 / 解决 SVC IP 漂移）。
    模式 B（base_url 非空）：直接走外部 HTTP。
    """
    body_json = json.dumps(body or {}, ensure_ascii=False)
    resolved_token = _resolve_token(token, namespace=namespace, secret=secret, base_url=base_url)

    if base_url:
        url = f"{base_url.rstrip('/')}{path}"
        req = urllib.request.Request(
            url,
            data=body_json.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {resolved_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                out = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            out = e.read().decode("utf-8")
            try:
                parsed = json.loads(out)
                raise RuntimeError(f"admin POST {e.code}: {parsed}")
            except json.JSONDecodeError:
                raise RuntimeError(f"admin POST {e.code}: {out}")
    else:
        if not _have_kubectl():
            raise RuntimeError("kubectl 不可用；本机无集群上下文。请用 --base-url 走 HTTP 模式")
        inner = (
            f"curl -sS -X POST -H 'Authorization: Bearer {resolved_token}' "
            f"-H 'content-type: application/json' "
            f"-d {shlex.quote(body_json)} "
            f"http://localhost:8000{path}"
        )
        cmd = ["kubectl", "-n", namespace, "exec", deploy, "--", "bash", "-c", inner]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"admin POST failed: {proc.stderr}")
        out = proc.stdout.strip()

    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out}


# ─── subcommands ───────────────────────────────────────────────────────────


def cmd_req_status(args: argparse.Namespace) -> int:
    if args.req_id:
        sql = (
            "SELECT req_id || '|' || state || '|age=' "
            "|| EXTRACT(EPOCH FROM (NOW()-updated_at))::int "
            f"FROM req_state WHERE req_id='{args.req_id}'; "
            f"SELECT jsonb_pretty(history) FROM req_state WHERE req_id='{args.req_id}';"
        )
    elif args.terminal:
        sql = (
            "SELECT state || '|' || COUNT(*) FROM req_state "
            "GROUP BY state ORDER BY COUNT(*) DESC;"
        )
    else:
        sql = (
            "SELECT req_id || '|' || state || '|age=' "
            "|| EXTRACT(EPOCH FROM (NOW()-updated_at))::int "
            "FROM req_state WHERE state NOT IN ('done','escalated') ORDER BY updated_at;"
        )
    try:
        out = _pg_query(sql, namespace=args.namespace, pod=args.pg_pod)
    except RuntimeError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


def cmd_admin(args: argparse.Namespace) -> int:
    if args.action == "escalate":
        body: dict[str, Any] = {}
        if args.reason:
            body["reason"] = args.reason
        if args.kind:
            body["kind"] = args.kind
        path = f"/admin/req/{args.req_id}/escalate"
    elif args.action == "complete":
        body = {"reason": args.reason} if args.reason else {}
        path = f"/admin/req/{args.req_id}/complete"
    elif args.action == "pr-merged":
        if not args.pr_url:
            print("ERR: --pr-url required for pr-merged", file=sys.stderr)
            return 1
        body = {
            "merged_pr_url": args.pr_url,
            "merged_sha": args.merged_sha or "",
        }
        path = f"/admin/req/{args.req_id}/pr-merged"
    else:
        print(f"ERR: unknown admin action {args.action}", file=sys.stderr)
        return 1
    try:
        resp = _admin_post(
            path, body,
            namespace=args.namespace,
            deploy=args.orch_deploy,
            secret=args.orch_secret,
            base_url=args.base_url,
            token=args.token,
        )
    except RuntimeError as e:
        print(f"ERR: {e}", file=sys.stderr)
        return 1
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    return 0


# ─── parser ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="sisyphus 集群管理 CLI（PG + orch admin endpoint）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE,
                   help=f"K8s namespace (default: {DEFAULT_NAMESPACE})")
    p.add_argument("--base-url", default="",
                   help="orch 外部地址（如 http://sisyphus.43.239.84.24.nip.io），提供时直接走 HTTP 不绕 kubectl")
    p.add_argument("--token", default="",
                   help="Bearer token；留空从环境变量 SISYPHUS_ADMIN_TOKEN 或 kubectl secret 取")
    sub = p.add_subparsers(dest="cmd")

    # req-status
    sp = sub.add_parser("req-status", help="查 PG req_state（默认列 in-flight）")
    sp.add_argument("req_id", nargs="?", help="可选：单 REQ id 看 history")
    sp.add_argument("--terminal", action="store_true", help="按 state 计数")
    sp.add_argument("--pg-pod", default=DEFAULT_PG_POD)
    sp.set_defaults(func=cmd_req_status)

    # admin
    sp = sub.add_parser("admin", help="调 orch admin endpoint")
    sp.add_argument("action", choices=["escalate", "complete", "pr-merged"])
    sp.add_argument("req_id", help="目标 REQ id")
    sp.add_argument("--reason", help="reason 字段（escalate / complete）")
    sp.add_argument("--kind", help="escalate kind（如 sandbox-cleanup / infra-bug）")
    sp.add_argument("--pr-url", help="pr-merged 必需 PR URL")
    sp.add_argument("--merged-sha", help="pr-merged commit sha")
    sp.add_argument("--orch-deploy", default=DEFAULT_ORCH_DEPLOY)
    sp.add_argument("--orch-secret", default=DEFAULT_ORCH_SECRET)
    sp.set_defaults(func=cmd_admin)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
