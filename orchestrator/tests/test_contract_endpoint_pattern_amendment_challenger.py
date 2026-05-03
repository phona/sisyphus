"""Challenger contract tests for REQ-spec-pattern-contract-amendment-1777818469.

Black-box contracts derived **exclusively** from:

  openspec/changes/REQ-spec-pattern-contract-amendment-1777818469/specs/
    feat-cross-repo-env-orchestration/spec.md

Scenarios covered (all EPCA-* — the new amendment scenarios):

  EPCA-S1   pattern-form emit with literal + ${SISYPHUS_*} parses cleanly
  EPCA-S2   mixed bare-string + pattern emits in one list parses cleanly
  EPCA-S3   pattern referencing a placeholder absent from `vars` is rejected
  EPCA-S4   pattern-form value reaches consumers without parsing layer output
  EPCA-S5   pattern resolves to a fully-substituted string value
  EPCA-S6   resolved pattern value is byte-identical (opaque passthrough)
  EPCA-S7   pre-resolve assembles bundle from (manifests, req_context) alone
  EPCA-S8   unresolved ${SISYPHUS_*} reference fails loud with attribution
  EPCA-S9   pre-resolved bundle is observable before any layer runtime data
  EPCA-S10  manifest-fetch failure during pre-resolve is distinguishable

The proposal is spec-only (no impl in this REQ); the downstream impl REQ MUST
make these tests green.  Dev MUST NOT modify the tests to make them pass —
fix the implementation instead.  If a test is genuinely wrong, escalate to
spec_fixer; do not patch around it in code.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable

import pytest

from orchestrator.cross_repo_env import (
    Manifest,
    ManifestError,
    parse_manifest,
)


# ─── canonical entry points the spec mandates ────────────────────────────────

def _pre_resolve():
    """Return ``orchestrator.cross_repo_env.pre_resolve_endpoint_bundle`` or
    fail loud.

    R12 mandates a pre-resolve phase that, from manifests + REQ context alone,
    produces ``stage_runs.context.endpoint_bundle_pre_resolved``.  The
    canonical pure-logic seam for this is a function on
    ``orchestrator.cross_repo_env``.  A missing symbol is itself a contract
    violation so we surface it as a test failure — not a collection error.
    """
    from orchestrator import cross_repo_env

    fn = getattr(cross_repo_env, "pre_resolve_endpoint_bundle", None)
    assert callable(fn), (
        "Spec R12 mandates "
        "orchestrator.cross_repo_env.pre_resolve_endpoint_bundle to exist as "
        "the canonical pre-resolve seam.  It MUST take only (topology, "
        "manifest_loader, req_context) and return a partial bundle keyed by "
        "OWNER/REPO with each pattern-form emit resolved to a string."
    )
    return fn


def _pre_resolve_error():
    """Return ``orchestrator.cross_repo_env.PreResolveError`` or fail loud."""
    from orchestrator import cross_repo_env

    cls = getattr(cross_repo_env, "PreResolveError", None)
    assert isinstance(cls, type) and issubclass(cls, Exception), (
        "Spec R12 mandates a dedicated PreResolveError exception so the "
        "orchestrator can attribute pre-resolve failures distinctly from "
        "R10's runtime layer-attribution.  Expected at "
        "orchestrator.cross_repo_env.PreResolveError."
    )
    return cls


# ─── helpers ─────────────────────────────────────────────────────────────────

def _loader_from(graph: dict[str, str | None]) -> Callable[[str], Manifest | None]:
    """Build a manifest_loader from a {repo: yaml_text_or_None} dict.

    yaml_text == None means "no manifest present" (R8 allows this).  Use the
    sentinel ``"__FETCH_FAIL__"`` to simulate an upstream fetch error (the
    loader raises ``RuntimeError`` — the contract under test is that
    pre-resolve translates this into ``PreResolveError`` with attribution).
    """

    def _load(repo: str) -> Manifest | None:
        text = graph.get(repo, None)
        if text is None:
            return None
        if text == "__FETCH_FAIL__":
            raise RuntimeError(f"simulated manifest fetch HTTP 5xx for {repo}")
        return parse_manifest(text)

    return _load


_REQ_CTX = {
    "SISYPHUS_NAMESPACE": "req-7-foo",
    "SISYPHUS_REQ_ID": "REQ-7-foo",
    "SISYPHUS_REQ_BRANCH": "feat/REQ-7-foo",
    "SISYPHUS_SOURCE_REPO_SHA": "deadbeef",
}


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S1 — pattern-form emit with literal + ${SISYPHUS_*} parses
# ═══════════════════════════════════════════════════════════════════════════

def test_epca_s1_pattern_emit_with_literal_and_sisyphus_var_parses() -> None:
    """EPCA-S1: pattern-form emit with one literal + one ${SISYPHUS_*} placeholder
    MUST validate cleanly.  The validator MUST record the entry as a
    pattern-form emit with field name ``endpoint`` (observable downstream as a
    pre-resolved value in the bundle).
    """
    text = """
