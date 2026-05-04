"""Challenger contract tests for REQ-fix-clone-per-repo-base-1777808457.

Black-box contracts derived exclusively from:
  openspec/changes/REQ-fix-clone-per-repo-base-1777808457/specs/
    server-side-clone-and-no-env-fallback/spec.md

Scenarios covered:
  CBOR-S1  helm-style ``<owner>/<repo>`` key normalized to basename at clone
           time (script gets ``--base-for sisyphus main``; req_state stores
           ``{"sisyphus": "main"}``).
  CBOR-S2  basename-keyed override remains supported (backward compat).
  CBOR-S3  mixed key forms (basename + owner/repo) collapse to one
           basename-keyed entry per repo.

Plus the explicitly stated lower-level contract from the Requirement body:

  - ``orchestrator.router.normalize_base_overrides(d)`` MUST exist and return
    a NEW dict whose keys are the basename of each input key.
  - The basename rule strips ``<owner>/`` prefix AND any trailing ``.git``.
  - ``actions/start_execute.py`` and
    ``actions/start_execute_with_finalized_intent.py`` MUST call it after
    merging ``settings.default_base_branches`` into the per-REQ
    ``base_overrides``, before forwarding to
    ``clone_involved_repos_into_runner``.

Dev MUST NOT modify these tests to make them pass — fix the implementation
instead. If a test is genuinely wrong, escalate to spec_fixer to correct the
spec, not the test.
"""
from __future__ import annotations

import inspect

# ─── Black-box entry point ────────────────────────────────────────────────────


def _normalize():
    """Return ``orchestrator.router.normalize_base_overrides`` or fail loud.

    The spec mandates this exact symbol — a missing import is itself a contract
    violation, so we surface it as a test failure rather than a collection
    error.
    """
    from orchestrator import router

    fn = getattr(router, "normalize_base_overrides", None)
    assert callable(fn), (
        "Spec mandates orchestrator.router.normalize_base_overrides(d) "
        "to exist and be callable. The symbol is the canonical normalization "
        "entry point shared by start_execute and "
        "start_execute_with_finalized_intent."
    )
    return fn


# ─── CBOR-S1 ──────────────────────────────────────────────────────────────────


def test_cbor_s1_owner_repo_key_normalized_to_basename() -> None:
    """CBOR-S1: helm-style ``<owner>/<repo>`` key MUST collapse to basename.

    Spec: GIVEN ``settings.default_base_branches = {"phona/sisyphus": "main"}``,
    THEN the dict forwarded to the clone helper MUST be keyed by basename
    (``"sisyphus"``), so the runner-pod command contains
    ``--base-for sisyphus main`` (NOT ``--base-for phona/sisyphus main``).
    """
    normalize = _normalize()

    out = normalize({"phona/sisyphus": "main"})

    assert out == {"sisyphus": "main"}, (
        f"CBOR-S1: helm key 'phona/sisyphus' MUST normalize to basename "
        f"'sisyphus'; got {out!r}. The script's _resolve_base() looks up by "
        f"basename, so leaving the slash form in place silently drops the "
        f"override and falls back to the global --base develop."
    )


# ─── CBOR-S2 ──────────────────────────────────────────────────────────────────


def test_cbor_s2_basename_form_passes_through_unchanged() -> None:
    """CBOR-S2: pre-#345 basename-keyed shape MUST keep working (backward compat).

    Spec: GIVEN ``settings.default_base_branches = {"sisyphus": "main"}``,
    THEN the resulting dict MUST still be ``{"sisyphus": "main"}``. Operators
    who already write the basename form must not regress.
    """
    normalize = _normalize()

    out = normalize({"sisyphus": "main"})

    assert out == {"sisyphus": "main"}, (
        f"CBOR-S2: basename-form input must be idempotent under "
        f"normalize_base_overrides; got {out!r}."
    )


# ─── CBOR-S3 ──────────────────────────────────────────────────────────────────


def test_cbor_s3_mixed_key_forms_collapse_to_single_basename_entries() -> None:
    """CBOR-S3: basename + owner/repo inputs MUST yield one basename entry each.

    Spec: GIVEN ``extract_base_branches`` returns
    ``{"ttpos-flutter": "feat/hwt"}`` and
    ``settings.default_base_branches = {"phona/ttpos-server-go": "release"}``,
    THEN the merged + normalized result MUST be EXACTLY
    ``{"ttpos-flutter": "feat/hwt", "ttpos-server-go": "release"}``.
    """
    normalize = _normalize()

    out = normalize(
        {
            "ttpos-flutter": "feat/hwt",
            "phona/ttpos-server-go": "release",
        }
    )

    assert out == {
        "ttpos-flutter": "feat/hwt",
        "ttpos-server-go": "release",
    }, (
        f"CBOR-S3: mixed key forms must collapse to basename-keyed dict; "
        f"got {out!r}. Both --base-for ttpos-flutter feat/hwt and "
        f"--base-for ttpos-server-go release must reach the runner pod."
    )


