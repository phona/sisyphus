"""REQ-415 (thanatos M1): accept.md.j2 has two branches gated on
`thanatos_pod`. When set, the rendered prompt instructs the accept-agent to
drive scenarios via thanatos MCP (`kubectl exec ... python -m thanatos.server`,
`tools/call run_all`, apply kb_updates, git push). When unset, the prompt keeps
the legacy SDA-S9 direct-curl behaviour. Both branches keep the result:* tag +
statusId=review hand-off so the engine's verifier flow is unchanged.

Coverage: TMW-S3 / TMW-S4 / TMW-S5 (specs/thanatos-mcp-wire/spec.md).
"""
from __future__ import annotations

from orchestrator.prompts import render


def _render(thanatos_pod, thanatos_namespace=None, thanatos_skill_repo=None) -> str:
    return render(
        "accept.md.j2",
        req_id="REQ-415",
        endpoint="http://lab.svc:8080",
        namespace="accept-req-415",
        source_issue_id="iss-1",
        accept_env={"endpoint": "http://lab.svc:8080"},
        project_id="nnvxh8wj",
        project_alias="nnvxh8wj",
        thanatos_pod=thanatos_pod,
        thanatos_namespace=thanatos_namespace,
        thanatos_skill_repo=thanatos_skill_repo,
    )


# ─── TMW-S3: thanatos_pod set → MCP exec instructions present ─────────────


def test_thanatos_branch_contains_mcp_exec_command():
    text = _render(
        thanatos_pod="thanatos-abc",
        thanatos_namespace="accept-req-415",
        thanatos_skill_repo="ttpos-flutter",
    )
    assert (
        "kubectl -n accept-req-415 exec -i thanatos-abc -- python -m thanatos.server"
        in text
    ), "thanatos branch must show kubectl exec into thanatos pod (TMW-S3)"
    assert "tools/call" in text, "thanatos branch must mention MCP tools/call"
    assert "run_all" in text, "thanatos branch must invoke run_all tool"
    assert "git push origin feat/REQ-415" in text, (
        "thanatos branch must instruct kb_updates push to feat branch (TMW-S3)"
    )


def test_thanatos_branch_uses_skill_repo_path():
    text = _render(
        thanatos_pod="thanatos-abc",
        thanatos_namespace="accept-req-415",
        thanatos_skill_repo="ttpos-flutter",
    )
    # skill_path must point inside the source repo basename the JSON declared
    assert (
        "/workspace/source/ttpos-flutter/.thanatos/skill.yaml" in text
    ), "skill_path must use thanatos_skill_repo basename"


# ─── TMW-S4: thanatos_pod unset → legacy fallback branch ──────────────────


def test_fallback_branch_keeps_legacy_glob_and_omits_mcp():
    text = _render(thanatos_pod=None)
    assert (
        "/workspace/source/*/openspec/changes/REQ-415/specs/*/spec.md" in text
    ), "fallback must glob spec.md across /workspace/source/* (TMW-S4)"
    assert (
        "python -m thanatos.server" not in text
    ), "fallback branch must not invoke thanatos.server (TMW-S4)"


def test_fallback_branch_keeps_curl_endpoint():
    text = _render(thanatos_pod=None)
    # the endpoint URL is rendered in the fallback step examples
    assert "http://lab.svc:8080" in text


# ─── TMW-S5: both branches keep the result: tag + statusId=review hand-off ─


def test_both_branches_emit_result_tag_and_review_status():
    for pod in ("thanatos-abc", None):
        text = _render(
            thanatos_pod=pod,
            thanatos_namespace="accept-req-415" if pod else None,
            thanatos_skill_repo="ttpos-flutter" if pod else None,
        )
        assert "result:pass" in text, f"missing result:pass when pod={pod!r}"
        assert "result:fail" in text, f"missing result:fail when pod={pod!r}"
        assert "statusId" in text and "review" in text, (
            f"missing statusId=review when pod={pod!r}"
        )


# ─── Negative: empty-string pod is treated as "unset" (template guards on
#     truthiness, not just None) so accept-env-up emitting `{"thanatos":
#     {"pod": ""}}` doesn't accidentally render the MCP branch with an
#     invalid pod name. ────────────────────────────────────────────────────


def test_empty_string_pod_treated_as_unset():
    text = _render(thanatos_pod="")
    assert "python -m thanatos.server" not in text, (
        "empty thanatos_pod must trigger fallback branch"
    )
    assert "/workspace/source/*/openspec/changes/REQ-415/specs/*/spec.md" in text
