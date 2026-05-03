"""Unit tests for orchestrator.cross_repo_env (R1 / R2 / R3 / R6).

Pure logic — no kubectl / asyncpg / git mocks required.
"""
from __future__ import annotations

import pytest

from orchestrator.cross_repo_env import (
    Manifest,
    ManifestError,
    TopologyError,
    infer_branch_class,
    parse_manifest,
    resolve_branch,
    resolve_topology,
    workspace_dir_map,
)

# ─── R1: parse_manifest ───────────────────────────────────────────────────

def test_parse_manifest_full(  ):
    """OCRE-S1 / CREO-S1: emits + needs + inputs + branches all populated."""
    text = """
emits:
  - endpoint
  - namespace
needs:
  - ZonEaseTech/ttpos-server-go
inputs:
  BACKEND_ENDPOINT: "ZonEaseTech/ttpos-server-go.endpoint"
branches:
  develop: develop
"""
    m = parse_manifest(text)
    assert m.emits == ("endpoint", "namespace")
    assert m.needs == ("ZonEaseTech/ttpos-server-go",)
    assert m.inputs == {
        "BACKEND_ENDPOINT": ("ZonEaseTech/ttpos-server-go", "endpoint"),
    }
    # default release branch merged in
    assert m.branches == {"develop": "develop", "release": "release"}


def test_parse_manifest_emits_only():
    """OCRE / CREO-S5: emits-only manifest is valid (leaf provider)."""
    m = parse_manifest("emits: [endpoint]")
    assert m.emits == ("endpoint",)
    assert m.needs == ()
    assert m.inputs == {}
    assert m.branches == {"develop": "develop", "release": "release"}


def test_parse_manifest_empty_yaml():
    """absent / blank yaml parses to a fully-default Manifest."""
    m = parse_manifest("")
    assert m == Manifest()


def test_parse_manifest_inputs_undeclared_needs():
    """OCRE-S2 / CREO-S2: inputs reference repo not in needs -> reject."""
    text = """
inputs:
  FOO: "some-org/some-repo.field"
"""
    with pytest.raises(ManifestError, match="some-org/some-repo"):
        parse_manifest(text)


def test_parse_manifest_invalid_repo_name():
    """OCRE-S3 / CREO-S3: malformed needs entry rejected."""
    text = """
needs:
  - "not-a-valid/repo/name"
"""
    with pytest.raises(ManifestError, match="not-a-valid/repo/name"):
        parse_manifest(text)


def test_parse_manifest_invalid_env_var():
    """OCRE-S4 / CREO-S4: shell var name starting with digit rejected."""
    text = """
needs:
  - org/repo
inputs:
  "123INVALID": "org/repo.field"
"""
    with pytest.raises(ManifestError, match="123INVALID"):
        parse_manifest(text)


def test_parse_manifest_unknown_top_key():
    """unknown top-level keys surface fail-loud rather than silent ignore."""
    with pytest.raises(ManifestError, match="unknown top-level"):
        parse_manifest("foo: bar")


def test_parse_manifest_branches_partial_override():
    """user can override only develop; release default still merged in."""
    m = parse_manifest("branches:\n  develop: master")
    assert m.branches == {"develop": "master", "release": "release"}


def test_parse_manifest_yaml_error():
    with pytest.raises(ManifestError, match="not valid YAML"):
        parse_manifest("emits: [unclosed")


# ─── R2: resolve_topology ─────────────────────────────────────────────────

def _loader(graph: dict[str, list[str]]):
    """make a manifest_loader from a {repo: [needs...]} adjacency dict."""
    def _load(repo: str) -> Manifest | None:
        if repo not in graph:
            return None
        return Manifest(needs=tuple(graph[repo]))
    return _load


def test_resolve_topology_linear_chain():
    """OCRE-S5 / CREO-S6: A → B → C orders [C, B, A]."""
    graph = {"A": ["B"], "B": ["C"], "C": []}
    assert resolve_topology("A", _loader(graph)) == ["C", "B", "A"]