emits:
  - endpoint:
      pattern: "ttpos-server-go.{NAMESPACE}.svc.cluster.local:{PORT}"
      vars:
        NAMESPACE: "${SISYPHUS_NAMESPACE}"
        PORT: "8080"
"""
    # Parsing alone MUST not raise.
    m = parse_manifest(text)
    assert isinstance(m, Manifest)

    # The recorded distinction is observable through pre_resolve: the field
    # ``endpoint`` MUST appear in the bundle for this repo with the resolved
    # string value (proving it was treated as pattern-form, not bare-string).
    pre_resolve = _pre_resolve()
    bundle = pre_resolve(
        ["ZonEaseTech/ttpos-server-go"],
        _loader_from({"ZonEaseTech/ttpos-server-go": text}),
        _REQ_CTX,
    )
    repo_bundle = bundle["ZonEaseTech/ttpos-server-go"]
    assert (
        repo_bundle.get("endpoint")
        == "ttpos-server-go.req-7-foo.svc.cluster.local:8080"
    ), (
        "EPCA-S1: pattern-form emit MUST resolve via vars + ${SISYPHUS_*} "
        "expansion to a fully-substituted string; the bundle MUST surface the "
        "field under its declared name."
    )


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S2 — mixed bare-string + pattern emits in one list is valid
# ═══════════════════════════════════════════════════════════════════════════

def test_epca_s2_mixed_bare_and_pattern_emits_in_one_list() -> None:
    """EPCA-S2: a single ``emits`` list MAY mix bare-string and pattern entries.
    Validator records ``namespace`` as bare-string, ``endpoint`` as pattern.
    """
    text = """
emits:
  - namespace
  - endpoint:
      pattern: "svc.{NS}:8080"
      vars:
        NS: "${SISYPHUS_NAMESPACE}"
"""
    # Parsing succeeds.
    m = parse_manifest(text)
    assert isinstance(m, Manifest)

    # Behavioural distinction: pre-resolve MUST surface ONLY the pattern-form
    # entry (bare-string emits are filled in at layer runtime per R4).
    pre_resolve = _pre_resolve()
    bundle = pre_resolve(
        ["org/x"],
        _loader_from({"org/x": text}),
        _REQ_CTX,
    )
    repo_bundle = bundle.get("org/x", {})
    assert repo_bundle.get("endpoint") == "svc.req-7-foo:8080", (
        "EPCA-S2: pattern-form `endpoint` MUST be pre-resolved into the bundle."
    )
    assert "namespace" not in repo_bundle, (
        "EPCA-S2: bare-string emits MUST NOT appear in the pre-resolve bundle "
        "(they are filled in at layer runtime by R4)."
    )


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S3 — pattern referencing undeclared placeholder is rejected
# ═══════════════════════════════════════════════════════════════════════════

def test_epca_s3_pattern_with_undeclared_placeholder_is_rejected() -> None:
    """EPCA-S3: a pattern that references ``{PORT}`` while ``vars`` declares
    only ``NS`` MUST be rejected at validation time, naming ``PORT``.
    """
    text = """
