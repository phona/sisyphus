#!/usr/bin/env python3
"""验证 /workspace/.sisyphus/manifest.yaml 结构合法。

烘到 runner image 的 /opt/sisyphus/scripts/，每个 stage agent 起手调：
    validate-manifest.py /workspace/.sisyphus/manifest.yaml

合法 exit 0，不合法 exit 非 0 + stderr 列出所有问题。

schema 故意做得严：字段名 / 类型 / 枚举值对不上都拒，早暴露 agent 生成错的 manifest。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:
    print("FATAL: PyYAML 未装；烘 runner image 时需 pip install pyyaml", file=sys.stderr)
    sys.exit(2)


SCHEMA_VERSION = 1
REQ_ID_RE = re.compile(r"^REQ-[\w-]+$")
REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")   # owner/repo
ROLE_VALUES = {"leader", "source"}
BRANCH_PREFIX = "stage/"


def _collect_errors(manifest: dict[str, Any]) -> list[str]:
    errs: list[str] = []

    # top-level
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errs.append(
            f"schema_version 必须是 {SCHEMA_VERSION}，实际是 {manifest.get('schema_version')}"
        )

    req_id = manifest.get("req_id")
    if not isinstance(req_id, str) or not REQ_ID_RE.match(req_id):
        errs.append(f"req_id 必须是 REQ-* 形式，实际 {req_id!r}")

    # sources
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        errs.append("sources 必须是非空 list")
    else:
        leader_count = 0
        repo_names: set[str] = set()
        for i, src in enumerate(sources):
            if not isinstance(src, dict):
                errs.append(f"sources[{i}] 必须是 object")
                continue
            repo = src.get("repo")
            if not isinstance(repo, str) or not REPO_RE.match(repo):
                errs.append(f"sources[{i}].repo 必须是 owner/repo 形式，实际 {repo!r}")
            elif repo in repo_names:
                errs.append(f"sources 里 repo {repo!r} 重复出现")
            else:
                repo_names.add(repo)

            path = src.get("path")
            if not isinstance(path, str) or not path.startswith("source/"):
                errs.append(f"sources[{i}].path 必须以 source/ 开头，实际 {path!r}")

            role = src.get("role")
            if role not in ROLE_VALUES:
                errs.append(f"sources[{i}].role 必须属于 {ROLE_VALUES}，实际 {role!r}")
            elif role == "leader":
                leader_count += 1

            branch = src.get("branch")
            if not isinstance(branch, str) or not branch.startswith(BRANCH_PREFIX):
                errs.append(f"sources[{i}].branch 必须以 {BRANCH_PREFIX} 开头，实际 {branch!r}")

            # depends_on optional
            deps = src.get("depends_on", [])
            if deps is not None and not isinstance(deps, list):
                errs.append(f"sources[{i}].depends_on 必须是 list 或缺省")
            elif isinstance(deps, list):
                for j, dep in enumerate(deps):
                    if not isinstance(dep, str) or not REPO_RE.match(dep):
                        errs.append(
                            f"sources[{i}].depends_on[{j}] 必须是 owner/repo，实际 {dep!r}"
                        )

        if leader_count != 1:
            errs.append(f"sources 必须正好有 1 个 role=leader，实际 {leader_count} 个")

    # integration（optional）
    integration = manifest.get("integration")
    if integration is not None:
        if not isinstance(integration, dict):
            errs.append("integration 必须是 object 或缺省")
        else:
            irepo = integration.get("repo")
            if not isinstance(irepo, str) or not REPO_RE.match(irepo):
                errs.append(f"integration.repo 必须是 owner/repo，实际 {irepo!r}")
            ipath = integration.get("path")
            if not isinstance(ipath, str) or not ipath.startswith("integration/"):
                errs.append(f"integration.path 必须以 integration/ 开头，实际 {ipath!r}")

    # 下面几项是后续 stage 填的，分析阶段不 required 但类型要对（若存在）
    for field_name, expected_type in (
        ("sha_by_repo", dict),
        ("pr_by_repo", dict),
        ("image_tags", dict),
        ("merge_order", list),
    ):
        val = manifest.get(field_name)
        if val is not None and not isinstance(val, expected_type):
            errs.append(
                f"{field_name} 存在时必须是 {expected_type.__name__}，实际 {type(val).__name__}"
            )

    # M14d: parallelism.dev 任务列表（可选；存在时做基础 shape 校验）
    parallelism = manifest.get("parallelism")
    if parallelism is not None:
        if not isinstance(parallelism, dict):
            errs.append("parallelism 必须是 object 或缺省")
        else:
            dev_tasks = parallelism.get("dev")
            if dev_tasks is not None:
                if not isinstance(dev_tasks, list):
                    errs.append("parallelism.dev 必须是 list 或缺省")
                else:
                    seen_ids: set[str] = set()
                    for i, t in enumerate(dev_tasks):
                        if not isinstance(t, dict):
                            errs.append(f"parallelism.dev[{i}] 必须是 object")
                            continue
                        tid = t.get("id")
                        if not isinstance(tid, str) or not tid:
                            errs.append(f"parallelism.dev[{i}].id 必须是非空 string")
                        elif tid in seen_ids:
                            errs.append(f"parallelism.dev 里 id {tid!r} 重复")
                        else:
                            seen_ids.add(tid)
                        scope = t.get("scope")
                        if not isinstance(scope, list) or not scope:
                            errs.append(
                                f"parallelism.dev[{i}].scope 必须是非空 list"
                            )
                        desc = t.get("description")
                        if not isinstance(desc, str) or not desc:
                            errs.append(
                                f"parallelism.dev[{i}].description 必填非空 string"
                            )
                        deps = t.get("depends_on", [])
                        if deps is not None and not isinstance(deps, list):
                            errs.append(
                                f"parallelism.dev[{i}].depends_on 必须是 list 或缺省"
                            )

    return errs


def validate_path(path: Path) -> list[str]:
    if not path.exists():
        return [f"manifest 文件不存在: {path}"]
    try:
        raw = path.read_text()
    except Exception as e:
        return [f"读取 manifest 失败: {e}"]
    try:
        manifest = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return [f"manifest YAML 解析失败: {e}"]
    if not isinstance(manifest, dict):
        return ["manifest 根必须是 object / dict"]
    return _collect_errors(manifest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate sisyphus workspace manifest.yaml")
    parser.add_argument(
        "path", nargs="?", default="/workspace/.sisyphus/manifest.yaml",
        help="manifest path（默认 /workspace/.sisyphus/manifest.yaml）",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="只返 exit code，不打印",
    )
    args = parser.parse_args(argv)

    errs = validate_path(Path(args.path))
    if errs:
        if not args.quiet:
            print(f"manifest 验证失败 ({len(errs)} 项):", file=sys.stderr)
            for e in errs:
                print(f"  - {e}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(f"manifest OK: {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