def test_resolve_topology_diamond_dedup():
    """OCRE-S6 / CREO-S7: D appears once, before B and C; A is last."""
    graph = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
    order = resolve_topology("A", _loader(graph))
    assert order.count("D") == 1
    assert order.index("D") < order.index("B")
    assert order.index("D") < order.index("C")
    assert order[-1] == "A"


def test_resolve_topology_cycle_detection():
    """OCRE-S6 / CREO-S8: cyclic graph raises TopologyError naming the cycle."""
    graph = {"A": ["B"], "B": ["A"]}
    with pytest.raises(TopologyError, match=r"A -> B -> A|B -> A -> B"):
        resolve_topology("A", _loader(graph))


def test_resolve_topology_source_no_manifest():
    """CREO-S9: source with no manifest yields single-element list."""
    assert resolve_topology("A", lambda r: None) == ["A"]


def test_resolve_topology_needs_repo_no_manifest():
    """CREO-S10: leaf needs repo with no manifest is included as no-emits leaf."""
    def _load(repo: str) -> Manifest | None:
        if repo == "A":
            return Manifest(needs=("B",))
        return None  # B has no manifest

    order = resolve_topology("A", _load)
    assert order == ["B", "A"]


# ─── R3: workspace_dir_map ────────────────────────────────────────────────

def test_workspace_dir_map_distinct_short_names():
    """OCRE-S7 / CREO-S11: distinct short names map to short basenames."""
    assert workspace_dir_map([
        "ZonEaseTech/ttpos-server-go",
        "ZonEaseTech/ttpos-flutter",
    ]) == {
        "ZonEaseTech/ttpos-server-go": "ttpos-server-go",
        "ZonEaseTech/ttpos-flutter": "ttpos-flutter",
    }


def test_workspace_dir_map_collision():
    """OCRE-S8 / CREO-S12: colliding short names switch to OWNER__REPO form."""
    assert workspace_dir_map(["org-a/shared-lib", "org-b/shared-lib"]) == {
        "org-a/shared-lib": "org-a__shared-lib",
        "org-b/shared-lib": "org-b__shared-lib",
    }


def test_workspace_dir_map_single_repo_short_name():
    """OCRE-S9 / CREO-S13: single-repo input keeps short basename."""
    assert workspace_dir_map(["phona/sisyphus"]) == {"phona/sisyphus": "sisyphus"}


def test_workspace_dir_map_partial_collision():
    """only colliding entries get OWNER__REPO; non-colliding stay short."""
    assert workspace_dir_map([
        "phona/sisyphus",
        "org-a/shared-lib",
        "org-b/shared-lib",
    ]) == {
        "phona/sisyphus": "sisyphus",
        "org-a/shared-lib": "org-a__shared-lib",
        "org-b/shared-lib": "org-b__shared-lib",
    }


def test_workspace_dir_map_invalid_repo():
    with pytest.raises(ValueError, match="OWNER/REPO"):
        workspace_dir_map(["just-a-name"])


# ─── R6: resolve_branch ───────────────────────────────────────────────────

def _exists_factory(branches: dict[str, set[str]]):
    """branch_exists callable backed by {repo: {branch1, branch2}} dict."""
    def _exists(repo: str, branch: str) -> bool:
        return branch in branches.get(repo, set())
    return _exists


def test_resolve_branch_same_name_priority():
    """OCRE-S10 / CREO-S21: same-name branch in needs repo wins immediately."""
    src_manifest = Manifest()
    needs_manifest = Manifest()
    exists = _exists_factory({"org/needs": {"feat/REQ-42-foo"}})
    res = resolve_branch(
        "feat/REQ-42-foo", src_manifest, "org/needs", needs_manifest, exists,
    )
    assert res.branch == "feat/REQ-42-foo"
    assert res.reason == "same_name"