emits:
  - endpoint:
      pattern: "svc.{NS}.local:{PORT}"
      vars:
        NS: "${SISYPHUS_NAMESPACE}"
"""
    with pytest.raises(ManifestError, match=r"\bPORT\b"):
        parse_manifest(text)


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S4 — pattern-form value injected without parsing layer output
# ═══════════════════════════════════════════════════════════════════════════

def test_epca_s4_pre_resolve_independent_of_layer_runtime_output() -> None:
    """EPCA-S4 (+ structural facet of EPCA-S9): pre-resolve MUST be a function
    of (topology, manifest_loader, req_context) **only**.  In particular it
    MUST NOT take any layer-runtime input (e.g. ``accept-env-up`` JSON, layer
    completion status, pod readiness).  This is what lets APK build receive
    BACKEND_ENDPOINT in parallel with — not after — backend `accept-env-up`.
    """
    pre_resolve = _pre_resolve()
    sig = inspect.signature(pre_resolve)
    param_names = [p.name for p in sig.parameters.values()]

    # Strict: only these three parameters (in any order) are allowed.
    forbidden_substrings = (
        "output",       # accept-env-up json output
        "json",
        "result",
        "runtime",
        "ready",
        "endpoint_bundle",  # already-runtime bundle
    )
    for p in param_names:
        for bad in forbidden_substrings:
            assert bad not in p.lower(), (
                f"EPCA-S4 / EPCA-S9: pre_resolve_endpoint_bundle MUST NOT "
                f"depend on layer-runtime data; offending param: {p!r}.  "
                f"Allowed inputs: topology, manifest_loader, req_context."
            )


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S5 — pattern resolves to a string value
# ═══════════════════════════════════════════════════════════════════════════

def test_epca_s5_pattern_resolves_to_string_value() -> None:
    """EPCA-S5: pattern + literal + ${SISYPHUS_NAMESPACE} resolves to a JSON
    string with placeholders fully substituted.
    """
    text = """
emits:
  - endpoint:
      pattern: "ttpos-server-go.{NS}.svc.cluster.local:{PORT}"
      vars:
        NS: "${SISYPHUS_NAMESPACE}"
        PORT: "8080"
"""
    pre_resolve = _pre_resolve()
    bundle = pre_resolve(
        ["ZonEaseTech/ttpos-server-go"],
        _loader_from({"ZonEaseTech/ttpos-server-go": text}),
        _REQ_CTX,  # SISYPHUS_NAMESPACE=req-7-foo
    )
    value = bundle["ZonEaseTech/ttpos-server-go"]["endpoint"]
    assert value == "ttpos-server-go.req-7-foo.svc.cluster.local:8080"
    # Spec mandates the resolved value is a (JSON) string.
    assert isinstance(value, str)


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S6 — resolved pattern value is opaque (no reformatting)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "pattern, vars_map, ctx, expected",
    [
        # ADB-style host:port (must NOT be URL-prefixed or stripped to host)
        (
            "redroid.{NS}.svc.cluster.local:5554",
            {"NS": "${SISYPHUS_NAMESPACE}"},
            {"SISYPHUS_NAMESPACE": "req-7-abc"},
            "redroid.req-7-abc.svc.cluster.local:5554",
        ),
        # HTTP URL form (must pass through byte-identical)
        (
            "http://{HOST}:{PORT}/api",
            {"HOST": "${SISYPHUS_NAMESPACE}.svc", "PORT": "8080"},
            {"SISYPHUS_NAMESPACE": "ns-x"},
            "http://ns-x.svc:8080/api",
        ),
        # Full literal — no placeholders
        (
            "literal-only-value",
            {},
            {"SISYPHUS_NAMESPACE": "ignored"},
            "literal-only-value",
        ),
    ],
)
def test_epca_s6_resolved_value_passthrough_byte_identical(
    pattern: str, vars_map: dict[str, str], ctx: dict[str, str], expected: str
) -> None:
    """EPCA-S6: post-resolution, the value MUST pass through byte-identical —
    no URL prefixing, no port stripping, no host extraction.
    """
    vars_yaml = "\n".join(f'        {k}: "{v}"' for k, v in vars_map.items())
    text = f"""