# ─── Lower-level contract from the Requirement body ─────────────────────────


def test_cbor_dot_git_suffix_stripped_when_normalizing() -> None:
    """Requirement body: basename = last ``/``-segment with trailing ``.git`` stripped.

    A helm operator who writes ``phona/sisyphus.git`` (a habit copied from
    git remote URLs) MUST still get the same canonical key as the rest of
    the system.
    """
    normalize = _normalize()

    out = normalize({"phona/sisyphus.git": "main"})

    assert out == {"sisyphus": "main"}, (
        f"Trailing '.git' MUST be stripped during normalization; got {out!r}. "
        f"Otherwise the key 'sisyphus.git' never matches the script's "
        f"basename-keyed REPO_BASE_MAP and the override is silently lost."
    )


def test_cbor_normalize_returns_new_dict_does_not_mutate_input() -> None:
    """Spec: ``return a NEW dict whose keys are the basename of each input``.

    Callers in ``start_execute`` rely on the input dict (often built from
    ``settings.default_base_branches`` then merged with BKD-tag-derived
    overrides) staying intact. Mutation would be an aliasing landmine.
    """
    normalize = _normalize()
    original = {"phona/sisyphus": "main"}
    snapshot = dict(original)

    out = normalize(original)

    assert original == snapshot, (
        f"normalize_base_overrides MUST NOT mutate its input; "
        f"original was {snapshot!r}, became {original!r} after the call."
    )
    assert out is not original, (
        "normalize_base_overrides MUST return a new dict, not the same "
        "object — callers rely on that."
    )


def test_cbor_empty_input_returns_empty_dict() -> None:
    """Empty input must produce empty output (no NoneType / KeyError surprises).

    Plenty of REQs ship with zero per-repo overrides; the no-override path
    must stay the cheap, total fast-path.
    """
    normalize = _normalize()

    assert normalize({}) == {}


# ─── Wiring: BOTH start_execute call sites MUST invoke normalize ─────────────
#
# The Requirement body names two call sites by exact module path:
#   - orchestrator/src/orchestrator/actions/start_execute.py
#   - orchestrator/src/orchestrator/actions/start_execute_with_finalized_intent.py
#
# A unit-level test of ``normalize_base_overrides`` alone would not catch a
# regression where a future refactor drops the call from one of these modules
# and passes ``settings.default_base_branches`` straight through. Source
# inspection is the cheapest reliable check that does not require booting the
# full async start_execute pipeline (DB / BKD / k8s / dispatch_slugs mocks).


def test_cbor_start_analyze_module_invokes_normalize_base_overrides() -> None:
    """start_execute.py MUST reference ``normalize_base_overrides``.

    Spec: ``actions/start_execute.py ... MUST call it after merging
    settings.default_base_branches ... before forwarding to
    clone_involved_repos_into_runner``. If the symbol is not even mentioned
    in the module source, the wiring is broken — operators with helm-style
    keys will silently lose their per-repo overrides.
    """
    from orchestrator.actions import start_execute as sa

    src = inspect.getsource(sa)
    assert "normalize_base_overrides" in src, (
        "CBOR wiring: start_execute.py MUST reference normalize_base_overrides "
        "(the spec names this exact module path as a required call site). "
        "If you renamed the helper, update the spec — do NOT silently drop "
        "the normalization step."
    )


def test_cbor_start_analyze_with_finalized_intent_module_invokes_normalize_base_overrides() -> None:
    """start_execute_with_finalized_intent.py MUST reference ``normalize_base_overrides``.

    Spec calls out this exact module by name as the second required call
    site. The intake → analyze and the direct-analyze paths MUST share the
    same canonical override shape — otherwise helm configs work on one path
    and silently fail on the other.
    """
    from orchestrator.actions import start_execute_with_finalized_intent as safi

    src = inspect.getsource(safi)
    assert "normalize_base_overrides" in src, (
        "CBOR wiring: start_execute_with_finalized_intent.py MUST reference "
        "normalize_base_overrides (spec names this module by exact path). "
        "Skipping it on the finalized-intent path would silently drop "
        "helm-style overrides for direct-analyze REQs."
    )
