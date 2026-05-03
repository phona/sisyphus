"""P0-2：startup 时自动检测 prompt / checker / config 变更并写 config_version 表。

逻辑：
  1. git rev-parse HEAD 拿当前 commit SHA
  2. 查 config_version 表的上一次 git_commit（按 applied_at DESC LIMIT 1）
  3. 若不同：git diff --name-only <last> <current> 过滤白名单文件
  4. 白名单内有改动 → INSERT 一行（version_hash=full SHA，changed_files=jsonb，kind 自动推断）
  5. 任何 git / DB 错误只 log warning，不阻断服务启动。

白名单（_WATCHED_PREFIXES）：
  - orchestrator/src/orchestrator/prompts/  （.j2 模板）
  - orchestrator/src/orchestrator/checkers/ （机械 checker）
  - orchestrator/src/orchestrator/config.py （阈值 / feature flags）
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime

import asyncpg
import structlog

log = structlog.get_logger(__name__)

# 相对 repo 根的路径前缀（git diff --name-only 输出的路径）
_WATCHED_PREFIXES: tuple[str, ...] = (
    "orchestrator/src/orchestrator/prompts/",
    "orchestrator/src/orchestrator/checkers/",
    "orchestrator/src/orchestrator/config.py",
)


def _git(*args: str) -> str:
    """同步跑 git 命令；失败抛 CalledProcessError / FileNotFoundError。"""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _is_watched(path: str) -> bool:
    return any(path.startswith(p) or path == p.rstrip("/") for p in _WATCHED_PREFIXES)


def _infer_kind(files: list[str]) -> str:
    has_prompt = any("prompts" in f or f.endswith(".j2") for f in files)
    has_checker = any("checkers" in f for f in files)
    has_config = any(f.endswith("config.py") for f in files)
    kinds = []
    if has_prompt:
        kinds.append("prompt")
    if has_checker:
        kinds.append("checker")
    if has_config:
        kinds.append("config")
    return "+".join(kinds) if kinds else "mixed"


async def maybe_record_config_change(obs_pool: asyncpg.Pool) -> None:
    """startup hook：检测 prompt/checker/config 变更，有则写 config_version 行。

    best-effort — 任何错误只 log，不阻断主服务启动。
    """
    # 1. 拿当前 HEAD SHA
    try:
        current_sha: str = await asyncio.to_thread(_git, "rev-parse", "HEAD")
    except Exception as e:
        log.debug("config_version.git_unavailable", error=str(e))
        return

    # 2. 查上次已记录的 commit
    try:
        last_sha: str | None = await obs_pool.fetchval(
            "SELECT git_commit FROM config_version ORDER BY applied_at DESC LIMIT 1"
        )
    except Exception as e:
        log.warning("config_version.query_failed", error=str(e))
        return

    if last_sha == current_sha:
        log.debug("config_version.up_to_date", sha=current_sha[:8])
        return

    # 3. 算变更文件列表
    if last_sha:
        try:
            diff_out: str = await asyncio.to_thread(
                _git, "diff", "--name-only", last_sha, current_sha
            )
            all_changed = [f for f in diff_out.splitlines() if f]
        except Exception as e:
            log.warning("config_version.git_diff_failed",
                        last=last_sha[:8], cur=current_sha[:8], error=str(e))
            return
        watched = [f for f in all_changed if _is_watched(f)]
        if not watched:
            log.debug("config_version.no_watched_files_changed",
                      sha=current_sha[:8], total_changed=len(all_changed))
            return
    else:
        # 第一次写入：没有历史 commit 可 diff，直接记录当前 SHA
        watched = ["<initial-record>"]

    # 4. INSERT（version_hash=full SHA，ON CONFLICT DO NOTHING 保幂等）
    kind = _infer_kind(watched)
    diff_summary = f"{len(watched)} file(s) changed"
    try:
        await obs_pool.execute(
            """
            INSERT INTO config_version
                (version_hash, kind, target, applied_at,
                 git_commit, diff_summary, changed_files, author)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
            ON CONFLICT (version_hash) DO NOTHING
            """,
            current_sha,
            kind,
            None,
            datetime.now(UTC),
            current_sha,
            diff_summary,
            json.dumps(watched),
            "orchestrator-startup",
        )
        log.info("config_version.recorded",
                 sha=current_sha[:8], kind=kind, files=watched[:20])
    except Exception as e:
        log.warning("config_version.insert_failed",
                    sha=current_sha[:8], error=str(e))