emits:
  - endpoint:
      pattern: "{pattern}"
      vars:
{vars_yaml or "        DUMMY_KEY: \"_unused_\""}
"""
    # If pattern has no placeholders, vars may be empty.  Some YAML linters
    # dislike an empty mapping, so synthesise a harmless key when needed.
    if not vars_map:
        text = f"""
emits:
  - endpoint:
      pattern: "{pattern}"
      vars: {{}}
"""

    full_ctx = dict(_REQ_CTX)
    full_ctx.update(ctx)

    pre_resolve = _pre_resolve()
    bundle = pre_resolve(
        ["org/x"],
        _loader_from({"org/x": text}),
        full_ctx,
    )
    assert bundle["org/x"]["endpoint"] == expected


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S7 — pre-resolve assembles bundle keyed by OWNER/REPO
# ═══════════════════════════════════════════════════════════════════════════

def test_epca_s7_pre_resolve_bundle_shape_keyed_by_owner_repo() -> None:
    """EPCA-S7: pre-resolve produces ``dict[OWNER/REPO, dict[field, str]]`` —
    the same shape persisted to ``stage_runs.context.endpoint_bundle_pre_resolved``.
    """
    text_be = """
emits:
  - endpoint:
      pattern: "ttpos-server-go.{NS}.svc.cluster.local:{PORT}"
      vars:
        NS: "${SISYPHUS_NAMESPACE}"
        PORT: "8080"
"""
    text_fe = """
needs:
  - ZonEaseTech/ttpos-server-go
inputs:
  BACKEND_ENDPOINT: "ZonEaseTech/ttpos-server-go.endpoint"
"""
    pre_resolve = _pre_resolve()
    bundle = pre_resolve(
        ["ZonEaseTech/ttpos-server-go", "ZonEaseTech/ttpos-flutter"],
        _loader_from(
            {
                "ZonEaseTech/ttpos-server-go": text_be,
                "ZonEaseTech/ttpos-flutter": text_fe,
            }
        ),
        {"SISYPHUS_NAMESPACE": "req-7-foo"},
    )
    assert bundle == {
        "ZonEaseTech/ttpos-server-go": {
            "endpoint": "ttpos-server-go.req-7-foo.svc.cluster.local:8080",
        },
        # flutter has no pattern-form emits → no entry (or empty entry — both
        # acceptable; the spec only requires the *server-go* shape).
        **(
            {"ZonEaseTech/ttpos-flutter": {}}
            if "ZonEaseTech/ttpos-flutter" in bundle
            else {}
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S8 — unresolved ${SISYPHUS_*} reference fails loud with attribution
# ═══════════════════════════════════════════════════════════════════════════

def test_epca_s8_unresolved_sisyphus_var_raises_pre_resolve_error() -> None:
    """EPCA-S8: a pattern referencing ``${SISYPHUS_NONEXISTENT_VAR}`` (not in
    the REQ context allow-list) MUST cause pre-resolve to escalate **before**
    the runner pod is created.  Concretely: the pure pre-resolve function
    raises ``PreResolveError`` with attribution to:

      - ``failed_phase == "pre_resolve"``
      - ``failed_layer == "<offending OWNER/REPO>"``
      - error message names the unresolved variable
    """
    text = """
emits:
  - endpoint:
      pattern: "svc.{NS}:8080"
      vars:
        NS: "${SISYPHUS_NONEXISTENT_VAR}"
