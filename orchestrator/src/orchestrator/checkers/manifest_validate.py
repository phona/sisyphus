"""manifest 自检（M3）：sisyphus 把 PVC 上的 manifest.yaml 拉回来用 jsonschema 验。

analyze-agent 完成后，sisyphus 是唯一裁判：能不能进 fanout_specs 不靠 agent tag，
靠这里 admission gate emit 的事件。

设计要点：
- jsonschema draft-07 验结构（required / type / pattern / enum）
- jsonschema 表达不了的跨字段约束（"sources 里恰好 1 个 leader"、"repo 不重复"）
  在本模块手写，跟 schema 一起组成完整规则
- 行为与 scripts/validate-manifest.py 对齐，避免 runner 内 / orchestrator 侧两套口径
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable
from importlib import resources
from typing import Any

import structlog
import yaml
from jsonschema import Draft7Validator

from .. import k8s_runner
from ..config import settings
from ._types import CheckResult

log = structlog.get_logger(__name__)

_TAIL = 2048
_MANIFEST_PATH = "/workspace/.sisyphus/manifest.yaml"
_READ_CMD = f"cat {_MANIFEST_PATH}"

# 语义 reason：CheckResult.reason 可选值
REASON_OPEN_QUESTIONS_PENDING = "open_questions_pending"

_validator: Draft7Validator | None = None


def _get_validator() -> Draft7Validator:
    global _validator
    if _validator is None:
        raw = resources.files("orchestrator.schemas").joinpath("manifest.json").read_text()
        _validator = Draft7Validator(json.loads(raw))
    return _validator


def _format_error_path(path: Iterable[Any]) -> str:
    """jsonschema deque 路径 → 'sources[0].repo' 形式。"""
    out: list[str] = []
    for p in path:
        if isinstance(p, int):
            out.append(f"[{p}]")
        else:
            out.append(f".{p}" if out else str(p))
    return "".join(out) or "<root>"


def _collect_errors(manifest: Any) -> list[str]:
    """跑 jsonschema + 跨字段约束。返回中文错误列表（空 = 合法）。"""
    if not isinstance(manifest, dict):
        return ["manifest 根必须是 object / dict"]

    errs: list[str] = []
    for err in sorted(_get_validator().iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        loc = _format_error_path(err.absolute_path)
        errs.append(f"{loc}: {err.message}")

    # jsonschema draft-07 表达不了：sources 必须正好 1 个 leader、repo 不重复
    sources = manifest.get("sources")
    if isinstance(sources, list) and sources:
        leaders = sum(
            1 for s in sources
            if isinstance(s, dict) and s.get("role") == "leader"
        )
        if leaders != 1:
            errs.append(f"sources 必须正好有 1 个 role=leader，实际 {leaders} 个")

        seen: set[str] = set()
        for s in sources:
            if not isinstance(s, dict):
                continue
            repo = s.get("repo")
            if not isinstance(repo, str):
                continue
            if repo in seen:
                errs.append(f"sources 里 repo {repo!r} 重复出现")
            else:
                seen.add(repo)

    return errs


async def run_manifest_validate(req_id: str, *, timeout_sec: int = 30) -> CheckResult:
    """kubectl exec runner cat manifest.yaml → jsonschema validate → CheckResult。

    所有失败原因（pod 不可达 / cat 返非 0 / yaml 解析挂 / schema 不符）
    都收成 passed=False，stderr_tail 带原因。engine 拿 passed 决策即可。
    """
    rc = k8s_runner.get_controller()
    started = time.monotonic()

    try:
        exec_result = await asyncio.wait_for(
            rc.exec_in_runner(req_id, _READ_CMD, timeout_sec=timeout_sec),
            timeout=timeout_sec + 10,
        )
    except TimeoutError:
        log.error("checker.manifest_validate.read_timeout", req_id=req_id)
        return CheckResult(
            passed=False, exit_code=-1,
            stdout_tail="", stderr_tail=f"read timeout after {timeout_sec}s",
            duration_sec=time.monotonic() - started, cmd=_READ_CMD,
        )

    if exec_result.exit_code != 0:
        log.warning(
            "checker.manifest_validate.read_failed",
            req_id=req_id, exit_code=exec_result.exit_code,
        )
        return CheckResult(
            passed=False, exit_code=exec_result.exit_code,
            stdout_tail=exec_result.stdout[-_TAIL:],
            stderr_tail=(exec_result.stderr or f"cat {_MANIFEST_PATH} exit {exec_result.exit_code}")[-_TAIL:],
            duration_sec=exec_result.duration_sec, cmd=_READ_CMD,
        )

    try:
        manifest = yaml.safe_load(exec_result.stdout)
    except yaml.YAMLError as e:
        log.warning("checker.manifest_validate.yaml_error", req_id=req_id, error=str(e))
        return CheckResult(
            passed=False, exit_code=2,
            stdout_tail=exec_result.stdout[-_TAIL:],
            stderr_tail=f"YAML 解析失败: {e}"[-_TAIL:],
            duration_sec=exec_result.duration_sec, cmd=_READ_CMD,
        )

    errs = _collect_errors(manifest)
    duration = time.monotonic() - started
    if errs:
        log.info(
            "checker.manifest_validate.invalid",
            req_id=req_id, error_count=len(errs),
        )
        joined = "\n".join(f"  - {e}" for e in errs)
        return CheckResult(
            passed=False, exit_code=1,
            stdout_tail=exec_result.stdout[-_TAIL:],
            stderr_tail=f"manifest 验证失败 ({len(errs)} 项):\n{joined}"[-_TAIL:],
            duration_sec=duration, cmd=_READ_CMD,
        )

    # M6 ambiguity admission：schema 过了才看 open_questions。
    # 非空 → 歧义未清 → 路由 pending-human（区别于 schema-fail 的 bugfix 重做）。
    # 由 settings.admission_analyze_pending_questions 灰度控制。
    if settings.admission_analyze_pending_questions:
        questions = manifest.get("open_questions") or []
        if isinstance(questions, list) and questions:
            joined = "\n".join(f"  - {q}" for q in questions if isinstance(q, str))
            log.info(
                "checker.manifest_validate.pending_human",
                req_id=req_id, count=len(questions),
            )
            return CheckResult(
                passed=False, exit_code=3,
                stdout_tail=exec_result.stdout[-_TAIL:],
                stderr_tail=(
                    f"{REASON_OPEN_QUESTIONS_PENDING}: {len(questions)} 项\n{joined}"
                )[-_TAIL:],
                duration_sec=duration, cmd=_READ_CMD,
                reason=REASON_OPEN_QUESTIONS_PENDING,
            )

    log.info("checker.manifest_validate.ok", req_id=req_id, duration_sec=round(duration, 2))
    return CheckResult(
        passed=True, exit_code=0,
        stdout_tail=exec_result.stdout[-_TAIL:],
        stderr_tail="", duration_sec=duration, cmd=_READ_CMD,
    )
