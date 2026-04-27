"""Render assertions for `accept.md.j2` thanatos M1 wiring (REQ-417).

Black-box: render the template with realistic context dicts and assert that
the rendered prompt contains the expected literal substrings for the two
branches (thanatos-on / thanatos-off). These are the same invariants the
spec-delta `specs/thanatos-accept-wire/spec.md` scenarios TM1-S3 / TM1-S4
spell out.
"""

from __future__ import annotations

import pytest

from orchestrator.prompts import render

_BASE_CTX = dict(
    req_id="REQ-417",
    endpoint="http://lab.accept-req-417.svc.cluster.local:8080",
    namespace="accept-req-417",
    source_issue_id="src-issue-1",
    project_id="nnvxh8wj",
    project_alias="nnvxh8wj",
)


def _render(**overrides: object) -> str:
    ctx = {**_BASE_CTX, **overrides}
    return render("accept.md.j2", **ctx)


def test_thanatos_pod_present_renders_thanatos_branch() -> None:
    """TM1-S3: with accept_env.thanatos_pod set, prompt contains thanatos invocation."""
    out = _render(
        accept_env={
            "endpoint": _BASE_CTX["endpoint"],
            "namespace": _BASE_CTX["namespace"],
            "thanatos_pod": "thanatos-abc-xyz",
        }
    )
    assert "python -m thanatos run-scenario" in out
    assert "kb_updates" in out
    assert "git push origin feat/" in out
    # thanatos pod name interpolated into the kubectl exec example
    assert "thanatos-abc-xyz" in out


def test_thanatos_pod_absent_omits_thanatos_pod_var() -> None:
    """No accept_env.thanatos_pod → THANATOS_POD variable not declared at the top."""
    out = _render(accept_env=None)
    # The conditional THANATOS_POD={{ ... }} line must be absent
    assert "THANATOS_POD=" not in out


def test_curl_fallback_block_always_rendered() -> None:
    """TM1-S4: curl fallback section must render in BOTH the thanatos-on and -off cases."""
    for accept_env in [
        None,
        {
            "endpoint": _BASE_CTX["endpoint"],
            "thanatos_pod": "thanatos-abc",
        },
    ]:
        out = _render(accept_env=accept_env)
        assert "curl -sf" in out, "legacy curl example must remain documented"
        # Section heading mentioning fallback so reviewers see both paths
        assert "fallback" in out.lower()


def test_routing_signal_documented() -> None:
    """Step 2 must explain the routing decision (thanatos vs curl) in plain language."""
    out = _render(
        accept_env={
            "endpoint": _BASE_CTX["endpoint"],
            "thanatos_pod": "thanatos-abc",
        }
    )
    # The two AND signals should both be named near the routing block
    assert ".thanatos/skill.yaml" in out
    assert "thanatos_pod" in out


def test_vacuous_pass_defense_documented() -> None:
    """Thanatos branch must call out the 0-scenario → fail defense (no silent pass)."""
    out = _render(
        accept_env={
            "endpoint": _BASE_CTX["endpoint"],
            "thanatos_pod": "thanatos-abc",
        }
    )
    assert "vacuous-pass" in out.lower() or "0 个 scenario" in out


@pytest.mark.parametrize(
    "missing_field",
    ["endpoint", "thanatos_pod"],
)
def test_curl_fallback_when_thanatos_signal_partial(missing_field: str) -> None:
    """Partial thanatos signals: prompt still emits curl fallback; thanatos branch only when complete."""
    accept_env = {
        "endpoint": _BASE_CTX["endpoint"],
        "thanatos_pod": "thanatos-abc",
    }
    accept_env.pop(missing_field, None)
    out = _render(accept_env=accept_env)
    assert "curl -sf" in out