"""
    pre_resolve = _pre_resolve()
    PreResolveError = _pre_resolve_error()

    with pytest.raises(PreResolveError) as excinfo:
        pre_resolve(
            ["org/x"],
            _loader_from({"org/x": text}),
            {"SISYPHUS_NAMESPACE": "req-7-foo"},  # nonexistent var NOT here
        )

    err = excinfo.value
    assert getattr(err, "failed_phase", None) == "pre_resolve", (
        "EPCA-S8: PreResolveError MUST set failed_phase='pre_resolve'."
    )
    assert getattr(err, "failed_layer", None) == "org/x", (
        "EPCA-S8: PreResolveError MUST identify the offending repo via "
        "failed_layer='OWNER/REPO'."
    )
    assert "SISYPHUS_NONEXISTENT_VAR" in str(err), (
        "EPCA-S8: error message MUST name the unresolved ${SISYPHUS_*} "
        "reference so operators can fix the manifest or REQ-context allow-list."
    )


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S9 — bundle observable before any layer's accept-env-up runs
# ═══════════════════════════════════════════════════════════════════════════

def test_epca_s9_pre_resolve_observable_before_any_layer_runtime() -> None:
    """EPCA-S9: pre-resolve MUST be callable using only manifests + req_context
    (no layer started, no docker-compose up, no kubectl apply).  This is the
    contract that lets APK-build dispatch run in parallel with the upstream
    layer's `accept-env-up`.

    Concretely: invoking pre-resolve in a hermetic test (no network, no DB,
    no pod) MUST succeed and yield the bundle.  Combined with EPCA-S4's
    structural assertion, this proves no runtime data is implicit.
    """
    text = """
emits:
  - endpoint:
      pattern: "ttpos-server-go.{NS}.svc.cluster.local:{PORT}"
      vars:
        NS: "${SISYPHUS_NAMESPACE}"
        PORT: "8080"
"""
    pre_resolve = _pre_resolve()
    # We deliberately pass an in-memory loader; if pre-resolve secretly needs
    # accept-env-up output, kubectl, asyncpg, or HTTP, this hermetic call
    # would fail.  Success here is the contract.
    bundle = pre_resolve(
        ["ZonEaseTech/ttpos-server-go"],
        _loader_from({"ZonEaseTech/ttpos-server-go": text}),
        {"SISYPHUS_NAMESPACE": "req-7-foo"},
    )
    # Bundle shape: APK-build dispatch will read this exact key path.
    assert (
        bundle["ZonEaseTech/ttpos-server-go"]["endpoint"]
        == "ttpos-server-go.req-7-foo.svc.cluster.local:8080"
    )


# ═══════════════════════════════════════════════════════════════════════════
# EPCA-S10 — manifest fetch failure during pre-resolve fails loud, distinct
# ═══════════════════════════════════════════════════════════════════════════

def test_epca_s10_manifest_fetch_failure_raises_distinguishable_error() -> None:
    """EPCA-S10: a fetch failure (loader raises) MUST become a
    ``PreResolveError`` whose attribution distinguishes it from a placeholder
    failure:

      - ``failed_phase == "pre_resolve"``
      - ``failed_layer == "org/some-repo"``
      - the error message MUST mention the manifest fetch (so it is not
        confused with EPCA-S8's placeholder-resolution failure)
    """
    pre_resolve = _pre_resolve()
    PreResolveError = _pre_resolve_error()

    loader = _loader_from({"org/some-repo": "__FETCH_FAIL__"})

    with pytest.raises(PreResolveError) as excinfo:
        pre_resolve(["org/some-repo"], loader, _REQ_CTX)

    err = excinfo.value
    assert getattr(err, "failed_phase", None) == "pre_resolve", (
        "EPCA-S10: failed_phase MUST be 'pre_resolve'."
    )
    assert getattr(err, "failed_layer", None) == "org/some-repo", (
        "EPCA-S10: failed_layer MUST identify the repo whose manifest fetch "
        "failed."
    )
    msg = str(err).lower()
    assert any(token in msg for token in ("fetch", "manifest")), (
        "EPCA-S10: error MUST distinguish manifest-fetch failure from "
        "placeholder-resolution failure (mention 'fetch' or 'manifest')."
    )
