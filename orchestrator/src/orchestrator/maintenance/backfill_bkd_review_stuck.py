"""一次性 backfill：把卡在 BKD `statusId='review'` 的 sub-agent issue 推到 'done'。

REQ: REQ-bkd-cleanup-historical-review-1777222384
Spec: openspec/changes/REQ-bkd-cleanup-historical-review-1777222384/specs/bkd-status-backfill/

跑法（dry-run 默认）：

    cd orchestrator
    uv run python -m orchestrator.maintenance.backfill_bkd_review_stuck \\
        --project nnvxh8wj --bkd-base-url http://localhost:3000

加 --apply 才真发 PATCH。

stdout 是 machine-readable JSON lines（每条候选一行），stderr 是人读 log。
operator 可 `2>/dev/null | jq -s .` 收 audit。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import httpx

# 业务能识别的 sub-agent role tag（intake 不在此列：intake 跑在 user 创的 intent
# issue 上，那条 issue 的 statusId 反映用户意图，不该被本脚本动）
_ROLE_TAGS = frozenset(
    {"verifier", "fixer", "analyze", "challenger", "accept-agent", "done-archive"}
)


def _first_role_tag(tags: list[str]) -> str | None:
    """返回 issue 上第一个 _ROLE_TAGS 命中；都没命中返 None。"""
    for t in tags or []:
        if t in _ROLE_TAGS:
            return t
    return None


def _has_req_tag(tags: list[str]) -> bool:
    return any((t or "").startswith("REQ-") for t in tags or [])


def _extract_req_tag(tags: list[str]) -> str | None:
    for t in tags or []:
        if (t or "").startswith("REQ-"):
            return t
    return None


def is_safe_target(issue: dict[str, Any]) -> tuple[bool, str]:
    """单条 issue 决策：能不能 PATCH 到 done。

    返回 (selected, decision_reason)。selected=False 时 reason 说明被跳的原因；
    selected=True 时 reason 写 role/session 用作 audit。
    """
    if issue.get("statusId") != "review":
        return False, "not-review"
    if issue.get("sessionStatus") == "running":
        return False, "session-running"
    role = _first_role_tag(issue.get("tags") or [])
    if role is None:
        return False, "no-role-tag"
    if not _has_req_tag(issue.get("tags") or []):
        return False, "no-req-tag"
    return True, f"role={role};session={issue.get('sessionStatus') or '-'}"


def select_targets(
    issues: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    """对全 issue 列表过滤；返回 [(issue, reason)] 仅含 selected=True 的项。"""
    out: list[tuple[dict[str, Any], str]] = []
    for it in issues:
        ok, reason = is_safe_target(it)
        if ok:
            out.append((it, reason))
    return out


def _audit_line(
    *,
    issue: dict[str, Any],
    action: str,
    reason: str,
) -> dict[str, Any]:
    """单条 stdout JSON line 的 shape（spec contract.spec.yaml 落定）。"""
    tags = issue.get("tags") or []
    return {
        "issue_id": issue.get("id"),
        "req_id": _extract_req_tag(tags),
        "role": _first_role_tag(tags),
        "sessionStatus": issue.get("sessionStatus"),
        "action": action,
        "reason": reason,
    }


async def _list_issues(
    client: httpx.AsyncClient,
    base_url: str,
    project_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """调 BKD `GET /api/projects/{pid}/issues?limit=N`；返 list of dict。"""
    url = f"{base_url.rstrip('/')}/api/projects/{project_id}/issues"
    r = await client.get(url, params={"limit": limit})
    r.raise_for_status()
    payload = r.json()
    # BKD envelope: {success, data: [...]} or {success, data: {items: [...]}}
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise RuntimeError(f"unexpected list-issues response shape: {type(data).__name__}")
    return data


async def _patch_status_done(
    client: httpx.AsyncClient,
    base_url: str,
    project_id: str,
    issue_id: str,
) -> None:
    """PATCH 单条 issue statusId='done'；body **不**带 tags（避免 BKD replace 语义抹历史 tag）。"""
    url = f"{base_url.rstrip('/')}/api/projects/{project_id}/issues/{issue_id}"
    r = await client.patch(url, json={"statusId": "done"})
    r.raise_for_status()


async def run(
    *,
    project_id: str,
    bkd_base_url: str,
    apply: bool,
    limit: int = 2000,
    out=sys.stdout,
    err=sys.stderr,
) -> int:
    """主流程；返 exit code（0=成功，非 0=全失败）。

    out / err 可注入用于测试。logging 走 stderr（人读），audit 走 stdout（JSON-lines）。
    """
    print(
        f"[backfill] project={project_id} base_url={bkd_base_url} "
        f"apply={apply} limit={limit}",
        file=err,
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            issues = await _list_issues(client, bkd_base_url, project_id, limit)
        except (httpx.HTTPError, RuntimeError, ValueError) as e:
            print(f"[backfill] list-issues failed: {e}", file=err)
            return 2

        targets = select_targets(issues)
        print(
            f"[backfill] scanned={len(issues)} candidates={len(targets)}",
            file=err,
        )

        if not apply:
            for it, reason in targets:
                line = _audit_line(issue=it, action="skipped", reason=reason)
                print(json.dumps(line, ensure_ascii=False), file=out)
            return 0

        ok = 0
        fail = 0
        for it, reason in targets:
            issue_id = it.get("id")
            try:
                await _patch_status_done(
                    client, bkd_base_url, project_id, issue_id,
                )
            except (httpx.HTTPError, ValueError) as e:
                fail += 1
                line = _audit_line(
                    issue=it,
                    action="failed",
                    reason=f"http-error: {e}",
                )
                print(json.dumps(line, ensure_ascii=False), file=out)
                print(
                    f"[backfill] PATCH {issue_id} failed: {e}",
                    file=err,
                )
                continue
            ok += 1
            line = _audit_line(issue=it, action="patched", reason=reason)
            print(json.dumps(line, ensure_ascii=False), file=out)

        print(f"[backfill] done patched={ok} failed={fail}", file=err)
        # exit 0 iff at least one success（spec 合约：partial failure 不阻塞 caller）
        if ok == 0 and fail > 0:
            return 1
        return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="backfill_bkd_review_stuck",
        description="One-shot backfill: PATCH BKD review-stuck sub-issues to statusId=done.",
    )
    p.add_argument(
        "--project",
        required=True,
        help="BKD project alias (e.g. nnvxh8wj)",
    )
    p.add_argument(
        "--bkd-base-url",
        default="http://localhost:3000",
        help="BKD REST base URL",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually PATCH (default: dry-run)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="BKD list-issues page size (default 2000, single-shot)",
    )
    args = p.parse_args(argv)

    return asyncio.run(
        run(
            project_id=args.project,
            bkd_base_url=args.bkd_base_url,
            apply=args.apply,
            limit=args.limit,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
