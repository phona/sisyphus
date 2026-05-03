"""cross-repo env orchestration helpers (pure logic, no I/O).

`feat-cross-repo-env-orchestration` (PR #342) declares that each repository in
a multi-layer accept-env chain ships a `.sisyphus/env.yaml` manifest declaring
which fields it `emits`, which upstream repos it `needs`, what `inputs` to
expect from upstream emits, and how its `branches` map develop/release class
to actual branch names.

The orchestrator side splits cleanly into:

- pure logic (this module): manifest schema validation, topology resolution,
  workspace dir mapping, branch resolution
- runner-side I/O (create_accept.py): kubectl exec, git fetch, JSON parsing

Keeping the pure half here means the unit tests don't need a kubectl mock and
the create_accept refactor stays focused on translating runner I/O into the
shapes this module consumes.
"""
from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import yaml

# spec R1: needs entries match OWNER/REPO; allow letters/digits + . _ - on both sides
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHELL_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INPUT_REF_RE = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\.([A-Za-z0-9_]+)$")
_DEFAULT_BRANCHES: dict[str, str] = {"develop": "develop", "release": "release"}
_ALLOWED_TOP_KEYS: frozenset[str] = frozenset({"emits", "needs", "inputs", "branches"})


class ManifestError(ValueError):
    """raised by parse_manifest when schema validation fails."""


class TopologyError(ValueError):
    """raised by resolve_topology when the dependency graph contains a cycle."""


@dataclass(frozen=True)
class Manifest:
    """parsed `.sisyphus/env.yaml` contents.

    `inputs` maps env var name → (upstream repo full name, field name on that repo's emits).
    `branches` is always populated with develop/release defaults merged in.
    """

    emits: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    inputs: dict[str, tuple[str, str]] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=lambda: dict(_DEFAULT_BRANCHES))


@dataclass(frozen=True)
class BranchResolution:
    """outcome of resolve_branch.

    `branch` is None on failure; `reason` is one of
    `same_name` / `class_fallback` / `branch_resolution_failed`.
    """

    branch: str | None
    reason: str
    failed_class: str | None = None