def test_resolve_branch_class_fallback_default():
    """OCRE-S11 / CREO-S22: feature branch + default develop maps to needs.develop."""
    src_manifest = Manifest()
    needs_manifest = Manifest()  # default branches {develop: develop, release: release}
    exists = _exists_factory({"org/needs": {"develop"}})
    res = resolve_branch(
        "feat/REQ-42-foo", src_manifest, "org/needs", needs_manifest, exists,
    )
    assert res.branch == "develop"
    assert res.reason == "class_fallback"


def test_resolve_branch_class_fallback_custom_alias():
    """OCRE-S11 / CREO-S23: source main->develop class; needs develop=master."""
    src_manifest = Manifest(branches={"develop": "main", "release": "release"})
    needs_manifest = Manifest(branches={"develop": "master", "release": "stable"})
    exists = _exists_factory({"org/needs": {"master"}})
    res = resolve_branch("main", src_manifest, "org/needs", needs_manifest, exists)
    assert res.branch == "master"
    assert res.reason == "class_fallback"


def test_resolve_branch_fail_loud():
    """OCRE-S12 / CREO-S24: no same-name + no class branch -> failure resolution."""
    src_manifest = Manifest()
    needs_manifest = Manifest()
    exists = _exists_factory({"org/needs": set()})  # nothing exists
    res = resolve_branch(
        "feat/REQ-42-foo", src_manifest, "org/needs", needs_manifest, exists,
    )
    assert res.branch is None
    assert res.reason == "branch_resolution_failed"
    assert res.failed_class == "develop"


def test_resolve_branch_release_class():
    """source on `release` branch routes to needs.release alias."""
    src_manifest = Manifest()
    needs_manifest = Manifest(branches={"develop": "develop", "release": "stable"})
    exists = _exists_factory({"org/needs": {"stable"}})
    res = resolve_branch(
        "release", src_manifest, "org/needs", needs_manifest, exists,
    )
    assert res.branch == "stable"
    assert res.reason == "class_fallback"


def test_infer_branch_class_default_develop():
    """feature branch falls into develop class."""
    assert infer_branch_class("feat/REQ-42", Manifest()) == "develop"


def test_infer_branch_class_explicit_release():
    """source branch matching branches.release -> release class."""
    m = Manifest(branches={"develop": "develop", "release": "release"})
    assert infer_branch_class("release", m) == "release"


# ─── Integration: full end-to-end through the pure-logic helpers ─────────

def test_endtoend_topology_with_branch_resolution():
    """linear chain + branch resolution + dir map combine cleanly."""
    # source = ZonEaseTech/ttpos-flutter -> needs ZonEaseTech/ttpos-server-go
    manifests = {
        "ZonEaseTech/ttpos-flutter": Manifest(
            emits=("device",),
            needs=("ZonEaseTech/ttpos-server-go",),
            inputs={"BACKEND_ENDPOINT": ("ZonEaseTech/ttpos-server-go", "endpoint")},
        ),
        "ZonEaseTech/ttpos-server-go": Manifest(emits=("endpoint",)),
    }
    topo = resolve_topology(
        "ZonEaseTech/ttpos-flutter", lambda r: manifests.get(r),
    )
    assert topo == ["ZonEaseTech/ttpos-server-go", "ZonEaseTech/ttpos-flutter"]
    dir_map = workspace_dir_map(topo)
    assert dir_map == {
        "ZonEaseTech/ttpos-server-go": "ttpos-server-go",
        "ZonEaseTech/ttpos-flutter": "ttpos-flutter",
    }
    # branch on source repo doesn't exist anywhere; class fallback resolves
    exists = _exists_factory({"ZonEaseTech/ttpos-server-go": {"develop"}})
    res = resolve_branch(
        "feat/REQ-42",
        manifests["ZonEaseTech/ttpos-flutter"],
        "ZonEaseTech/ttpos-server-go",
        manifests["ZonEaseTech/ttpos-server-go"],
        exists,
    )
    assert res.branch == "develop"


def test_resolve_topology_self_cycle():
    """A repo that depends on itself raises TopologyError."""
    graph = {"A": ["A"]}
    with pytest.raises(TopologyError, match=r"A -> A"):
        resolve_topology("A", _loader(graph))
