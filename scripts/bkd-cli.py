#!/usr/bin/env python3
"""BKD REST API CLI —— 通用 BKD 客户端，跟具体 orchestrator 解耦。

涵盖**纯 BKD REST 操作**，可在任何 BKD 实例上用（不依赖 sisyphus 集群 / PG / admin endpoint）。
sisyphus 特定的 PG 查询 / orch admin endpoint 操作请见同目录 ``sisyphus-admin.py``。

子命令一览：

  inline           单条派单（POST → follow-up → PATCH statusId=working / intent tag）
  yaml             批量 YAML 派单
  list             列项目下 issues
  trigger-existing 给已建 issue 补 intent tag（PATCH 触发订阅 issue.updated 的 orchestrator）
  close            批量 PATCH issue statusId=done（清 review 队列等批量收尾）
  logs             获取 issue 的完整操作日志（agent messages / tool calls / thinking）

历史用法兼容：``python bkd-cli.py example-reqs.yaml`` 自动转 ``yaml example-reqs.yaml``。

设计要点
========

1. **intent tag 必须创建后 PATCH**（不要 POST 就带）。BKD 创建 issue 只发 issue.created；
   订阅 issue.updated 的 orchestrator（如 sisyphus）只能通过 PATCH 触发的 issue.updated
   webhook 接管。POST 即带 intent → 永远收不到。

2. **派单两条路径**：
   - YAML 模式（``yaml`` / 历史默认）：POST 不带 intent → follow-up → PATCH 加 intent tag。
     statusId 全程保持 ``todo``，由 orchestrator 通过 intent webhook 接管。
   - inline 模式（``inline``）：POST 不带 intent → follow-up → PATCH 加 intent tag +
     ``statusId=working``。让 BKD spawn agent 直接跑，orchestrator 通过 issue.updated 接管。

3. **退出码**：0 全 OK / dry-run 通过；1 参数错误 / 部分失败。

Examples
========

::

    # 单条派
    bkd-cli.py inline \\
        --slug fix-foo-bar \\
        --title "fix(bar): foo 抖动" \\
        --prompt-file /tmp/prompt.md

    # 列 review 队列
    bkd-cli.py list --status review

    # 批关 child issue
    bkd-cli.py close uk6ajzoh,t9xgf1hf,pijcf32t

    # 给已建 issue 补 intent:intake 触发
    bkd-cli.py trigger-existing weatd05d --intent intake

    # 查看 issue 的 agent 操作日志（排查零产出等）
    bkd-cli.py logs weatd05d --filter tool-use,assistant-message --truncate 300

    # 批量 YAML 派
    bkd-cli.py yaml example-reqs.yaml --trigger
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "http://localhost:3000/api"
DEFAULT_PROJECT = "nnvxh8wj"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_ENGINE_TYPE = "claude-code"
SLUG_TS_LIMIT = 46  # tag 整体 ≤ 50；REQ-<slug> 后 4 字 prefix


# ─── BKD REST helpers ──────────────────────────────────────────────────────


def _req(method: str, url: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """发 HTTP 到 BKD，返回解析后 dict。"""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    r = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            raise RuntimeError(f"HTTP {e.code}: {e.reason}") from e


def list_issues(base_url: str, project_id: str, limit: int = 200) -> list[dict[str, Any]]:
    resp = _req("GET", f"{base_url}/projects/{project_id}/issues?limit={limit}")
    return resp.get("data", []) if resp.get("success") else []


def get_issue(base_url: str, project_id: str, issue_id: str) -> dict[str, Any]:
    resp = _req("GET", f"{base_url}/projects/{project_id}/issues/{issue_id}")
    if not resp.get("success"):
        raise RuntimeError(resp.get("error", "unknown"))
    return resp["data"]


def create_issue(
    base_url: str,
    project_id: str,
    title: str,
    description: str,
    tags: list[str],
    *,
    status_id: str = "todo",
    use_worktree: bool = True,
    engine_type: str = DEFAULT_ENGINE_TYPE,
    model: str = DEFAULT_MODEL,
) -> dict[str, Any]:
    body = {
        "title": title,
        "description": description,
        "statusId": status_id,
        "tags": tags,
        "useWorktree": use_worktree,
        "engineType": engine_type,
        "model": model,
    }
    resp = _req("POST", f"{base_url}/projects/{project_id}/issues", body)
    if not resp.get("success"):
        raise RuntimeError(resp.get("error", "unknown"))
    return resp["data"]


def follow_up(base_url: str, project_id: str, issue_id: str, prompt: str) -> dict[str, Any]:
    return _req(
        "POST",
        f"{base_url}/projects/{project_id}/issues/{issue_id}/follow-up",
        {"prompt": prompt},
    )


def patch_issue(
    base_url: str,
    project_id: str,
    issue_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    return _req(
        "PATCH",
        f"{base_url}/projects/{project_id}/issues/{issue_id}",
        patch,
    )


def get_logs(base_url: str, project_id: str, issue_id: str) -> list[dict[str, Any]]:
    """获取 issue 的完整操作日志（agent messages / tool calls / thinking）。"""
    resp = _req("GET", f"{base_url}/projects/{project_id}/issues/{issue_id}/logs")
    if not resp.get("success"):
        raise RuntimeError(resp.get("error", "unknown"))
    return resp.get("data", {}).get("logs", [])


# ─── subcommands ───────────────────────────────────────────────────────────


def cmd_inline(args: argparse.Namespace) -> int:
    """单条派 REQ：POST → follow-up → PATCH 转 working + intent tag。"""
    ts = int(time.time())
    slug = f"{args.slug}-{ts}" if not args.slug.endswith(f"-{ts}") else args.slug
    if len(slug) > SLUG_TS_LIMIT:
        print(f"ERR: slug too long ({len(slug)} > {SLUG_TS_LIMIT}); 缩短 slug", file=sys.stderr)
        return 1
    intent_tag = f"intent:{args.intent}"
    title = f"[REQ-{slug}] {args.title}"
    base_tags = [f"REQ-{slug}", *args.tag]
    final_tags = [intent_tag, *base_tags]

    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
    elif args.prompt:
        prompt = args.prompt
    else:
        prompt = args.title

    # Step 1: POST （不带 intent；statusId=todo）
    issue = create_issue(
        args.base_url, args.project, title, args.description or args.title,
        base_tags, status_id="todo",
        engine_type=args.engine_type, model=args.model,
        use_worktree=not args.no_worktree,
    )
    issue_id = issue["id"]
    print(f"CREATE: id={issue_id} num={issue.get('issueNumber')} slug={slug}")

    # Step 2: follow-up
    fu = follow_up(args.base_url, args.project, issue_id, prompt)
    if fu.get("success"):
        print(f"  follow-up OK ({len(prompt)} chars)")
    else:
        print(f"  follow-up FAIL: {fu.get('error')}", file=sys.stderr)
        return 1

    # Step 3: PATCH 加 intent tag + 可选 statusId=working
    patch_body: dict[str, Any] = {"tags": final_tags}
    if args.activate:
        patch_body["statusId"] = "working"
    p = patch_issue(args.base_url, args.project, issue_id, patch_body)
    if p.get("success"):
        d = p.get("data", {})
        print(f"  trigger OK: status={d.get('statusId')} session={d.get('sessionStatus')}")
    else:
        print(f"  trigger FAIL: {p.get('error')}", file=sys.stderr)
        return 1
    return 0


def cmd_yaml(args: argparse.Namespace) -> int:
    """批量 YAML 派单。"""
    try:
        import yaml
    except ImportError:
        print("ERR: PyYAML required (pip install pyyaml)", file=sys.stderr)
        return 1
    with open(args.file, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    project_id = args.project or cfg.get("project", DEFAULT_PROJECT)
    base_url = args.base_url or cfg.get("base_url", DEFAULT_BASE_URL)

    existing = {
        i["title"].split("]")[0] if "[" in i["title"] else i["title"]
        for i in list_issues(base_url, project_id)
    }

    definitions = cfg.get("issues", [])
    if not definitions:
        print("ERR: no 'issues' in YAML", file=sys.stderr)
        return 1

    created: list[dict[str, Any]] = []
    for idx, d in enumerate(definitions, 1):
        title = d["title"]
        desc = d.get("description", "")
        prompt = d.get("prompt") or desc
        tags = list(d.get("tags", []))
        if d.get("priority"):
            tags.append(d["priority"])

        should_trigger = bool(args.trigger or d.get("trigger"))
        intent_tag = f"intent:{d.get('intent', args.intent)}" if should_trigger else None

        if title in existing:
            print(f"SKIP [{idx}]: dup — {title[:60]}")
            continue
        if args.dry_run:
            print(f"DRY  [{idx}]: would create '{title[:60]}' tags={tags} trigger={intent_tag}")
            continue

        try:
            issue = create_issue(base_url, project_id, title, desc, tags)
            iid = issue["id"]
            print(f"CREATE [{idx}]: {iid} | {title[:60]}")
            fu = follow_up(base_url, project_id, iid, prompt)
            if not fu.get("success"):
                print(f"  follow-up FAIL: {fu.get('error')}")
            if intent_tag:
                trigger_tags = tags + [intent_tag]
                up = patch_issue(base_url, project_id, iid, {"tags": trigger_tags})
                if up.get("success"):
                    print(f"  trigger OK ({intent_tag})")
                else:
                    print(f"  trigger FAIL: {up.get('error')}")
                tags = trigger_tags
            created.append({"id": iid, "title": title, "tags": tags})
        except Exception as e:
            print(f"FAIL [{idx}]: {title[:60]} | {e}")

    print(f"\nDone. Created {len(created)}/{len(definitions)}.")
    return 0 if created else 1


def cmd_list(args: argparse.Namespace) -> int:
    issues = list_issues(args.base_url, args.project, limit=args.limit)
    if args.status:
        issues = [i for i in issues if i.get("statusId") == args.status]
    print(f"Project={args.project} | matched={len(issues)} (status={args.status or '*'})")
    for i in issues:
        print(
            f"  {i['id']} | {i.get('statusId','?'):<8} | {i.get('sessionStatus','-'):<10} "
            f"| {i['title'][:90]}"
        )
    return 0


def cmd_trigger_existing(args: argparse.Namespace) -> int:
    intent_tag = f"intent:{args.intent}"
    ids = [s.strip() for s in args.issue_ids.split(",") if s.strip()]
    print(f"trigger {len(ids)} issues with {intent_tag}...")
    for iid in ids:
        try:
            cur = get_issue(args.base_url, args.project, iid)
        except Exception as e:
            print(f"GET FAIL: {iid} | {e}")
            continue
        cur_tags = list(cur.get("tags") or [])
        if intent_tag in cur_tags:
            print(f"SKIP: {iid} 已有 {intent_tag}")
            continue
        new_tags = cur_tags + [intent_tag]
        up = patch_issue(args.base_url, args.project, iid, {"tags": new_tags})
        if up.get("success"):
            print(f"OK: {iid} | {cur['title'][:60]}")
        else:
            print(f"FAIL: {iid} | {up.get('error')}")
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    ids = [s.strip() for s in args.issue_ids.split(",") if s.strip()]
    if not ids:
        print("ERR: no issue ids", file=sys.stderr)
        return 1
    print(f"close {len(ids)} issues to status={args.to}...")
    ok = 0
    fail = 0
    for iid in ids:
        try:
            up = patch_issue(args.base_url, args.project, iid, {"statusId": args.to})
            if up.get("success"):
                print(f"OK: {iid}")
                ok += 1
            else:
                print(f"FAIL: {iid} | {up.get('error')}")
                fail += 1
        except Exception as e:
            print(f"FAIL: {iid} | {e}")
            fail += 1
    print(f"\ndone: ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


def cmd_logs(args: argparse.Namespace) -> int:
    """获取 issue 的完整操作日志（agent messages / tool calls / thinking）。"""
    try:
        logs = get_logs(args.base_url, args.project, args.issue_id)
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print(f"Issue={args.issue_id} | total logs={len(logs)}")
    if not logs:
        return 0

    for i, log in enumerate(logs):
        entry_type = log.get("entryType", "unknown")
        ts = log.get("timestamp", "")
        content = log.get("content", "")

        if args.filter and entry_type not in args.filter.split(","):
            continue

        # 截断长内容
        display = content.replace("\n", " ")
        if len(display) > args.truncate:
            display = display[: args.truncate] + "..."

        print(f"\n[{i:3d}] [{entry_type:20s}] {ts}")
        print(f"      {display}")

    return 0


# ─── parser ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="BKD REST API CLI（通用 BKD 客户端）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--base-url", default=DEFAULT_BASE_URL,
                   help=f"BKD API base (default: {DEFAULT_BASE_URL})")
    p.add_argument("--project", default=DEFAULT_PROJECT,
                   help=f"BKD project (default: {DEFAULT_PROJECT})")

    sub = p.add_subparsers(dest="cmd")

    # inline
    sp = sub.add_parser("inline", help="单条 REQ 派单（POST→follow-up→PATCH working）")
    sp.add_argument("--slug", required=True, help="REQ slug 段（不含 -ts；ts 自动追加）")
    sp.add_argument("--title", required=True, help="一行短标题（agent 看的入口）")
    sp.add_argument("--description", help="BKD UI 看板上的描述（可选；缺省=title）")
    sp.add_argument("--prompt-file", help="详细 prompt markdown 文件（推荐）")
    sp.add_argument("--prompt", help="详细 prompt 字面字符串（短场景；与 --prompt-file 互斥）")
    sp.add_argument("--intent", choices=["intake", "analyze"], default="analyze")
    sp.add_argument("--tag", action="append", default=[], help="额外 tag（可多次）")
    sp.add_argument("--engine-type", default=DEFAULT_ENGINE_TYPE)
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.add_argument("--no-worktree", action="store_true", help="useWorktree=False")
    sp.add_argument("--no-activate", dest="activate", action="store_false",
                    help="不 PATCH statusId=working（让 orchestrator 自取）")
    sp.set_defaults(activate=True, func=cmd_inline)

    # yaml
    sp = sub.add_parser("yaml", help="批量 YAML 派单")
    sp.add_argument("file", help="issues YAML 文件")
    sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--trigger", action="store_true",
                    help="创建后 PATCH intent tag 触发 orchestrator")
    sp.add_argument("--intent", choices=["intake", "analyze"], default="intake")
    sp.set_defaults(func=cmd_yaml)

    # list
    sp = sub.add_parser("list", help="列项目 issues")
    sp.add_argument("--limit", type=int, default=200)
    sp.add_argument("--status", help="按 statusId 过滤（todo/working/review/done）")
    sp.set_defaults(func=cmd_list)

    # trigger-existing
    sp = sub.add_parser("trigger-existing", help="给现有 issue 补 intent tag 触发")
    sp.add_argument("issue_ids", help="逗号分隔 issue ids")
    sp.add_argument("--intent", choices=["intake", "analyze"], default="intake")
    sp.set_defaults(func=cmd_trigger_existing)

    # close
    sp = sub.add_parser("close", help="批量 PATCH issue statusId（默认 done）")
    sp.add_argument("issue_ids", help="逗号分隔 issue ids")
    sp.add_argument("--to", default="done", choices=["done", "review", "todo", "working"])
    sp.set_defaults(func=cmd_close)

    # logs
    sp = sub.add_parser("logs", help="获取 issue 的完整操作日志（agent / tool / thinking）")
    sp.add_argument("issue_id", help="BKD issue id")
    sp.add_argument("--filter", help="按 entryType 过滤，逗号分隔（如 tool-use,assistant-message）")
    sp.add_argument("--truncate", type=int, default=200,
                    help="单条内容截断长度（默认 200）")
    sp.set_defaults(func=cmd_logs)

    return p


def main() -> int:
    parser = build_parser()
    # 历史兼容：第一个位置参数是 .yaml → 自动转 yaml 子命令
    argv = sys.argv[1:]
    if argv and not argv[0].startswith("-") and argv[0].endswith((".yaml", ".yml")):
        argv = ["yaml", *argv]
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