def parse_manifest(text: str) -> Manifest:
    """parse + validate `.sisyphus/env.yaml`. Raises ManifestError on schema break."""
    raw: Any
    try:
        raw = yaml.safe_load(text) if text and text.strip() else {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifest is not valid YAML: {exc}") from exc

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ManifestError(f"manifest top level must be a mapping, got {type(raw).__name__}")

    extras = set(raw.keys()) - _ALLOWED_TOP_KEYS
    if extras:
        raise ManifestError(f"manifest has unknown top-level keys: {sorted(extras)}")

    emits = _coerce_string_list(raw.get("emits"), field_name="emits")
    needs_list = _coerce_string_list(raw.get("needs"), field_name="needs")
    for n in needs_list:
        if not _REPO_NAME_RE.match(n):
            raise ManifestError(f"needs entry {n!r} does not match OWNER/REPO pattern")

    branches = dict(_DEFAULT_BRANCHES)
    raw_branches = raw.get("branches")
    if raw_branches is not None:
        if not isinstance(raw_branches, dict):
            raise ManifestError("branches must be a mapping of class -> branch name")
        for k, v in raw_branches.items():
            if not isinstance(k, str) or not k.strip():
                raise ManifestError(f"branches key {k!r} must be a non-empty string")
            if not isinstance(v, str) or not v.strip():
                raise ManifestError(f"branches value for {k!r} must be a non-empty string")
            branches[k] = v

    inputs: dict[str, tuple[str, str]] = {}
    raw_inputs = raw.get("inputs")
    if raw_inputs is not None:
        if not isinstance(raw_inputs, dict):
            raise ManifestError("inputs must be a mapping of ENV_VAR -> 'OWNER/REPO.field'")
        needs_set = set(needs_list)
        for env_name, ref in raw_inputs.items():
            if not isinstance(env_name, str) or not _SHELL_VAR_RE.match(env_name):
                raise ManifestError(
                    f"inputs key {env_name!r} is not a valid shell variable name"
                )
            if not isinstance(ref, str):
                raise ManifestError(
                    f"inputs[{env_name!r}] must be a string of the form OWNER/REPO.field"
                )
            m = _INPUT_REF_RE.match(ref)
            if not m:
                raise ManifestError(
                    f"inputs[{env_name!r}]={ref!r} is not 'OWNER/REPO.field' shape"
                )
            repo, fld = m.group(1), m.group(2)
            if repo not in needs_set:
                raise ManifestError(
                    f"inputs[{env_name!r}] references {repo} which is not declared in needs"
                )
            inputs[env_name] = (repo, fld)

    return Manifest(
        emits=tuple(emits),
        needs=tuple(needs_list),
        inputs=inputs,
        branches=branches,
    )


def _coerce_string_list(raw: Any, *, field_name: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ManifestError(f"{field_name} must be a list, got {type(raw).__name__}")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ManifestError(f"{field_name} entry {item!r} must be a non-empty string")
        out.append(item.strip())
    return out


def resolve_topology(
    source_repo: str,
    manifest_loader: Callable[[str], Manifest | None],
) -> list[str]:
    """topo-sort the needs graph rooted at `source_repo`. Leaves first.

    `manifest_loader` is a callable returning the parsed Manifest for any repo
    full name, or None if no manifest exists (R2-S10 — leaf with no emits).

    Raises TopologyError if a cycle is detected. The error message names the
    repos forming the cycle, e.g. `A -> B -> A`.
    """
    # BFS gather all reachable repos + their adjacency (repo -> needs list)
    adj: dict[str, list[str]] = {}
    queue: deque[str] = deque([source_repo])
    while queue:
        repo = queue.popleft()
        if repo in adj:
            continue
        m = manifest_loader(repo)
        deps = list(m.needs) if m else []
        # preserve declaration order, dedup just in case
        seen: set[str] = set()
        ordered_deps: list[str] = []
        for d in deps:
            if d in seen:
                continue
            seen.add(d)
            ordered_deps.append(d)
        adj[repo] = ordered_deps
        for d in ordered_deps:
            if d not in adj:
                queue.append(d)

    # cycle detection via DFS with path stack
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[str] = []

    def _dfs(node: str, path: list[str]) -> None:
        if node in visited:
            return
        if node in visiting:
            i = path.index(node)
            cycle = " -> ".join([*path[i:], node])
            raise TopologyError(f"dependency cycle detected: {cycle}")
        visiting.add(node)
        path.append(node)
        for dep in adj.get(node, ()):
            _dfs(dep, path)
        path.pop()
        visiting.discard(node)
        visited.add(node)
        order.append(node)

    _dfs(source_repo, [])
    return order


def workspace_dir_map(repos: Iterable[str]) -> dict[str, str]:
    """`OWNER/REPO` -> directory basename under `/workspace/source/`.

    Distinct short names: keep the short form (sisyphus-clone-repos.sh default).
    Colliding short names: ALL conflicting entries switch to `<owner>__<repo>`.
    Single-repo input always keeps short form (R3-S13 backward compat).
    """
    repos_list = list(repos)
    short_counts: dict[str, int] = {}
    for r in repos_list:
        if "/" not in r:
            raise ValueError(f"repo {r!r} is not in OWNER/REPO form")
        _owner, short = r.split("/", 1)
        short_counts[short] = short_counts.get(short, 0) + 1

    out: dict[str, str] = {}
    for r in repos_list:
        owner, short = r.split("/", 1)
        if short_counts[short] > 1:
            out[r] = f"{owner}__{short}"
        else:
            out[r] = short
    return out


def infer_branch_class(source_branch: str, source_manifest: Manifest) -> str:
    """which class (`develop` / `release` / custom) does `source_branch` belong to.

    A branch matching one of `source_manifest.branches.values()` claims that
    class. Anything else (feature branches like `feat/REQ-x`) is treated as
    `develop`-class — that's the spec's default for collaborative impl REQs.
    """
    for cls, name in source_manifest.branches.items():
        if name == source_branch:
            return cls
    return "develop"


def resolve_branch(
    source_branch: str,
    source_manifest: Manifest,
    needs_repo: str,
    needs_manifest: Manifest,
    branch_exists: Callable[[str, str], bool],
) -> BranchResolution:
    """spec R6 4-step branch resolver.

    `branch_exists(repo, branch)` is the I/O hook (in production: `git
    ls-remote --heads`). Returning False from any check funnels into the
    fail-loud branch.
    """
    if branch_exists(needs_repo, source_branch):
        return BranchResolution(branch=source_branch, reason="same_name")

    cls = infer_branch_class(source_branch, source_manifest)
    candidate = needs_manifest.branches.get(cls)
    if candidate and branch_exists(needs_repo, candidate):
        return BranchResolution(branch=candidate, reason="class_fallback")

    return BranchResolution(
        branch=None,
        reason="branch_resolution_failed",
        failed_class=cls,
    )
