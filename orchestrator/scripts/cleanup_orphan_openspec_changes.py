#!/usr/bin/env python3
"""One-time script: clean up orphan openspec/changes/REQ-*/ dirs from main.

Usage (from repo root):
    # dry-run (default): list what would be deleted
    python orchestrator/scripts/cleanup_orphan_openspec_changes.py

    # apply: git rm + commit per dir (does NOT push)
    python orchestrator/scripts/cleanup_orphan_openspec_changes.py --apply

    # custom repo root or PG URL
    python orchestrator/scripts/cleanup_orphan_openspec_changes.py \
        --repo-root /path/to/repo \
        --pg-url postgresql://user:pass@host/db
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class DirStatus(NamedTuple):
    dir_name: str
    state: str   # pg state value, "not_found", or "in_flight"
    action: str  # "delete" or "keep"


async def _query_states(pg_url: str, req_ids: list[str]) -> dict[str, str]:
    """Query PG req_state table for each req_id. Returns {req_id: state_str}."""
    try:
        import asyncpg
    except ImportError:
        print("ERROR: asyncpg not installed. Install with: pip install asyncpg", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(pg_url)
    try:
        rows = await conn.fetch(
            "SELECT req_id, state FROM req_state WHERE req_id = ANY($1)",
            req_ids,
        )
        return {row["req_id"]: row["state"] for row in rows}
    finally:
        await conn.close()


_TERMINAL_STATES = {"done", "escalated"}
_SKIP_DIRS = {"archive", "_superseded"}


def _collect_req_dirs(repo_root: Path) -> list[str]:
    changes_dir = repo_root / "openspec" / "changes"
    if not changes_dir.is_dir():
        print(f"ERROR: {changes_dir} not found", file=sys.stderr)
        sys.exit(1)
    return [
        d.name
        for d in sorted(changes_dir.iterdir())
        if d.is_dir() and d.name.startswith("REQ-") and d.name not in _SKIP_DIRS
    ]


async def _classify(req_dirs: list[str], pg_url: str) -> list[DirStatus]:
    states = await _query_states(pg_url, req_dirs)
    result: list[DirStatus] = []
    for name in req_dirs:
        pg_state = states.get(name)
        if pg_state is None:
            status = DirStatus(name, "not_found", "delete")
        elif pg_state in _TERMINAL_STATES:
            status = DirStatus(name, pg_state, "delete")
        else:
            status = DirStatus(name, pg_state, "keep")
        result.append(status)
    return result


def _git_rm_and_commit(repo_root: Path, dir_name: str, state: str) -> None:
    changes_path = Path("openspec") / "changes" / dir_name
    subprocess.run(
        ["git", "rm", "-rf", str(changes_path)],
        cwd=repo_root, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m",
         f"chore(openspec): cleanup orphan {dir_name} from {state}"],
        cwd=repo_root, check=True,
    )
    print(f"  [committed] rm {changes_path} (state={state})")


def _print_table(statuses: list[DirStatus]) -> None:
    deletes = [s for s in statuses if s.action == "delete"]
    keeps = [s for s in statuses if s.action == "keep"]
    print(f"\n{'DIR':60s}  {'PG STATE':12s}  ACTION")
    print("-" * 85)
    for s in statuses:
        print(f"  {s.dir_name:58s}  {s.state:12s}  {s.action}")
    print(f"\nSummary: {len(deletes)} to delete, {len(keeps)} to keep")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", default=".", help="Path to repo root (default: CWD)")
    parser.add_argument("--pg-url", default=os.environ.get("DATABASE_URL", ""),
                        help="PostgreSQL URL (default: $DATABASE_URL)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually git rm + commit (does NOT push)")
    args = parser.parse_args()

    if not args.pg_url:
        print("ERROR: --pg-url or DATABASE_URL required", file=sys.stderr)
        sys.exit(1)

    repo_root = Path(args.repo_root).resolve()
    req_dirs = _collect_req_dirs(repo_root)
    if not req_dirs:
        print("No REQ-* directories found in openspec/changes/")
        return

    print(f"Found {len(req_dirs)} REQ-* dirs under openspec/changes/")
    statuses = await _classify(req_dirs, args.pg_url)
    _print_table(statuses)

    to_delete = [s for s in statuses if s.action == "delete"]
    if not to_delete:
        print("\nNothing to delete.")
        return

    if not args.apply:
        print("\nDry-run only. Pass --apply to commit deletions (no push).")
        return

    print(f"\nApplying: deleting {len(to_delete)} dirs...")
    for s in to_delete:
        print(f"  rm openspec/changes/{s.dir_name} (state={s.state})")
        try:
            _git_rm_and_commit(repo_root, s.dir_name, s.state)
        except subprocess.CalledProcessError as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            print("  Stopping. Review state and re-run.", file=sys.stderr)
            sys.exit(1)

    print(f"\nDone. {len(to_delete)} dirs removed and committed. Review then push manually.")


if __name__ == "__main__":
    asyncio.run(main())
